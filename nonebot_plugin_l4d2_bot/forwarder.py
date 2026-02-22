from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, unquote

import aiofiles
import httpx
import nonebot
from nonebot import on_message, on_notice, Bot
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent, GroupUploadNoticeEvent, Message,
)
from nonebot.rule import Rule

from .config import get_config
from nonebot.log import logger
from .connection import conn_mgr
from .protocol import make_file_in_notice
from .ws_server import on_file_out, on_result
from .http_server import get_file_meta, safe_filename

_URL_RE = re.compile(r'https?://[^\s"<>\]\)}{,]+', re.IGNORECASE)

_FLASH_RE = re.compile(
    r'(?:'
    r'\[flashtransfer:fileSetId=([a-f0-9\-]+)\]'
    r'|'
    r'\[CQ:flashtransfer,fileSetId=([a-f0-9\-]+)\]'
    r')',
    re.IGNORECASE,
)


def _is_bridge_group() -> Rule:
    async def checker(event: GroupMessageEvent) -> bool:
        return str(event.group_id) in get_config().l4d2_bot_qq_groups
    return Rule(checker)


def _starts_with_download() -> Rule:
    async def checker(event: GroupMessageEvent) -> bool:
        text = event.get_plaintext().strip()
        return text.startswith("下载") and bool(_URL_RE.search(text))
    return Rule(checker)


def _has_flash_segment() -> Rule:
    async def checker(event: GroupMessageEvent) -> bool:
        for seg in event.message:
            if seg.type == "flashtransfer":
                return True
        return bool(_FLASH_RE.search(str(event.get_message())))
    return Rule(checker)


def setup_forwarder():
    _register_game_to_bot_handlers()

    grp = _is_bridge_group()

    on_message(rule=grp & _has_flash_segment(), priority=85, block=False).handle()(
        _handle_flash_message)
    on_message(rule=grp & _starts_with_download(), priority=10).handle()(
        _handle_download_cmd)

    upload_notice = on_notice(priority=50, block=False)
    upload_notice.handle()(_handle_group_upload)


def _filename_from_url(url: str, fallback: str = "download") -> str:
    try:
        name = PurePosixPath(unquote(urlparse(url).path)).name
        if name:
            return name
    except Exception:
        pass
    return fallback


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


async def _handle_group_upload(bot: Bot, event: GroupUploadNoticeEvent):
    cfg = get_config()
    if str(event.group_id) not in cfg.l4d2_bot_qq_groups:
        return

    if str(event.user_id) == str(bot.self_id):
        return

    file_name = event.file.name or ""
    if not file_name.lower().endswith(".vpk"):
        return

    if conn_mgr.count == 0:
        return

    try:
        resp = await bot.call_api(
            "get_group_file_url",
            group_id=event.group_id,
            file_id=event.file.id,
            busid=event.file.busid,
        )
        file_url = resp.get("url", "") if isinstance(resp, dict) else str(resp)
    except Exception as e:
        logger.error(f"获取群文件URL失败: {e}")
        return

    if not file_url:
        logger.warning(f"群文件URL为空: {file_name}")
        return

    channel = f"qq_group:{event.group_id}"
    logger.info(f"群文件上传检测: {file_name} ({event.file.size} bytes)")

    pkt = make_file_in_notice(
        channel=channel, file_name=file_name,
        secret=cfg.l4d2_bot_token, url=file_url,
        size=event.file.size or 0)
    await conn_mgr.broadcast(pkt)

    try:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=Message(f"检测到文件上传: {file_name}\n已推送至服务器"))
    except Exception:
        pass


async def _handle_flash_message(bot: Bot, event: GroupMessageEvent):
    fileset_id = ""
    for seg in event.message:
        if seg.type == "flashtransfer":
            fileset_id = seg.data.get("fileSetId", "")
            break
    if not fileset_id:
        m = _FLASH_RE.search(str(event.get_message()))
        if not m:
            return
        fileset_id = m.group(1) or m.group(2)

    logger.info(f"检测到闪传: fileSetId={fileset_id}")

    try:
        resp = await bot.call_api("get_flash_file_list", fileset_id=fileset_id)
    except Exception as e:
        logger.warning(f"获取闪传文件列表失败: {e}")
        return

    files: list[dict] = []
    if isinstance(resp, dict):
        for fl in resp.get("fileLists", []):
            for item in fl.get("fileList", []):
                name = item.get("name", "")
                if name:
                    files.append({"file_name": name, "size": int(item.get("fileSize", 0))})
    elif isinstance(resp, list):
        files = [{"file_name": f.get("file_name", ""), "size": f.get("size", 0)} for f in resp]

    vpk_files = [f for f in files if f["file_name"].lower().endswith(".vpk")]
    if not vpk_files:
        if files:
            logger.info(f"闪传无VPK文件: {[f['file_name'] for f in files]}")
        return

    if conn_mgr.count == 0:
        await bot.send(event, "检测到闪传 VPK，但没有服务器在线")
        return

    cfg = get_config()
    channel = f"qq_group:{event.group_id}"

    for f in vpk_files:
        file_name = f.get("file_name", "download.vpk")
        try:
            url_resp = await bot.call_api(
                "get_flash_file_url", fileset_id=fileset_id, file_name=file_name)
        except Exception as e:
            logger.error(f"获取闪传文件URL失败: {e}")
            await bot.send(event, f"闪传文件URL获取失败: {e}")
            continue

        file_url = ""
        if isinstance(url_resp, str):
            file_url = url_resp
        elif isinstance(url_resp, dict):
            file_url = url_resp.get("transferUrl", "") or url_resp.get("url", "")
        if not file_url:
            logger.warning(f"闪传文件URL为空: {file_name}, resp={url_resp}")
            continue

        logger.info(f"闪传链接推送: {file_name} from {_sender_name(event)}")
        pkt = make_file_in_notice(
            channel=channel, file_name=file_name,
            secret=cfg.l4d2_bot_token, url=file_url,
            size=f.get("size", 0))
        await conn_mgr.broadcast(pkt)
        await bot.send(event, f"推送服务端\n文件: {file_name}")


