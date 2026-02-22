from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import nonebot
from nonebot import get_driver
from nonebot.adapters.onebot.v11 import Message
from nonebot.drivers import ASGIMixin, WebSocketServerSetup, WebSocket
from starlette.requests import URL

from .config import get_config
from nonebot.log import logger
from .connection import conn_mgr
from .protocol import (
    BridgePacket, MsgType, ErrCode, DedupWindow,
    make_hello_ack, make_pong, make_ack, make_error,
)
from .http_server import register_file, safe_filename

_MAX_CONNECTIONS = 16
_SERVER_ID_RE = re.compile(r'^[\w.\-]{1,64}$')

_dedup = DedupWindow()

FileOutHandler = Callable[..., Awaitable[None]]
ResultHandler = Callable[..., Awaitable[None]]

_file_out_handlers: list[FileOutHandler] = []
_result_handlers: list[ResultHandler] = []


def on_file_out(func: FileOutHandler) -> FileOutHandler:
    _file_out_handlers.append(func)
    return func


def on_result(func: ResultHandler) -> ResultHandler:
    _result_handlers.append(func)
    return func


@dataclass(slots=True)
class _ChunkedTransfer:
    total_chunks: int
    file_name: str
    total_size: int
    sha256: str
    channel: str
    received: set[int] = field(default_factory=set)
    data: bytearray = field(default_factory=bytearray)
    created_at: float = field(default_factory=time.time)

_transfers: dict[str, _ChunkedTransfer] = {}
_TRANSFER_TIMEOUT = 300


def _cleanup_stale_transfers():
    now = time.time()
    for tid in [
        k for k, v in _transfers.items()
        if now - v.created_at > _TRANSFER_TIMEOUT
    ]:
        logger.warning(f"清理超时分块传输: {tid}")
        del _transfers[tid]


def setup_ws_server():
    cfg = get_config()
    _dedup._window = cfg.l4d2_bot_msg_dedup_window_sec

    driver = get_driver()
    if not isinstance(driver, ASGIMixin):
        logger.error(f"当前驱动不支持 ASGI，无法挂载 WebSocket")
        return

    ws_setup = WebSocketServerSetup(
        path=URL(cfg.l4d2_bot_ws_path),
        name="l4d2_bot_ws",
        handle_func=_handle_ws,
    )
    driver.setup_websocket_server(ws_setup)
    logger.info(f"WebSocket 服务已挂载: {cfg.l4d2_bot_ws_path}")


async def _handle_ws(ws: WebSocket):
    if conn_mgr.count >= _MAX_CONNECTIONS:
        logger.warning(f"连接数已达上限 ({_MAX_CONNECTIONS})，拒绝新连接")
        await ws.accept()
        await ws.close()
        return

    await ws.accept()
    server_id = ""
    try:
        server_id = await _do_handshake(ws)
        if not server_id:
            return

        conn = conn_mgr.add(server_id, ws)
        conn.authenticated = True

        heartbeat_task = asyncio.create_task(_heartbeat_monitor(server_id))
        try:
            await _message_loop(ws, server_id)
        finally:
            heartbeat_task.cancel()
            conn_mgr.remove(server_id)
    except Exception as e:
        logger.error(f"WebSocket 异常 ({server_id}): {e}")
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


async def _do_handshake(ws: WebSocket) -> str:
    cfg = get_config()
    token = cfg.l4d2_bot_token

    try:
        data = await asyncio.wait_for(ws.receive(), timeout=10)
        raw = data if isinstance(data, str) else data.decode("utf-8")
    except asyncio.TimeoutError:
        logger.warning(f"握手超时")
        return ""

    try:
        pkt = BridgePacket.model_validate_json(raw)
    except Exception:
        logger.warning(f"握手包解析失败")
        return ""

    if pkt.type != MsgType.HELLO:
        await _send(ws, make_error(ErrCode.AUTH_FAILED, "期望 hello", token))
        return ""

    token_in = pkt.payload.get("token", "")
    if not hmac.compare_digest(token_in, token):
        await _send(ws, make_error(ErrCode.AUTH_FAILED, "token 错误", token))
        return ""

    if not pkt.verify_sig(token):
        await _send(ws, make_error(ErrCode.INVALID_SIG, "签名校验失败", token))
        return ""

    if not pkt.verify_ts(cfg.l4d2_bot_hmac_window_sec):
        await _send(ws, make_error(ErrCode.EXPIRED, "时间戳过期", token))
        return ""

    server_id = pkt.server_id or "unknown"
    if not _SERVER_ID_RE.match(server_id):
        server_id = re.sub(r'[^\w.\-]', '_', server_id)[:64] or "unknown"

    await _send(ws, make_hello_ack(server_id, token))
    logger.info(f"认证成功: {server_id}")
    return server_id


async def _message_loop(ws: WebSocket, server_id: str):
    token = get_config().l4d2_bot_token

    while True:
        try:
            data = await ws.receive()
            raw = data if isinstance(data, str) else data.decode("utf-8")
        except Exception:
            logger.info(f"连接关闭: {server_id}")
            break

        try:
            pkt = BridgePacket.model_validate_json(raw)
        except Exception as e:
            logger.warning(f"数据包解析失败 ({server_id}): {e}")
            continue

        if not pkt.verify_sig(token):
            await _send(ws, make_error(
                ErrCode.INVALID_SIG, "签名错误", token, pkt.msg_id))
            continue

        if pkt.type not in (MsgType.PING, MsgType.ACK) and _dedup.is_dup(pkt.msg_id):
            logger.debug(f"重复消息已跳过: {pkt.msg_id}")
            await _send(ws, make_ack(pkt.msg_id, token))
            continue

        conn = conn_mgr.get(server_id)
        if conn:
            conn.last_ping = time.time()

        await _dispatch(ws, server_id, pkt)


