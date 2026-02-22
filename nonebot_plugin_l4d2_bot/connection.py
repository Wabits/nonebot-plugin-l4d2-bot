from __future__ import annotations

import asyncio
import time

from nonebot.log import logger
from .protocol import BridgePacket


class GameServerConn:
    __slots__ = ("server_id", "ws", "connected_at", "last_ping",
                 "authenticated", "_send_lock")

    def __init__(self, server_id: str, ws):
        self.server_id = server_id
        self.ws = ws
        self.connected_at = time.time()
        self.last_ping = time.time()
        self.authenticated = False
        self._send_lock = asyncio.Lock()

    async def send_packet(self, pkt: BridgePacket):
        async with self._send_lock:
            try:
                await self.ws.send_text(pkt.model_dump_json())
            except Exception as e:
                logger.error(f"发送数据包到 {self.server_id} 失败: {e}")
                raise

    @property
    def alive_seconds(self) -> float:
        return time.time() - self.connected_at


class ConnectionManager:

    def __init__(self):
        self._conns: dict[str, GameServerConn] = {}

    def add(self, server_id: str, ws) -> GameServerConn:
        if server_id in self._conns:
            logger.warning(f"覆盖已有连接: {server_id}")
        conn = GameServerConn(server_id, ws)
        self._conns[server_id] = conn
        logger.info(f"服务器已连接: {server_id}")
        return conn

    def remove(self, server_id: str):
        if self._conns.pop(server_id, None) is not None:
            logger.info(f"服务器已断开: {server_id}")

    def get(self, server_id: str) -> GameServerConn | None:
        return self._conns.get(server_id)

    def __len__(self) -> int:
        return len(self._conns)

    def __contains__(self, server_id: str) -> bool:
        return server_id in self._conns

    @property
    def count(self) -> int:
        return len(self._conns)

    async def broadcast(self, pkt: BridgePacket, exclude: str = ""):
        for sid, conn in list(self._conns.items()):
            if sid == exclude or not conn.authenticated:
                continue
            try:
                await conn.send_packet(pkt)
            except Exception as e:
                logger.error(f"广播到 {sid} 失败: {e}")
                self.remove(sid)


conn_mgr = ConnectionManager()
