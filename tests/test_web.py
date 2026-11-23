from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from arena.web import create_app


class WebIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.client_context = TestClient(create_app(Path(self.temp.name) / "web.db"))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def send(socket, kind: str, **data) -> None:
        socket.send_json({"type": kind, "data": data})

    @staticmethod
    def until(socket, kind: str, predicate=None, limit: int = 300) -> dict:
        predicate = predicate or (lambda _data: True)
        for _ in range(limit):
            incoming = socket.receive_json()
            if incoming["type"] == kind and predicate(incoming["data"]):
                return incoming["data"]
        raise AssertionError(f"没有收到预期消息 {kind}")

    def register(self, socket, username: str) -> str:
        self.send(socket, "register", username=username, password="test1234")
        return self.until(socket, "auth_ok")["token"]

    def test_health_and_spa_api(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tick_rate"], 30)

    def test_built_vue_spa_is_served_by_fastapi(self) -> None:
        dist = Path(__file__).resolve().parents[1] / "web" / "dist"
        if not (dist / "index.html").is_file():
            self.skipTest("请先执行 npm run build 生成 Vue 生产文件")
        with TestClient(create_app(Path(self.temp.name) / "spa.db", static_dir=dist)) as spa_client:
            response = spa_client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("BLACKLINE", response.text)
            self.assertEqual(spa_client.get("/some-client-route").status_code, 200)
            self.assertEqual(spa_client.get("/api/not-found").status_code, 404)

    def test_websocket_room_ready_chat_and_reconnect(self) -> None:
        with self.client.websocket_connect("/ws") as first, self.client.websocket_connect("/ws") as second:
            self.register(first, "webalice")
            token = self.register(second, "webbob")
            self.send(first, "create_room", name="Web 对战")
            room_id = self.until(first, "joined")["room_id"]
            self.send(second, "join_room", room_id=room_id)
            self.until(second, "joined")
            self.send(first, "ready", ready=True)
            self.send(second, "ready", ready=True)
            # 准备不再自动开局，需要房主显式开始
            self.send(first, "start_room")
            self.until(first, "snapshot", lambda data: data.get("phase") == "playing")
            self.until(second, "snapshot", lambda data: data.get("phase") == "playing")
            self.send(second, "chat", text="浏览器聊天")
            self.assertEqual(self.until(first, "chat")["text"], "浏览器聊天")
            self.send(first, "input", seq=1, move_x=1, move_y=0, angle=0, shoot=False, weapon="rifle")
            predicate = lambda data: any(p["username"] == "webalice" and p["ack_seq"] >= 1 for p in data.get("players", []))
            self.until(first, "snapshot", predicate)
            self.until(second, "snapshot", predicate)

        # 第二个连接正常关闭后，用原令牌在新的 WebSocket 上恢复同一身份。
        with self.client.websocket_connect("/ws") as restored:
            self.send(restored, "reconnect", token=token)
            auth = self.until(restored, "auth_ok")
            self.assertTrue(auth["reconnected"])
            room = self.until(restored, "room_state")["room"]
            self.assertIn("webbob", {p["username"] for p in room["player_list"]})

    def test_owner_start_permission_and_dissolve(self) -> None:
        with self.client.websocket_connect("/ws") as first, self.client.websocket_connect("/ws") as second:
            self.register(first, "ownera")
            self.register(second, "ownerb")
            self.send(first, "create_room", name="权限房间")
            room_id = self.until(first, "joined")["room_id"]
            self.send(second, "join_room", room_id=room_id)
            self.until(second, "joined")
            # 非房主开始被拒绝
            self.send(second, "start_room")
            self.assertEqual(self.until(second, "error")["text"], "只有房主可以开始游戏")
            # 未全员准备时房主也不能开始
            self.send(first, "start_room")
            self.assertEqual(self.until(first, "error")["text"], "仍有玩家未准备")
            self.send(first, "ready", ready=True)
            self.send(second, "ready", ready=True)
            self.send(first, "start_room")
            self.until(first, "snapshot", lambda data: data.get("phase") == "playing")
            # 非房主不能解散
            self.send(second, "dissolve_room")
            self.assertEqual(self.until(second, "error")["text"], "只有房主可以解散房间")
            self.send(first, "dissolve_room")
            self.until(second, "left")
            self.until(first, "left")

    def test_midgame_join_as_combatant(self) -> None:
        with self.client.websocket_connect("/ws") as first, self.client.websocket_connect("/ws") as second:
            self.register(first, "mida")
            self.register(second, "midb")
            self.send(first, "create_room", name="随时加入")
            room_id = self.until(first, "joined")["room_id"]
            self.send(second, "join_room", room_id=room_id)
            self.until(second, "joined")
            self.send(first, "ready", ready=True)
            self.send(second, "ready", ready=True)
            self.send(first, "start_room")
            self.until(first, "snapshot", lambda data: data.get("phase") == "playing")
            self.until(second, "snapshot", lambda data: data.get("phase") == "playing")

        with self.client.websocket_connect("/ws") as third:
            self.register(third, "midc")
            # 对局进行中、席位未满，加入即参战
            self.send(third, "join_room", room_id=room_id)
            joined = self.until(third, "joined")
            self.assertFalse(joined["spectator"])
            snap = self.until(third, "snapshot", lambda data: any(p["username"] == "midc" for p in data.get("players", [])))
            self.assertIn("midc", {p["username"] for p in snap["players"]})

    def test_owner_leave_keeps_room_alive_for_remaining(self) -> None:
        with self.client.websocket_connect("/ws") as first, self.client.websocket_connect("/ws") as second:
            self.register(first, "leavea")
            self.register(second, "leaveb")
            self.send(first, "create_room", name="离房不结束")
            room_id = self.until(first, "joined")["room_id"]
            self.send(second, "join_room", room_id=room_id)
            self.until(second, "joined")
            self.send(first, "ready", ready=True)
            self.send(second, "ready", ready=True)
            self.send(first, "start_room")
            self.until(first, "snapshot", lambda data: data.get("phase") == "playing")
            self.until(second, "snapshot", lambda data: data.get("phase") == "playing")

            # 房主退出后房间只剩一人，按新规则对局继续、不提前结束，房主转移给剩余玩家。
            self.send(first, "leave_room")
            self.until(first, "left")
            room_state = self.until(
                second,
                "room_state",
                lambda data: data["room"].get("owner") == "leaveb",
            )
            self.assertEqual(room_state["room"]["owner"], "leaveb")
            playing = self.until(second, "snapshot", lambda data: data.get("phase") == "playing")
            self.assertEqual({p["username"] for p in playing["players"]}, {"leaveb"})
            self.assertFalse(playing.get("result"))

    def test_dissolve_updates_non_member_room_list(self) -> None:
        with self.client.websocket_connect("/ws") as first, self.client.websocket_connect("/ws") as second:
            self.register(first, "disa")
            self.register(second, "disb")
            self.send(first, "create_room", name="解散房间")
            room_id = self.until(first, "joined")["room_id"]
            self.send(second, "join_room", room_id=room_id)
            self.until(second, "joined")

            with self.client.websocket_connect("/ws") as observer:
                self.register(observer, "disobserver")
                self.send(first, "dissolve_room")
                rooms = self.until(
                    observer,
                    "rooms",
                    lambda data: room_id not in {room["id"] for room in data.get("rooms", [])},
                )
                self.assertNotIn(room_id, {room["id"] for room in rooms["rooms"]})

    def test_last_player_leave_updates_non_member_room_list(self) -> None:
        with self.client.websocket_connect("/ws") as first:
            self.register(first, "solo")
            self.send(first, "create_room", name="空房间")
            room_id = self.until(first, "joined")["room_id"]

            with self.client.websocket_connect("/ws") as observer:
                self.register(observer, "soloobserver")
                self.send(first, "leave_room")
                rooms = self.until(
                    observer,
                    "rooms",
                    lambda data: room_id not in {room["id"] for room in data.get("rooms", [])},
                )
                self.assertNotIn(room_id, {room["id"] for room in rooms["rooms"]})


if __name__ == "__main__":
    unittest.main()