async def _dispatch(ws: WebSocket, server_id: str, pkt: BridgePacket):
    token = get_config().l4d2_bot_token
    p = pkt.payload

    match pkt.type:
        case MsgType.PING:
            await _send(ws, make_pong(token))

        case MsgType.FILE_OUT:
            await _send(ws, make_ack(pkt.msg_id, token))
            await _invoke_handlers(_file_out_handlers,
                server_id, pkt.channel, p.get("file_id", ""),
                p.get("file_name", ""), int(p.get("size", 0)),
                p.get("sha256", ""), p.get("url", ""), p.get("data", ""))

        case MsgType.FILE_CHUNK:
            await _send(ws, make_ack(pkt.msg_id, token))
            await _handle_file_chunk(server_id, pkt.channel, p)

        case MsgType.RESULT:
            await _invoke_handlers(_result_handlers,
                server_id,
                str(p.get("ok", "false")) in ("true", "1"),
                p.get("file_name", ""),
                str(p.get("size_mb", "")),
                str(p.get("speed", "")),
                p.get("err_msg", ""),
                p.get("extracted", ""),
                str(p.get("extract_time", "")),
                pkt.channel or "")

        case MsgType.ACK:
            logger.debug(f"收到 ACK ({server_id}): {p.get('ref_msg_id')}")

        case _:
            logger.warning(f"未知消息类型: {pkt.type} ({server_id})")


async def _invoke_handlers(handlers: list, *args):
    for handler in handlers:
        try:
            await handler(*args)
        except Exception as e:
            logger.error(f"处理器异常: {e}")


async def _handle_file_chunk(
        server_id: str, channel: str, p: dict):
    _cleanup_stale_transfers()
    transfer_id  = p.get("transfer_id", "")
    file_name    = p.get("file_name", "")
    total_size   = int(p.get("total_size", 0))
    sha256       = p.get("sha256", "")
    chunk_index  = int(p.get("chunk_index", 0))
    total_chunks = int(p.get("total_chunks", 1))
    chunk_data   = p.get("data", "")

    if transfer_id not in _transfers:
        _transfers[transfer_id] = _ChunkedTransfer(
            total_chunks, file_name, total_size, sha256, channel)
        logger.info(f"开始接收分块文件: {file_name} "
                   f"({total_size} bytes, {total_chunks} 块)")
        await _notify_upload_start(server_id, channel, file_name)

    xfer = _transfers[transfer_id]

    if chunk_index not in xfer.received:
        try:
            xfer.data.extend(base64.b64decode(chunk_data))
            xfer.received.add(chunk_index)
        except Exception as e:
            logger.error(f"分块解码失败 ({transfer_id} #{chunk_index}): {e}")

    logger.debug(f"收到分块 {chunk_index + 1}/{total_chunks} "
                f"({transfer_id})")

    if len(xfer.received) < xfer.total_chunks:
        return

    logger.info(f"分块接收完成: {xfer.file_name} "
               f"({len(xfer.data)} bytes)")
    actual_sha = hashlib.sha256(xfer.data).hexdigest()
    if xfer.sha256 and actual_sha != xfer.sha256:
        logger.warning(f"SHA256 不匹配: "
                      f"期望={xfer.sha256} 实际={actual_sha}")

    cfg = get_config()
    safe_name = safe_filename(xfer.file_name)
    save_path = cfg.upload_path / (safe_name or "upload")
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(xfer.data)
    logger.info(f"文件已保存: {save_path}")

    register_file(transfer_id, {
        "file_id": transfer_id, "file_name": xfer.file_name,
        "size": len(xfer.data), "sha256": actual_sha,
        "path": str(save_path.resolve()),
    })

    await _invoke_handlers(_file_out_handlers,
        server_id, xfer.channel, transfer_id,
        xfer.file_name, xfer.total_size, xfer.sha256, "", "")

    del _transfers[transfer_id]


async def _heartbeat_monitor(server_id: str):
    cfg = get_config()
    timeout = cfg.l4d2_bot_heartbeat_interval * 3
    while True:
        await asyncio.sleep(cfg.l4d2_bot_heartbeat_interval)
        conn = conn_mgr.get(server_id)
        if not conn:
            break
        if time.time() - conn.last_ping > timeout:
            logger.warning(f"心跳超时，断开连接: {server_id}")
            with contextlib.suppress(Exception):
                await conn.ws.close()
            conn_mgr.remove(server_id)
            break


async def _send(ws: WebSocket, pkt: BridgePacket):
    try:
        await ws.send_text(pkt.model_dump_json())
    except Exception as e:
        logger.error(f"发送失败: {e}")


async def _notify_upload_start(
        server_id: str, channel: str, file_name: str):
    try:
        bot = nonebot.get_bot()
    except ValueError:
        return

    cfg = get_config()
    if channel and channel.startswith("qq_group:"):
        gid = channel.removeprefix("qq_group:").strip()
        gids = [gid] if gid and gid in cfg.l4d2_bot_qq_groups else []
    else:
        gids = list(cfg.l4d2_bot_qq_groups)

    msg = f"检测到服务器上传文件: {file_name}"
    for gid in gids:
        try:
            await bot.send_group_msg(group_id=int(gid), message=Message(msg))
        except Exception as e:
            logger.error(f"通知群 {gid} 失败: {e}")
