"""FastAPI + WebSocket 的浏览器游戏服务，复用服务器权威 Room 规则。"""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import Database
from .game import MAX_PLAYERS, MIN_PLAYERS, Room

LOG = logging.getLogger(__name__)


@dataclass
class WebSession:
    username: str
    token: str
    peer: "WebPeer | None" = None


class WebPeer:
    def __init__(self, service: "WebGameService", websocket: WebSocket):
        self.service = service
        self.websocket = websocket
        self.username: str | None = None
        self.alive = True
        self.outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=600)
        self.writer: asyncio.Task[None] | None = None

    def send(self, kind: str, **data: Any) -> None:
        if not self.alive:
            return
        try:
            self.outbox.put_nowait({"type": kind, "data": data})
        except asyncio.QueueFull:
            if kind == "snapshot":
                return
            self.alive = False
            asyncio.create_task(self.close())

    async def _write_loop(self) -> None:
        try:
            while self.alive:
                outgoing = await self.outbox.get()
                await self.websocket.send_json(outgoing)
        except (RuntimeError, WebSocketDisconnect):
            pass
        finally:
            self.alive = False

    async def serve(self) -> None:
        self.writer = asyncio.create_task(self._write_loop())
        try:
            while self.alive and self.service.running:
                incoming = await self.websocket.receive_json()
                if not isinstance(incoming, dict) or not isinstance(incoming.get("type"), str):
                    self.send("error", code="protocol_error", text="消息格式不正确")
                    continue
                data = incoming.setdefault("data", {})
                if not isinstance(data, dict):
                    self.send("error", code="protocol_error", text="data 必须是对象")
                    continue
                await self.service.handle(self, incoming["type"], data)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        except Exception:
            LOG.exception("WebSocket 客户端处理失败")
        finally:
            self.alive = False
            await self.service.disconnect(self)
            await self.close()
            if self.writer is not None:
                self.writer.cancel()
                try:
                    await self.writer
                except asyncio.CancelledError:
                    pass

    async def close(self) -> None:
        self.alive = False
        try:
            await self.websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