async def _handle_download_cmd(bot: Bot, event: GroupMessageEvent):
    raw = event.get_plaintext().strip().removeprefix("下载").strip()
    urls = _URL_RE.findall(raw)
    if not urls:
        await bot.send(event, "请提供下载链接，例如：下载 https://example.com/file.vpk")
        return

    if conn_mgr.count == 0:
        await bot.send(event, "没有服务器在线，无法推送")
        return

    cfg = get_config()
    channel = f"qq_group:{event.group_id}"

    for url in urls:
        file_name = _filename_from_url(url)
        file_size = 0
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as cli:
                resp = await cli.head(url)
                file_size = int(resp.headers.get("content-length", 0))
        except Exception:
            pass
        logger.info(f"直链推送: {file_name} from {_sender_name(event)}")
        pkt = make_file_in_notice(
            channel=channel, file_name=file_name,
            secret=cfg.l4d2_bot_token, url=url,
            size=file_size)
        await conn_mgr.broadcast(pkt)
        await bot.send(event, f"推送服务端\n文件: {file_name}")


def _register_game_to_bot_handlers():

    @on_result
    async def _download_result_to_qq(
            server_id: str, ok: bool, file_name: str,
            size_mb: str, speed: str, err_msg: str,
            extracted: str = "", extract_time: str = "", channel: str = ""):
        gids = _resolve_target_gids(channel)
        if extracted == "true":
            msg = f"解压完成\n文件: {file_name}\n耗时: {extract_time}S"
        elif ok:
            msg = f"下载完成\n文件: {file_name}\n大小: {size_mb} MB\n速率: {speed} MB/S"
        else:
            msg = f"下载失败\n文件: {file_name}\n原因: {err_msg}"
        await _broadcast_text(msg, gids)

    @on_file_out
    async def _game_file_to_qq(
            server_id: str, channel: str, file_id: str,
            file_name: str, size: int, sha256: str,
            url: str = "", data: str = ""):
        gids = _resolve_target_gids(channel)
        cfg = get_config()
        safe_name = safe_filename(file_name)
        display = cfg.display_name(server_id)

        resolved = await _resolve_file_path(url, file_id, safe_name, cfg)
        if not resolved:
            reason = "下载失败" if url else "文件不可用"
            await _broadcast_text(f"{display}上传文件: {file_name} {reason}", gids)
            return

        await _broadcast_file(resolved, file_name, gids)
        await _broadcast_text(
            f"{display}上传文件: {file_name}\n大小: {size / 1024 / 1024:.2f} MB", gids)

        try:
            Path(resolved).unlink(missing_ok=True)
            logger.info(f"文件已清理: {file_name}")
        except Exception as e:
            logger.warning(f"文件清理失败: {e}")


async def _resolve_file_path(
        url: str, file_id: str, safe_name: str, cfg) -> str | None:
    if url:
        save_path = cfg.upload_path / safe_name
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async with aiofiles.open(save_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            await f.write(chunk)
            logger.info(f"文件已下载: {save_path}")
            return str(save_path.resolve())
        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            return None

    meta = get_file_meta(file_id)
    if not meta or not Path(meta["path"]).exists():
        logger.warning(f"文件未找到: {file_id}")
        return None
    return str(Path(meta["path"]).resolve())


def _resolve_target_gids(channel: str | None) -> list[str]:
    cfg = get_config()
    if channel and channel.startswith("qq_group:"):
        gid = channel.removeprefix("qq_group:").strip()
        if gid.isdigit() and gid in cfg.l4d2_bot_qq_groups:
            return [gid]
    return list(cfg.l4d2_bot_qq_groups)


def _get_bot() -> Bot | None:
    try:
        return nonebot.get_bot()
    except ValueError:
        return None


async def _broadcast_text(text: str, gids: list[str]):
    bot = _get_bot()
    if not bot:
        return
    for gid in gids:
        try:
            await bot.send_group_msg(group_id=int(gid), message=Message(text))
        except Exception as e:
            logger.error(f"发送到群 {gid} 失败: {e}")


async def _broadcast_file(file_path: str, file_name: str, gids: list[str]):
    bot = _get_bot()
    if not bot:
        return
    for gid in gids:
        try:
            await bot.call_api(
                "upload_group_file", group_id=int(gid),
                file=file_path, name=file_name)
        except Exception as e:
            logger.error(f"发送文件到群 {gid} 失败: {e}")
