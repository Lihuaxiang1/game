"""Tk 主线程之外运行的 TCP 客户端连接。"""

from __future__ import annotations

import queue
import socket
import threading
from typing import Any

from .protocol import ProtocolError, encode, message, read_message


class NetworkClient:
    def __init__(self):
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._socket: socket.socket | None = None
        self._stream: Any = None
        self._reader: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._generation = 0

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self, host: str, port: int) -> None:
        if self.connected:
            return
        sock = socket.create_connection((host, port), timeout=3.0)
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._generation += 1
        generation = self._generation
        self._socket = sock
        self._stream = sock.makefile("rb")
        self._reader = threading.Thread(
            target=self._read_loop,
            args=(sock, self._stream, generation),
            daemon=True,
            name="network-reader",
        )
        self._reader.start()

    def send(self, kind: str, **data: Any) -> None:
        sock = self._socket
        if sock is None:
            raise ConnectionError("尚未连接服务器")
        try:
            with self._send_lock:
                sock.sendall(encode(message(kind, **data)))
        except OSError as exc:
            self._notify_disconnect(self._generation, str(exc))
            raise ConnectionError("连接已断开") from exc

    def _read_loop(self, sock: socket.socket, stream: Any, generation: int) -> None:
        reason = "服务器关闭了连接"
        try:
            while True:
                incoming = read_message(stream)
                if incoming is None:
                    break
                self.events.put(incoming)
        except (OSError, ProtocolError, ValueError) as exc:
            # 关闭窗口时主线程会关闭底层流，读线程可能收到 ValueError。
            reason = str(exc)
        finally:
            self._notify_disconnect(generation, reason)

    def _notify_disconnect(self, generation: int, reason: str) -> None:
        if generation != self._generation:
            return
        self._generation += 1
        sock, stream = self._socket, self._stream
        self._socket = None
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.events.put(message("_disconnected", text=reason))

    def close(self) -> None:
        self._generation += 1
        sock, stream = self._socket, self._stream
        self._socket = None
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
