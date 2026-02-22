from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiofiles
from nonebot import get_driver
from nonebot.drivers import ASGIMixin, HTTPServerSetup, Request, Response
from starlette.requests import URL

from .config import get_config
from nonebot.log import logger

_FILE_ID_RE = re.compile(r'^[0-9a-f]{1,32}$')
_MAX_REGISTRY_SIZE = 500
_REGISTRY_TTL = 3600

_file_registry: dict[str, dict] = {}


def get_file_meta(file_id: str) -> dict | None:
    return _file_registry.get(file_id)


def register_file(file_id: str, meta: dict):
    meta["_registered_at"] = time.time()
    _file_registry[file_id] = meta
    _cleanup_registry()


def _cleanup_registry():
    if len(_file_registry) <= _MAX_REGISTRY_SIZE:
        return
    now = time.time()
    expired = [k for k, v in _file_registry.items()
               if now - v.get("_registered_at", 0) > _REGISTRY_TTL]
    for k in expired:
        del _file_registry[k]
    if len(_file_registry) > _MAX_REGISTRY_SIZE:
        oldest = sorted(_file_registry, key=lambda k: _file_registry[k].get("_registered_at", 0))
        for k in oldest[:len(_file_registry) - _MAX_REGISTRY_SIZE]:
            del _file_registry[k]


def setup_http_server():
    cfg = get_config()
    driver = get_driver()
    if not isinstance(driver, ASGIMixin):
        logger.error(f"当前驱动不支持 ASGI，无法挂载 HTTP 端点")
        return

    base = cfg.l4d2_bot_file_path
    for method, suffix, name, handler in (
        ("POST", "/upload",   "l4d2_bot_upload",    _handle_upload),
        ("GET",  "/download", "l4d2_bot_download",  _handle_download),
        ("GET",  "/list",     "l4d2_bot_file_list", _handle_list),
    ):
        driver.setup_http_server(HTTPServerSetup(
            path=URL(f"{base}{suffix}"), method=method,
            name=name, handle_func=handler))

    logger.info(f"HTTP 文件端点已挂载: {base}")


def _check_auth(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return hmac.compare_digest(auth[7:], get_config().l4d2_bot_token)
    qs = parse_qs(urlparse(str(request.url)).query)
    token = qs.get("token", [""])[0]
    if token:
        return hmac.compare_digest(token, get_config().l4d2_bot_token)
    return False


def _check_extension(filename: str) -> bool:
    ext = Path(filename).suffix.lstrip(".").lower()
    return ext in get_config().l4d2_bot_allowed_extensions


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', '_', name)
    while '..' in name:
        name = name.replace('..', '')
    name = name.strip('. ')
    return name or "unnamed"


def _json_resp(status: int, data: dict) -> Response:
    return Response(status, content=json.dumps(data))


def _parse_multipart(body: bytes, content_type: str) -> tuple[str, bytes] | None:
    m = re.search(r'boundary=([^\s;]+)', content_type)
    if not m:
        return None
    boundary = m.group(1).encode()
    if boundary.startswith(b'"') and boundary.endswith(b'"'):
        boundary = boundary[1:-1]

    delimiter = b"--" + boundary
    parts = body.split(delimiter)

    for part in parts:
        if not part or part == b"--\r\n" or part == b"--":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="replace")
        file_data = part[header_end + 4:]
        if file_data.endswith(b"\r\n"):
            file_data = file_data[:-2]

        if "filename=" not in headers_raw:
            continue

        fn_match = re.search(r'filename="([^"]*)"', headers_raw)
        if not fn_match:
            fn_match = re.search(r"filename=(\S+)", headers_raw)
        filename = fn_match.group(1) if fn_match else "unnamed"
        return filename, file_data

    return None


async def _handle_upload(request: Request) -> Response:
    cfg = get_config()

    if not _check_auth(request):
        return _json_resp(401, {"error": "unauthorized"})

    body = request.content
    if not body:
        return _json_resp(400, {"error": "empty body"})

    ct = request.headers.get("content-type", "")

    if "multipart/form-data" in ct:
        parsed = _parse_multipart(body, ct)
        if not parsed:
            return _json_resp(400, {"error": "failed to parse multipart"})
        filename, content = parsed
    else:
        filename = request.headers.get("x-file-name", "unnamed")
        content = body

    filename = safe_filename(filename)

    if not _check_extension(filename):
        return _json_resp(403, {
            "error": f"extension not allowed: {Path(filename).suffix}",
            "allowed": cfg.l4d2_bot_allowed_extensions})

    max_bytes = cfg.l4d2_bot_upload_max_mb * 1024 * 1024
    if len(content) > max_bytes:
        return _json_resp(413, {"error": f"file too large: {len(content)} > {max_bytes}"})

    sha256 = hashlib.sha256(content).hexdigest()
    file_id = uuid.uuid4().hex[:16]
    save_path = cfg.upload_path / filename

    async with aiofiles.open(save_path, "wb") as f:
        await f.write(content)

    meta = {
        "file_id": file_id, "file_name": filename,
        "size": len(content), "sha256": sha256,
        "path": str(save_path),
    }
    register_file(file_id, meta)
    logger.info(f"文件已上传: {filename} ({len(content)} bytes, id={file_id})")

    return Response(200, content=json.dumps(meta))


async def _handle_download(request: Request) -> Response:
    cfg = get_config()

    if not _check_auth(request):
        return _json_resp(401, {"error": "unauthorized"})

    qs = parse_qs(urlparse(str(request.url)).query)
    file_id = qs.get("file_id", [""])[0]

    if not file_id:
        return _json_resp(400, {"error": "missing file_id parameter"})
    if not _FILE_ID_RE.match(file_id):
        return _json_resp(400, {"error": "invalid file_id format"})

    meta = get_file_meta(file_id)
    if not meta:
        return _json_resp(404, {"error": "file not found"})

    file_path = Path(meta["path"]).resolve()
    if not file_path.is_relative_to(cfg.upload_path.resolve()):
        return _json_resp(403, {"error": "access denied"})
    if not file_path.exists():
        return _json_resp(404, {"error": "file missing on disk"})

    async with aiofiles.open(file_path, "rb") as f:
        content = await f.read()

    safe_name = safe_filename(meta["file_name"])
    resp = Response(200, headers={
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "Content-Type": "application/octet-stream",
        "X-File-SHA256": meta["sha256"],
    }, content=content)

    try:
        file_path.unlink(missing_ok=True)
        _file_registry.pop(file_id, None)
        logger.info(f"文件已清理: {safe_name} (id={file_id})")
    except Exception as e:
        logger.warning(f"文件清理失败: {e}")

    return resp


async def _handle_list(request: Request) -> Response:
    if not _check_auth(request):
        return _json_resp(401, {"error": "unauthorized"})

    file_list = [
        {"file_id": fid, "file_name": m.get("file_name", ""),
         "size": m.get("size", 0), "sha256": m.get("sha256", "")}
        for fid, m in _file_registry.items()
    ]
    return Response(200, content=json.dumps({"files": file_list}))
