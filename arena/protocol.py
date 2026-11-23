"""基于 TCP 字节流的换行分隔 JSON 应用层协议。"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

MAX_MESSAGE_BYTES = 128 * 1024


class ProtocolError(ValueError):
    """收到的数据不符合协议。"""


def encode(message: dict[str, Any]) -> bytes:
    """将一条消息编码为一个 TCP 应用层数据单元。"""
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("消息必须包含字符串类型的 type 字段")
    payload = json.dumps(
        message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("消息超过长度限制")
    return payload + b"\n"


def decode(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_MESSAGE_BYTES + 1:
        raise ProtocolError("消息超过长度限制")
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("不是有效的 UTF-8 JSON 消息") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("消息必须包含字符串类型的 type 字段")
    data = message.setdefault("data", {})
    if not isinstance(data, dict):
        raise ProtocolError("data 字段必须是对象")
    return message


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """从 makefile('rb') 返回的流读取一条消息；EOF 返回 None。"""
    line = stream.readline(MAX_MESSAGE_BYTES + 2)
    if not line:
        return None
    if len(line) > MAX_MESSAGE_BYTES + 1 or not line.endswith(b"\n"):
        raise ProtocolError("消息过长或发送方未正常结束消息")
    return decode(line)


def message(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": data}
