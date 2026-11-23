from __future__ import annotations

import socket
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable

from arena.protocol import message, read_message, encode
from arena.server import ArenaServer


class WireClient:
    def __init__(self, port: int):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.socket.settimeout(5)
        self.stream = self.socket.makefile("rb")
        self.inbox: list[dict[str, Any]] = []

    def send(self, kind: str, **data: Any) -> None:
        self.socket.sendall(encode(message(kind, **data)))

    def until(
        self,
        kind: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float = 6,
    ) -> dict[str, Any]:
        predicate = predicate or (lambda _data: True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, incoming in enumerate(self.inbox):
                if incoming["type"] == kind and predicate(incoming["data"]):
                    return self.inbox.pop(index)
            incoming = read_message(self.stream)
            if incoming is None:
                raise AssertionError(f"等待 {kind} 时连接关闭")
            if incoming["type"] == kind and predicate(incoming["data"]):
                return incoming
            self.inbox.append(incoming)
        raise AssertionError(f"等待 {kind} 超时")

    def close(self) -> None:
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.stream.close()
        except OSError:
            pass
        self.socket.close()


class NetworkIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        db_path = Path(self.temp.name) / "test.db"
        self.server = ArenaServer("127.0.0.1", 0, db_path)
        self.server.start()
        self.clients: list[WireClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.server.stop()
        self.server.database.close()
        self.temp.cleanup()

    def client(self, username: str) -> tuple[WireClient, str]:
        client = WireClient(self.server.port)
        self.clients.append(client)
        client.send("register", username=username, password="test1234")
        auth = client.until("auth_ok")["data"]
        self.assertEqual(auth["username"], username)
        return client, auth["token"]

    def start_room(self) -> tuple[WireClient, WireClient, int]:
        first, _ = self.client("alice")
        second, _ = self.client("bob")
        first.send("create_room", name="协议测试")
        room_id = first.until("joined")["data"]["room_id"]
        second.send("join_room", room_id=room_id)
        second.until("joined")
        first.send("ready", ready=True)
        second.send("ready", ready=True)
        # 准备不再自动开局；等服务器确认全员准备后再由房主开始，避免启动竞态。
        first.until(
            "room_state",
            lambda data: all(
                player.get("ready")
                for player in data["room"].get("player_list", [])
            ),
        )
        first.send("start_room")
        first.until("snapshot", lambda data: data.get("phase") == "playing")
        second.until("snapshot", lambda data: data.get("phase") == "playing")
        return first, second, room_id

    def test_room_input_weapon_and_midgame_spectator_chat(self) -> None:
        first, second, room_id = self.start_room()
        first.send(
            "input",
            seq=1,
            move_x=0,
            move_y=0,
            angle=0.5,
            shoot=True,
            weapon="rifle",
        )
        predicate = lambda data: any(
            player["username"] == "alice" and player["ack_seq"] >= 1 and player["weapon"] == "rifle"
            for player in data.get("players", [])
        )
        first.until("snapshot", predicate)
        second.until("snapshot", predicate)

        spectator, _ = self.client("viewer")
        # 对局中加入且仍有空位会直接参战；显式请求观战则进入观战
        spectator.send("join_room", room_id=room_id, spectator=True)
        joined = spectator.until("joined")["data"]
        self.assertTrue(joined["spectator"])
        spectator.send("chat", text="观战消息")
        chat = first.until("chat", lambda data: data.get("text") == "观战消息")["data"]
        self.assertEqual(chat["username"], "viewer")
        self.assertEqual(chat["room_id"], room_id)

    def test_reconnect_flow(self) -> None:
        first, _ = self.client("alice")
        second, token = self.client("bob")
        first.send("create_room", name="重连测试")
        room_id = first.until("joined")["data"]["room_id"]
        second.send("join_room", room_id=room_id)
        second.until("joined")
        first.send("ready", ready=True)
        second.send("ready", ready=True)
        first.send("start_room")
        first.until("snapshot", lambda data: data.get("phase") == "playing")
        second.until("snapshot", lambda data: data.get("phase") == "playing")

        second.close()
        first.until(
            "room_state",
            lambda data: any(
                p["username"] == "bob" and not p["connected"]
                for p in data["room"].get("player_list", [])
            ),
        )
        replacement = WireClient(self.server.port)
        self.clients.append(replacement)
        replacement.send("reconnect", token=token)
        auth = replacement.until("auth_ok")["data"]
        self.assertTrue(auth["reconnected"])
        replacement.until(
            "room_state",
            lambda data: any(
                p["username"] == "bob" and p["connected"]
                for p in data["room"].get("player_list", [])
            ),
        )
        snapshot = replacement.until("snapshot", lambda data: data.get("phase") == "playing")["data"]
        self.assertIn("bob", {player["username"] for player in snapshot["players"]})
        first.until(
            "room_state",
            lambda data: any(
                p["username"] == "bob" and p["connected"]
                for p in data["room"].get("player_list", [])
            ),
        )

    def test_leave_sends_final_snapshot_and_empty_room_list(self) -> None:
        first, second, room_id = self.start_room()
        observer, _ = self.client("observer")
        first.send("leave_room")
        first.until("left")
        snapshot = second.until("snapshot", lambda data: data.get("phase") == "finished")["data"]
        self.assertEqual({p["username"] for p in snapshot["players"]}, {"bob"})
        self.assertTrue(snapshot["result"])

        second.send("leave_room")
        second.until("left")
        observer.until(
            "rooms",
            lambda data: room_id not in {room["id"] for room in data.get("rooms", [])},
        )

    def test_owner_dissolve_updates_non_member_room_list(self) -> None:
        first, _ = self.client("disalice")
        second, _ = self.client("disbob")
        first.send("create_room", name="解散测试")
        room_id = first.until("joined")["data"]["room_id"]
        second.send("join_room", room_id=room_id)
        second.until("joined")
        observer, _ = self.client("disobserver")
        first.send("dissolve_room")
        observer.until(
            "rooms",
            lambda data: room_id not in {room["id"] for room in data.get("rooms", [])},
        )

    def test_game_over_is_persisted_to_leaderboard(self) -> None:
        first, second, room_id = self.start_room()
        with self.server._lock:
            room = self.server.rooms[room_id]
            room.players["alice"].kills = 2
            room.round_end = room.clock() + 0.05
        result = first.until("game_over")["data"]["results"]
        second.until("game_over")
        self.assertEqual(result[0]["username"], "alice")
        leaderboard = first.until(
            "leaderboard",
            lambda data: any(entry["username"] == "alice" and entry["games"] == 1 for entry in data["entries"]),
        )["data"]["entries"]
        alice = next(entry for entry in leaderboard if entry["username"] == "alice")
        bob = next(entry for entry in leaderboard if entry["username"] == "bob")
        self.assertEqual((alice["score"], alice["wins"], alice["kills"]), (500, 1, 2))
        self.assertEqual((bob["score"], bob["games"]), (0, 1))

    def test_duplicate_name_and_wrong_password_are_rejected(self) -> None:
        client, _ = self.client("secure")
        duplicate = WireClient(self.server.port)
        self.clients.append(duplicate)
        duplicate.send("register", username="secure", password="other123")
        self.assertEqual(duplicate.until("error")["data"]["text"], "用户名已存在")
        duplicate.send("login", username="secure", password="wrong")
        self.assertEqual(duplicate.until("error")["data"]["text"], "用户名或密码错误")


if __name__ == "__main__":
    unittest.main()
