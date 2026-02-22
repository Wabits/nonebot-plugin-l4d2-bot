from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from enum import IntEnum, StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field


class MsgType(StrEnum):
    HELLO = "greeting"
    HELLO_ACK = "welcome"
    PING = "are_you_there"
    PONG = "still_here"
    FILE_OUT = "deliver_file"
    FILE_CHUNK = "file_piece"
    FILE_IN_NOTICE = "incoming_file"
    ACK = "understood"
    RESULT = "mission_complete"
    ERROR = "something_wrong"


class ErrCode(IntEnum):
    OK = 0
    AUTH_FAILED = 1001
    INVALID_SIG = 1002
    EXPIRED = 1003
    DUPLICATE = 1004
    INVALID_PAYLOAD = 2001
    FILE_TOO_LARGE = 3001
    FILE_EXT_DENIED = 3002
    FILE_NOT_FOUND = 3003
    INTERNAL = 5000


class BridgePacket(BaseModel):
    v: int = 1
    type: MsgType
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    server_id: str = ""
    ts: int = Field(default_factory=lambda: int(time.time()))
    channel: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    sig: str = ""

    def compute_sig(self, secret: str) -> str:
        raw = f"{self.v}|{self.type}|{self.msg_id}|{self.server_id}|{self.ts}|{self.channel}"
        return hmac.new(
            secret.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()

    def sign(self, secret: str) -> Self:
        self.sig = self.compute_sig(secret)
        return self

    def verify_sig(self, secret: str) -> bool:
        expected = self.compute_sig(secret)
        return hmac.compare_digest(self.sig, expected)

    def verify_ts(self, window_sec: int = 30) -> bool:
        return abs(time.time() - self.ts) <= window_sec


def make_hello_ack(server_id: str, secret: str) -> BridgePacket:
    return BridgePacket(
        type=MsgType.HELLO_ACK,
        server_id="bridge",
        payload={"accepted_server": server_id},
    ).sign(secret)


def make_pong(secret: str) -> BridgePacket:
    return BridgePacket(type=MsgType.PONG, server_id="bridge").sign(secret)


def make_file_in_notice(
        channel: str, file_name: str, secret: str,
        url: str = "", file_id: str = "",
        size: int = 0, sha256: str = "") -> BridgePacket:
    payload = {"file_name": file_name}
    if url:
        payload["url"] = url
    if file_id:
        payload["file_id"] = file_id
    if size > 0:
        payload["size"] = size
    if sha256:
        payload["sha256"] = sha256
    return BridgePacket(
        type=MsgType.FILE_IN_NOTICE,
        server_id="bridge",
        channel=channel,
        payload=payload,
    ).sign(secret)


def make_ack(ref_msg_id: str, secret: str) -> BridgePacket:
    return BridgePacket(
        type=MsgType.ACK,
        server_id="bridge",
        payload={"ref_msg_id": ref_msg_id},
    ).sign(secret)


def make_error(
        code: ErrCode, msg: str, secret: str,
        ref_msg_id: str = "") -> BridgePacket:
    return BridgePacket(
        type=MsgType.ERROR,
        server_id="bridge",
        payload={"code": code.value, "message": msg, "ref_msg_id": ref_msg_id},
    ).sign(secret)


class DedupWindow:
    __slots__ = ("_window", "_seen")

    def __init__(self, window_sec: int = 600):
        self._window = window_sec
        self._seen: dict[str, float] = {}

    def is_dup(self, msg_id: str) -> bool:
        now = time.time()
        self._seen = {
            k: t for k, t in self._seen.items()
            if now - t <= self._window
        }
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = now
        return False