class WebGameService:
    def __init__(self, db_path: str | Path, tick_rate: int = 30):
        self.database = Database(db_path)
        self.tick_rate = tick_rate
        self.running = False
        self.rooms: dict[int, Room] = {}
        self.users: dict[str, WebSession] = {}
        self.sessions: dict[str, WebSession] = {}
        self.peers: set[WebPeer] = set()
        self._next_room_id = 1
        self._lock = asyncio.Lock()
        self._game_task: asyncio.Task[None] | None = None
        self._room_errors: set[int] = set()

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._game_task = asyncio.create_task(self._game_loop(), name="web-game-loop")

    async def stop(self) -> None:
        self.running = False
        if self._game_task is not None:
            self._game_task.cancel()
            try:
                await self._game_task
            except asyncio.CancelledError:
                pass
        for peer in list(self.peers):
            await peer.close()
        self.database.close()

    async def _game_loop(self) -> None:
        interval = 1.0 / self.tick_rate
        next_tick = time.monotonic()
        while self.running:
            now = time.monotonic()
            next_tick += interval
            if next_tick < now - interval * 5:
                next_tick = now + interval
            async with self._lock:
                for room in list(self.rooms.values()):
                    try:
                        await self._tick_room(room, now, interval)
                    except Exception:
                        # 单个房间的异常不能终止整个仿真循环，否则所有房间都会卡死；
                        # 同一个房间的连续异常只记录一次，避免 30Hz 刷日志。
                        if room.id not in self._room_errors:
                            LOG.exception("房间 %s 更新失败", room.id)
                            self._room_errors.add(room.id)
                    else:
                        self._room_errors.discard(room.id)
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    async def _tick_room(self, room: Room, now: float, interval: float) -> None:
        result = room.update(now, interval)
        # 每帧把新产生的淘汰事件下发给房间内所有成员。
        eliminated = False
        while room.elimination_events:
            event = room.elimination_events.pop(0)
            self._send_to_room(room, "eliminated", **event)
            eliminated = True
        if result is not None:
            try:
                await self._commit_result(room, result)
            except Exception:
                # 结算写库失败时仍继续发送快照，让客户端能显示本局结果。
                LOG.exception("房间 %s 结算写入失败", room.id)
            # 同步房间阶段与房间列表，避免大厅仍显示 playing。
            self._broadcast_room_state(room)
            # 只补发一帧最终快照；之后结束房间不再 30Hz 广播。
            self._send_to_room(room, "snapshot", **room.snapshot())
        elif room.phase == "playing":
            # 淘汰会改变玩家/观众席位，必须同步大厅的房间列表；
            # 否则玩家回到大厅后仍会看到自己还在玩家列表中。
            if eliminated:
                self._broadcast_room_state(room)
            self._send_to_room(room, "snapshot", **room.snapshot())

    async def handle(self, peer: WebPeer, kind: str, data: dict[str, Any]) -> None:
        try:
            # PBKDF2 哈希耗时较长，认证必须放在全局锁外，否则会阻塞 30Hz 游戏循环。
            if kind in ("register", "login"):
                await self._authenticate(peer, kind, data)
                return
            async with self._lock:
                await self._dispatch(peer, kind, data)
        except (ValueError, TypeError, OverflowError):
            peer.send("error", code="request_error", text="请求字段格式不正确")
        except Exception:
            LOG.exception("处理 WebSocket 消息 %s 失败", kind)
            peer.send("error", code="server_error", text="服务器处理请求失败")

    async def _authenticate(self, peer: WebPeer, kind: str, data: dict[str, Any]) -> None:
        if kind == "register":
            username = self._valid_username(data.get("username"))
            password = self._valid_password(data.get("password"))
            created = await asyncio.to_thread(self.database.create_user, username, password)
            if not created:
                peer.send("error", code="request_error", text="用户名已存在")
                return
        else:
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            authenticated = await asyncio.to_thread(self.database.authenticate, username, password)
            if not authenticated:
                peer.send("error", code="request_error", text="用户名或密码错误")
                return
        async with self._lock:
            existing = self.users.get(username.casefold())
            await self._attach_session(peer, existing.username if existing else username)

    async def _dispatch(self, peer: WebPeer, kind: str, data: dict[str, Any]) -> None:
        if kind == "ping":
            peer.send("pong", client_time=data.get("client_time"), server_time=time.monotonic())
            return
        if kind == "reconnect":
            session = self.sessions.get(str(data.get("token", "")))
            if session is None:
                peer.send("error", code="auth_required", text="登录状态已失效，请重新登录")
                return
            await self._attach_session(peer, session.username, session.token, True)
            return
        if peer.username is None:
            peer.send("error", code="auth_required", text="请先登录")
            return
        if kind == "list_rooms":
            self._send_rooms(peer)
        elif kind == "leaderboard":
            entries = await asyncio.to_thread(self.database.leaderboard)
            peer.send("leaderboard", entries=entries)
        elif kind == "create_room":
            self._create_room(peer, str(data.get("name", "")).strip())
        elif kind == "join_room":
            self._join_room(peer, self._as_int(data.get("room_id")), bool(data.get("spectator", False)))
        elif kind == "rejoin_room":
            self._rejoin_room(peer)
        elif kind == "leave_room":
            await self._leave_room(peer)
        elif kind == "ready":
            self._set_ready(peer, bool(data.get("ready", False)))
        elif kind == "start_room":
            self._start_room(peer)
        elif kind == "dissolve_room":
            await self._dissolve_room(peer)
        elif kind == "input":
            self._set_input(peer, data)
        elif kind == "chat":
            self._chat(peer, str(data.get("text", "")).strip())
        else:
            peer.send("error", code="request_error", text=f"未知消息类型：{kind}")

    async def _attach_session(
        self, peer: WebPeer, username: str, token: str | None = None, reconnected: bool = False
    ) -> None:
        key = username.casefold()
        session = self.users.get(key)
        if session is None:
            session = WebSession(username, token or secrets.token_urlsafe(32))
            self.users[key] = session
            self.sessions[session.token] = session
        elif token is None:
            self.sessions.pop(session.token, None)
            session.token = secrets.token_urlsafe(32)
            self.sessions[session.token] = session
        old_peer = session.peer
        session.peer = peer
        peer.username = session.username
        room = self._room_for_user(session.username)
        if room is not None:
            room.set_connected(session.username, True)
        peer.send(
            "auth_ok", username=session.username, token=session.token, reconnected=reconnected
        )
        self._send_rooms(peer)
        entries = await asyncio.to_thread(self.database.leaderboard)
        peer.send("leaderboard", entries=entries)
        if room is not None:
            peer.send("room_state", room=room.room_state())
            peer.send("snapshot", **room.snapshot())
            self._broadcast_room_state(room)
        if old_peer is not None and old_peer is not peer:
            await old_peer.close()

    async def disconnect(self, peer: WebPeer) -> None:
        async with self._lock:
            self.peers.discard(peer)
            if peer.username is None:
                return
            session = self.users.get(peer.username.casefold())
            if session is None or session.peer is not peer:
                return
            session.peer = None
            room = self._room_for_user(peer.username)
            if room is not None:
                room.set_connected(peer.username, False)
                self._broadcast_room_state(room)
                # 已结束房间不再有 tick 快照；断线后补一帧，避免其他客户端保留旧在线状态。
                if room.phase == "finished":
                    self._send_to_room(room, "snapshot", **room.snapshot())

    def _create_room(self, peer: WebPeer, name: str) -> None:
        assert peer.username is not None
        if self._room_for_user(peer.username) is not None:
            peer.send("error", code="request_error", text="请先离开当前房间")
            return
        if not name:
            name = f"{peer.username} 的房间"
        if len(name) > 20 or any(ord(char) < 32 for char in name):
            peer.send("error", code="request_error", text="房间名应为 1～20 个可见字符")
            return
        room = Room(self._next_room_id, name, peer.username)
        self._next_room_id += 1
        room.add_user(peer.username)
        self.rooms[room.id] = room
        peer.send("joined", room_id=room.id, spectator=False, notice="房间创建成功")
        self._broadcast_room_state(room)

    def _join_room(self, peer: WebPeer, room_id: int, spectator: bool) -> None:
        assert peer.username is not None
        if self._room_for_user(peer.username) is not None:
            peer.send("error", code="request_error", text="请先离开当前房间")
            return
        room = self.rooms.get(room_id)
        if room is None:
            peer.send("error", code="request_error", text="房间不存在")
            return
        _, notice, is_spectator = room.add_user(peer.username, spectator)
        peer.send("joined", room_id=room.id, spectator=is_spectator, notice=notice)
        self._broadcast_room_state(room)
        peer.send("snapshot", **room.snapshot())

    def _rejoin_room(self, peer: WebPeer) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None:
            peer.send("error", code="request_error", text="当前不在房间中")
            return
        if peer.username not in room.spectators:
            peer.send("error", code="request_error", text="当前已是玩家，无需重新参战")
            return
        if len(room.players) >= MAX_PLAYERS:
            peer.send("error", code="request_error", text="玩家席位已满，请等待空位")
            return
        _, notice, is_spectator = room.promote_spectator(peer.username)
        peer.send("joined", room_id=room.id, spectator=is_spectator, notice=notice)
        self._broadcast_room_state(room)
        self._send_to_room(room, "snapshot", **room.snapshot())

    async def _leave_room(self, peer: WebPeer) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None:
            peer.send("error", code="request_error", text="当前不在房间中")
            return
        result = room.remove_user(peer.username)
        if result:
            try:
                await self._commit_result(room, result)
            except Exception:
                # 即使结算写库失败，也要完成离房/房间清理，不能留下空房间。
                LOG.exception("房间 %s 结算写入失败", room.id)
        if not room.usernames():
            del self.rooms[room.id]
            # 房间删除后大厅列表必须刷新，否则其他客户端会继续显示已不存在的房间。
            self._broadcast_rooms()
        else:
            self._broadcast_room_state(room)
            # 离房可能触发“不足两人”的结算；此时 finished 房间不会再走 tick 广播，
            # 必须补发最终快照，客户端才能看到本局结果和最新玩家列表。
            self._send_to_room(room, "snapshot", **room.snapshot())
        peer.send("left")

    def _set_ready(self, peer: WebPeer, ready: bool) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None or peer.username not in room.players:
            peer.send("error", code="request_error", text="观众不能准备")
            return
        if room.phase not in ("waiting", "finished"):
            peer.send("error", code="request_error", text="对局进行中")
            return
        room.set_ready(peer.username, ready)
        self._broadcast_room_state(room)

    def _start_room(self, peer: WebPeer) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None:
            peer.send("error", code="request_error", text="当前不在房间中")
            return
        ok, reason = room.start_by_owner(peer.username)
        if not ok:
            peer.send("error", code="request_error", text=reason)
            return
        self._broadcast_room_state(room)
        self._send_to_room(room, "snapshot", **room.snapshot())

    def _dissolve_room(self, peer: WebPeer) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None:
            peer.send("error", code="request_error", text="当前不在房间中")
            return
        if peer.username != room.owner:
            peer.send("error", code="request_error", text="只有房主可以解散房间")
            return
        members = list(room.usernames())
        self._send_to_room(
            room,
            "notice",
            text="房主已解散房间",
        )
        del self.rooms[room.id]
        # 解散后不仅要通知房间成员，也要让其他大厅客户端收到新的房间列表。
        self._broadcast_rooms()
        for username in members:
            session = self.users.get(username.casefold())
            if session is not None and session.peer is not None:
                target = session.peer
                target.send("left")
                self._send_rooms(target)

    def _set_input(self, peer: WebPeer, data: dict[str, Any]) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None:
            return
        room.set_input(
            peer.username,
            self._as_int(data.get("seq")),
            self._as_float(data.get("move_x", 0)),
            self._as_float(data.get("move_y", 0)),
            self._as_float(data.get("angle", 0)),
            bool(data.get("shoot", False)),
            str(data.get("weapon", "pistol")),
        )

    def _chat(self, peer: WebPeer, text: str) -> None:
        assert peer.username is not None
        room = self._room_for_user(peer.username)
        if room is None:
            peer.send("error", code="request_error", text="加入房间后才能聊天")
            return
        if not text:
            return
        if len(text) > 200 or any(ord(char) < 32 for char in text):
            peer.send("error", code="request_error", text="聊天内容应为 1～200 个可见字符")
            return
        self._send_to_room(
            room, "chat", room_id=room.id, username=peer.username, text=text,
            timestamp=time.time(), system=False,
        )

    async def _commit_result(self, room: Room, result: list[dict[str, int | str]]) -> None:
        if not result:
            return
        await asyncio.to_thread(self.database.record_match, result)
        self._send_to_room(room, "game_over", room_id=room.id, results=result)
        entries = await asyncio.to_thread(self.database.leaderboard)
        for peer in list(self.peers):
            if peer.username is not None:
                peer.send("leaderboard", entries=entries)

    def _room_for_user(self, username: str) -> Room | None:
        return next((room for room in self.rooms.values() if room.has_user(username)), None)

    def _send_to_room(self, target: Room, kind: str, **data: Any) -> None:
        for username in target.usernames():
            session = self.users.get(username.casefold())
            if session is not None and session.peer is not None:
                session.peer.send(kind, **data)

    def _broadcast_room_state(self, room: Room) -> None:
        self._send_to_room(room, "room_state", room=room.room_state())
        self._broadcast_rooms()

    def _broadcast_rooms(self) -> None:
        rooms = [room.room_info() for room in self.rooms.values()]
        for peer in list(self.peers):
            if peer.username is not None:
                peer.send("rooms", rooms=rooms)

    def _send_rooms(self, peer: WebPeer) -> None:
        peer.send("rooms", rooms=[room.room_info() for room in self.rooms.values()])

    @staticmethod
    def _valid_username(value: Any) -> str:
        username = str(value or "").strip()
        if not 2 <= len(username) <= 16:
            raise ValueError("用户名长度不符合要求")
        if not all(char.isalnum() or char in "_-" for char in username):
            raise ValueError("用户名包含非法字符")
        return username

    @staticmethod
    def _valid_password(value: Any) -> str:
        password = str(value or "")
        if not 4 <= len(password) <= 64:
            raise ValueError("密码长度不符合要求")
        return password

    @staticmethod
    def _as_int(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("不能将布尔值作为整数")
        return int(value)

    @staticmethod
    def _as_float(value: Any) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("数值必须有限")
        return value


def create_app(
    db_path: str | Path = "data/arena.db",
    static_dir: str | Path | None = None,
    tick_rate: int = 30,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = WebGameService(db_path, tick_rate)
        app.state.game = service
        await service.start()
        yield
        await service.stop()

    app = FastAPI(title="BLACKLINE Arena", version="2.0.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "rooms": len(app.state.game.rooms),
            "connections": len(app.state.game.peers),
            "tick_rate": app.state.game.tick_rate,
        }

    @app.get("/api/leaderboard")
    async def leaderboard() -> JSONResponse:
        entries = await asyncio.to_thread(app.state.game.database.leaderboard)
        return JSONResponse({"entries": entries})

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        peer = WebPeer(app.state.game, websocket)
        app.state.game.peers.add(peer)
        await peer.serve()

    if static_dir is not None and Path(static_dir).is_dir():
        dist = Path(static_dir).resolve()
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            from fastapi.responses import FileResponse

            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def frontend_missing() -> dict[str, str]:
            return {"status": "ok", "message": "Vue 前端尚未构建，请先执行 npm run build"}

    return app
