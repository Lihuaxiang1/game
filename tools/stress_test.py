"""本机真实 TCP 多房间压力与快照一致性实验。"""

from __future__ import annotations

import argparse
import queue
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from arena.protocol import encode, message, read_message
from arena.server import ArenaServer


class Bot:
    def __init__(self, username: str, port: int):
        self.username = username
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.socket.settimeout(None)
        self.stream = self.socket.makefile("rb")
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.latest_snapshot: dict[str, Any] | None = None
        self.snapshots: dict[int, dict[str, Any]] = {}
        self.snapshot_count = 0
        self._condition = threading.Condition()
        self._send_lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True, name=f"bot-{username}").start()

    def _reader(self) -> None:
        try:
            while self.alive:
                incoming = read_message(self.stream)
                if incoming is None:
                    break
                if incoming["type"] == "snapshot":
                    data = incoming["data"]
                    with self._condition:
                        self.latest_snapshot = data
                        self.snapshots[data["tick"]] = data
                        if len(self.snapshots) > 500:
                            del self.snapshots[min(self.snapshots)]
                        self.snapshot_count += 1
                        self._condition.notify_all()
                else:
                    self.events.put(incoming)
        except OSError:
            pass

    def send(self, kind: str, **data: Any) -> None:
        with self._send_lock:
            self.socket.sendall(encode(message(kind, **data)))

    def wait_event(
        self,
        kind: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        predicate = predicate or (lambda _data: True)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.username} waiting for {kind}")
            incoming = self.events.get(timeout=remaining)
            if incoming["type"] == kind and predicate(incoming["data"]):
                return incoming["data"]

    def wait_snapshot(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 10
    ) -> dict[str, Any]:
        with self._condition:
            if not self._condition.wait_for(
                lambda: self.latest_snapshot is not None and predicate(self.latest_snapshot),
                timeout,
            ):
                raise TimeoutError(f"{self.username} waiting for snapshot")
            assert self.latest_snapshot is not None
            return self.latest_snapshot

    def close(self) -> None:
        self.alive = False
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.stream.close()
        except OSError:
            pass
        self.socket.close()


def run_experiment(room_count: int, duration: float) -> None:
    bots: list[Bot] = []
    room_players: dict[int, list[Bot]] = {}
    with tempfile.TemporaryDirectory() as temp_dir:
        server = ArenaServer("127.0.0.1", 0, Path(temp_dir) / "stress.db")
        server.start()
        try:
            for room_index in range(room_count):
                size = (2, 3, 5)[room_index % 3]
                players = [Bot(f"r{room_index}p{index}", server.port) for index in range(size)]
                bots.extend(players)
                room_players[room_index] = players
                for bot in players:
                    bot.send("register", username=bot.username, password="stress123")
                    auth = bot.wait_event("auth_ok")
                    if auth["username"] != bot.username:
                        raise AssertionError("authentication mismatch")
                owner = players[0]
                owner.send("create_room", name=f"stress-{room_index}")
                room_id = owner.wait_event("joined")["room_id"]
                for bot in players[1:]:
                    bot.send("join_room", room_id=room_id)
                    joined = bot.wait_event("joined")
                    if joined["spectator"]:
                        raise AssertionError("player was unexpectedly made spectator")
                for bot in players:
                    bot.send("ready", ready=True)
                # ready 不再自动开局。等房主收到“全员已准备”的房间状态后显式开始。
                owner.wait_event(
                    "room_state",
                    lambda data: all(
                        player.get("ready")
                        for player in data.get("room", {}).get("player_list", [])
                    ),
                )
                owner.send("start_room")
                for bot in players:
                    bot.wait_snapshot(lambda data: data.get("phase") == "playing")

            started = time.monotonic()
            sequence = 0
            weapons = ("pistol", "rifle", "shotgun", "sniper")
            while time.monotonic() - started < duration:
                sequence += 1
                for room_index, players in room_players.items():
                    for player_index, bot in enumerate(players):
                        angle = (sequence * 0.07 + player_index) % 6.283185307179586
                        bot.send(
                            "input",
                            seq=sequence,
                            move_x=1 if sequence % 90 < 45 else -1,
                            move_y=0.4 if player_index % 2 else -0.4,
                            angle=angle,
                            shoot=True,
                            weapon=weapons[(sequence // 30 + player_index) % 4],
                        )
                time.sleep(1 / 30)

            common_tick_counts = []
            for room_index, players in room_players.items():
                common = set(players[0].snapshots)
                for bot in players[1:]:
                    common.intersection_update(bot.snapshots)
                common = {tick for tick in common if tick > 0}
                if len(common) < 10:
                    raise AssertionError(f"room {room_index}: only {len(common)} common ticks")
                for tick in common:
                    reference = players[0].snapshots[tick]
                    for bot in players[1:]:
                        if bot.snapshots[tick] != reference:
                            raise AssertionError(
                                f"room {room_index}, tick {tick}: divergent snapshot at {bot.username}"
                            )
                for bot in players:
                    assert bot.latest_snapshot is not None
                    own = next(p for p in bot.latest_snapshot["players"] if p["username"] == bot.username)
                    if own["ack_seq"] <= 0:
                        raise AssertionError(f"{bot.username}: server did not acknowledge input")
                common_tick_counts.append(len(common))
                print(
                    f"room={room_index} players={len(players)} common_ticks={len(common)} "
                    f"snapshots={sum(bot.snapshot_count for bot in players)} consistent=yes"
                )
            print(
                f"PASS rooms={room_count} clients={len(bots)} duration={duration:.1f}s "
                f"inputs_per_client={sequence} min_common_ticks={min(common_tick_counts)}"
            )
        finally:
            for bot in bots:
                bot.close()
            server.stop()
            server.database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rooms", type=int, default=3, help="并发房间数，人数按 2/3/5 循环")
    parser.add_argument("--duration", type=float, default=3.0, help="战斗输入持续时间（秒）")
    args = parser.parse_args()
    if not 1 <= args.rooms <= 20 or not 0.5 <= args.duration <= 60:
        parser.error("rooms 应为 1～20，duration 应为 0.5～60")
    run_experiment(args.rooms, args.duration)


if __name__ == "__main__":
    main()
