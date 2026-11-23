"""多线程 TCP 服务器：认证、房间、权威仿真和状态广播。"""

from __future__ import annotations

import logging
import queue
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import Database
from .game import MAX_PLAYERS, MIN_PLAYERS, Room
from .protocol import ProtocolError, encode, message, read_message

LOG = logging.getLogger(__name__)


@dataclass
class Session:
    username: str
    token: str
    connection: "ClientConnection | None" = None


class ClientConnection:
    """一个 socket 对应一个读线程和一个写线程，发送操作永不阻塞游戏 tick。"""

    def __init__(self, server: "ArenaServer", sock: socket.socket, address: tuple[Any, ...]):
        self.server = server
        self.socket = sock
        self.address = address
        self.username: str | None = None
        self.alive = True
        self._close_lock = threading.Lock()
        self._outbox: queue.Queue[bytes | None] = queue.Queue(maxsize=600)
        self._writer = threading.Thread(target=self._write_loop, daemon=True, name="client-writer")

    def serve(self) -> None:
        self._writer.start()
        try:
            stream = self.socket.makefile("rb")
            while self.alive and self.server.running:
                incoming = read_message(stream)
                if incoming is None:
                    break
                self.server.handle_message(self, incoming)
        except (OSError, ProtocolError) as exc:
            LOG.debug("连接 %s 结束：%s", self.address, exc)
        except Exception:
            LOG.exception("处理客户端 %s 时发生异常", self.address)
        finally:
            self.close()
            self.server.connection_closed(self)

    def send(self, outgoing: dict[str, Any]) -> None:
        if not self.alive:
            return
        try:
            self._outbox.put_nowait(encode(outgoing))
        except queue.Full:
            # 快照可被下一帧替代；控制消息积压则说明客户端已无法正常接收。
            if outgoing.get("type") == "snapshot":
                return
            self.close()
        except ProtocolError:
            LOG.exception("无法编码服务器消息")

    def _write_loop(self) -> None:
        while self.alive:
            try:
                payload = self._outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                break
            try:
                self.socket.sendall(payload)
            except OSError:
                break
        self.close()

    def close(self) -> None:
        with self._close_lock:
            if not self.alive:
                return
            self.alive = False
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.socket.close()
            except OSError:
                pass


class ArenaServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        db_path: str | Path = "data/arena.db",
        tick_rate: int = 30,
    ):
        self.host = host
        self.port = port
        self.tick_rate = tick_rate
        self.database = Database(db_path)
        self.running = False
        self.rooms: dict[int, Room] = {}
        self.users: dict[str, Session] = {}
        self.sessions: dict[str, Session] = {}
        self.connections: set[ClientConnection] = set()
        self._next_room_id = 1
        self._lock = threading.RLock()
        self._room_errors: set[int] = set()
        self._listener: socket.socket | None = None
        self._stop = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._tick_thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    def start(self) -> None:
        if self.running:
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(100)
        listener.settimeout(0.5)
        self.port = listener.getsockname()[1]
        self._listener = listener
        self.running = True
        self._stop.clear()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="accept-loop")
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True, name="game-tick")
        self._accept_thread.start()
        self._tick_thread.start()
        LOG.info("服务器已监听 %s:%s，逻辑帧率 %sHz", self.host, self.port, self.tick_rate)

    def stop(self) -> None:
        if not self.running:
            self.database.close()
            return
        self.running = False
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        with self._lock:
            connections = list(self.connections)
        for connection in connections:
            connection.close()
        current = threading.current_thread()
        for thread in (self._accept_thread, self._tick_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=2)

    def serve_forever(self) -> None:
        self.start()
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            LOG.info("收到退出信号")
        finally:
            self.stop()
            self.database.close()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while self.running:
            try:
                sock, address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection = ClientConnection(self, sock, address)
            with self._lock:
                self.connections.add(connection)
            threading.Thread(
                target=connection.serve,
                daemon=True,
                name=f"client-{address}",
            ).start()

    def _tick_loop(self) -> None:
        interval = 1.0 / self.tick_rate
        next_tick = time.monotonic()
        while not self._stop.wait(max(0.0, next_tick - time.monotonic())):
            now = time.monotonic()
            next_tick += interval
            if next_tick < now - interval * 5:
                next_tick = now + interval
            with self._lock:
                for room in list(self.rooms.values()):
                    try:
                        eliminated = False
                        while room.elimination_events:
                            event = room.elimination_events.pop(0)
                            self._send_to_room(room, message("eliminated", **event))
                            eliminated = True
                        result = room.update(now, interval)
                        if result is not None:
                            try:
                                self._commit_result(room, result)
                            except Exception:
                                LOG.exception("房间 %s 结算写入失败", room.id)
                            # 同步房间阶段与房间列表，避免大厅仍显示 playing。
                            self._broadcast_room_state(room)
                            # 只补发一帧最终快照；之后结束房间不再 30Hz 广播。
                            self._send_to_room(room, message("snapshot", **room.snapshot()))
                        elif room.phase == "playing":
                            # 淘汰改变玩家/观众席位，需要同步大厅列表。
                            if eliminated:
                                self._broadcast_room_state(room)
                            self._send_to_room(room, message("snapshot", **room.snapshot()))
                    except Exception:
                        # 单个房间出错不应终止旧版 TCP 服务的仿真线程。
                        if room.id not in self._room_errors:
                            LOG.exception("房间 %s 更新失败", room.id)
                            self._room_errors.add(room.id)
                    else:
                        self._room_errors.discard(room.id)

    def handle_message(self, connection: ClientConnection, incoming: dict[str, Any]) -> None:
        kind = incoming["type"]
        data = incoming["data"]
        try:
            # PBKDF2 哈希不能在全局锁内执行，否则登录会暂停所有房间的 tick。
            if kind in ("register", "login"):
                self._authenticate(connection, kind, data)
                return
            with self._lock:
                self._dispatch(connection, kind, data)
        except (ValueError, TypeError, OverflowError):
            self._error(connection, "请求字段格式不正确")
        except Exception:
            LOG.exception("处理消息 %s 失败", kind)
            self._error(connection, "服务器处理请求失败")

    def _authenticate(self, connection: ClientConnection, kind: str, data: dict[str, Any]) -> None:
        if kind == "register":
            username = self._valid_username(data.get("username"))
            password = self._valid_password(data.get("password"))
            if not self.database.create_user(username, password):
                self._error(connection, "用户名已存在")
                return
        else:
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            if not self.database.authenticate(username, password):
                self._error(connection, "用户名或密码错误")
                return
        with self._lock:
            existing = self.users.get(username.casefold())
            self._attach_session(connection, existing.username if existing else username)

    def _dispatch(self, connection: ClientConnection, kind: str, data: dict[str, Any]) -> None:
        if kind == "ping":
            connection.send(message("pong", client_time=data.get("client_time"), server_time=time.monotonic()))
            return
        if kind == "reconnect":
            token = str(data.get("token", ""))
            session = self.sessions.get(token)
            if session is None:
                self._error(connection, "重连凭证无效，请重新登录", code="auth_required")
                return
            self._attach_session(connection, session.username, token=token, reconnected=True)
            return
        if connection.username is None:
            self._error(connection, "请先登录", code="auth_required")
            return

        if kind == "list_rooms":
            self._send_rooms(connection)
        elif kind == "leaderboard":
            connection.send(message("leaderboard", entries=self.database.leaderboard()))
        elif kind == "create_room":
            self._create_room(connection, str(data.get("name", "")).strip())
        elif kind == "join_room":
            self._join_room(connection, self._as_int(data.get("room_id")), bool(data.get("spectator", False)))
        elif kind == "rejoin_room":
            self._rejoin_room(connection)
        elif kind == "leave_room":
            self._leave_room(connection)
        elif kind == "ready":
            self._set_ready(connection, bool(data.get("ready", False)))
        elif kind == "start_room":
            self._start_room(connection)
        elif kind == "dissolve_room":
            self._dissolve_room(connection)
        elif kind == "input":
            self._set_input(connection, data)
        elif kind == "chat":
            self._chat(connection, str(data.get("text", "")).strip())
        else:
            self._error(connection, f"未知消息类型：{kind}")

    def _attach_session(
        self,
        connection: ClientConnection,
        username: str,
        token: str | None = None,
        reconnected: bool = False,
    ) -> None:
        key = username.casefold()
        session = self.users.get(key)
        if session is None:
            session = Session(username, token or secrets.token_urlsafe(32))
            self.users[key] = session
            self.sessions[session.token] = session
        elif token is None:
            # 新登录使旧凭证失效；断线自动重连则沿用原凭证。
            self.sessions.pop(session.token, None)
            session.token = secrets.token_urlsafe(32)
            self.sessions[session.token] = session
        old_connection = session.connection
        session.connection = connection
        connection.username = session.username
        room = self._room_for_user(session.username)
        if room is not None:
            room.set_connected(session.username, True)
        connection.send(
            message(
                "auth_ok",
                username=session.username,
                token=session.token,
                reconnected=reconnected,
            )
        )
        self._send_rooms(connection)
        connection.send(message("leaderboard", entries=self.database.leaderboard()))
        if room is not None:
            connection.send(message("room_state", room=room.room_state()))
            connection.send(message("snapshot", **room.snapshot()))
            self._broadcast_room_state(room)
        if old_connection is not None and old_connection is not connection:
            old_connection.close()

    def connection_closed(self, connection: ClientConnection) -> None:
        with self._lock:
            self.connections.discard(connection)
            username = connection.username
            if username is None:
                return
            session = self.users.get(username.casefold())
            # 被新连接替换的旧线程退出时，不能把新连接标记为离线。
            if session is None or session.connection is not connection:
                return
            session.connection = None
            room = self._room_for_user(username)
            if room is not None:
                room.set_connected(username, False)
                self._broadcast_room_state(room)
                # finished 房间不再 tick，断线后补一帧避免保留旧在线状态。
                if room.phase == "finished":
                    self._send_to_room(room, message("snapshot", **room.snapshot()))
                LOG.info("%s 断线，保留房间席位等待重连", username)

    def _create_room(self, connection: ClientConnection, name: str) -> None:
        assert connection.username is not None
        if self._room_for_user(connection.username) is not None:
            self._error(connection, "请先离开当前房间")
            return
        if not name:
            name = f"{connection.username} 的房间"
        if len(name) > 20 or any(ord(char) < 32 for char in name):
            self._error(connection, "房间名应为 1～20 个可见字符")
            return
        room = Room(self._next_room_id, name, connection.username)
        self._next_room_id += 1
        room.add_user(connection.username)
        self.rooms[room.id] = room
        connection.send(message("joined", room_id=room.id, spectator=False, notice="房间创建成功"))
        self._broadcast_room_state(room)

    def _join_room(self, connection: ClientConnection, room_id: int, spectator: bool) -> None:
        assert connection.username is not None
        if self._room_for_user(connection.username) is not None:
            self._error(connection, "请先离开当前房间")
            return
        room = self.rooms.get(room_id)
        if room is None:
            self._error(connection, "房间不存在")
            return
        _, notice, is_spectator = room.add_user(connection.username, spectator)
        connection.send(message("joined", room_id=room.id, spectator=is_spectator, notice=notice))
        self._broadcast_room_state(room)
        connection.send(message("snapshot", **room.snapshot()))

    def _rejoin_room(self, connection: ClientConnection) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None:
            self._error(connection, "当前不在房间中")
            return
        if connection.username not in room.spectators:
            self._error(connection, "当前已是玩家，无需重新参战")
            return
        if len(room.players) >= MAX_PLAYERS:
            self._error(connection, "玩家席位已满，请等待空位")
            return
        _, notice, is_spectator = room.promote_spectator(connection.username)
        connection.send(message("joined", room_id=room.id, spectator=is_spectator, notice=notice))
        self._broadcast_room_state(room)
        self._send_to_room(room, message("snapshot", **room.snapshot()))

    def _leave_room(self, connection: ClientConnection) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None:
            self._error(connection, "当前不在房间中")
            return
        result = room.remove_user(connection.username)
        if result:
            try:
                self._commit_result(room, result)
            except Exception:
                # 结算写库失败也不能阻止离房和空房间清理。
                LOG.exception("房间 %s 结算写入失败", room.id)
        if not room.usernames():
            del self.rooms[room.id]
            # 房间删除后必须让所有大厅客户端刷新，而不只是离开者。
            self._broadcast_rooms()
        else:
            self._broadcast_room_state(room)
            # 离房触发结算时，finished 房间不会再走 tick 广播，补发最终快照。
            self._send_to_room(room, message("snapshot", **room.snapshot()))
        connection.send(message("left"))
        self._send_rooms(connection)

    def _set_ready(self, connection: ClientConnection, ready: bool) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None or connection.username not in room.players:
            self._error(connection, "观众不能准备")
            return
        if room.phase not in ("waiting", "finished"):
            self._error(connection, "对局进行中")
            return
        room.set_ready(connection.username, ready)
        self._broadcast_room_state(room)

    def _start_room(self, connection: ClientConnection) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None:
            self._error(connection, "当前不在房间中")
            return
        ok, reason = room.start_by_owner(connection.username)
        if not ok:
            self._error(connection, reason)
            return
        self._broadcast_room_state(room)
        self._send_to_room(room, message("snapshot", **room.snapshot()))

    def _dissolve_room(self, connection: ClientConnection) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None:
            self._error(connection, "当前不在房间中")
            return
        if connection.username != room.owner:
            self._error(connection, "只有房主可以解散房间")
            return
        members = list(room.usernames())
        self.rooms.pop(room.id, None)
        # 解散后让所有大厅客户端立即看到删除结果，而不只是房间成员。
        self._broadcast_rooms()
        for username in members:
            session = self.users.get(username.casefold())
            if session is not None and session.connection is not None:
                target = session.connection
                target.send(message("notice", text="房主已解散房间"))
                target.send(message("left"))
                self._send_rooms(target)

    def _set_input(self, connection: ClientConnection, data: dict[str, Any]) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None:
            return
        room.set_input(
            connection.username,
            self._as_int(data.get("seq")),
            self._as_float(data.get("move_x", 0)),
            self._as_float(data.get("move_y", 0)),
            self._as_float(data.get("angle", 0)),
            bool(data.get("shoot", False)),
            str(data.get("weapon", "pistol")),
        )

    def _chat(self, connection: ClientConnection, text: str) -> None:
        assert connection.username is not None
        room = self._room_for_user(connection.username)
        if room is None:
            self._error(connection, "加入房间后才能聊天")
            return
        if not text:
            return
        if len(text) > 200 or any(ord(char) < 32 for char in text):
            self._error(connection, "聊天内容应为 1～200 个可见字符")
            return
        self._send_to_room(
            room,
            message(
                "chat",
                room_id=room.id,
                username=connection.username,
                text=text,
                timestamp=time.time(),
                system=False,
            ),
        )

    def _commit_result(self, room: Room, result: list[dict[str, int | str]]) -> None:
        if not result:
            return
        self.database.record_match(result)
        self._send_to_room(room, message("game_over", room_id=room.id, results=result))
        leaderboard = message("leaderboard", entries=self.database.leaderboard())
        for connection in list(self.connections):
            if connection.username is not None:
                connection.send(leaderboard)

    def _room_for_user(self, username: str) -> Room | None:
        return next((room for room in self.rooms.values() if room.has_user(username)), None)

    def _send_to_room(self, room: Room, outgoing: dict[str, Any]) -> None:
        for username in room.usernames():
            session = self.users.get(username.casefold())
            if session is not None and session.connection is not None:
                session.connection.send(outgoing)

    def _broadcast_room_state(self, room: Room) -> None:
        self._send_to_room(room, message("room_state", room=room.room_state()))
        self._broadcast_rooms()

    def _broadcast_rooms(self) -> None:
        rooms_message = message("rooms", rooms=[r.room_info() for r in self.rooms.values()])
        for connection in list(self.connections):
            if connection.username is not None:
                connection.send(rooms_message)

    def _send_rooms(self, connection: ClientConnection) -> None:
        connection.send(message("rooms", rooms=[room.room_info() for room in self.rooms.values()]))

    @staticmethod
    def _error(connection: ClientConnection, text: str, code: str = "request_error") -> None:
        connection.send(message("error", code=code, text=text))

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
        if isinstance(value, bool):
            raise ValueError("不能将布尔值作为数值")
        return float(value)
