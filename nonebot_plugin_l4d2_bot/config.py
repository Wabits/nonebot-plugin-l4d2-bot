from pathlib import Path

from pydantic import BaseModel, Field, PrivateAttr


class BridgeConfig(BaseModel):
    l4d2_bot_token: str = "change_me_to_a_secure_token"
    l4d2_bot_ws_path: str = "/ws/l4d2"
    l4d2_bot_file_path: str = "/v1/files"
    l4d2_bot_heartbeat_interval: int = 15
    l4d2_bot_upload_max_mb: int = 10240
    l4d2_bot_upload_dir: str = "data/Document"
    l4d2_bot_allowed_extensions: list[str] = Field(
        default_factory=lambda: ["vpk"]
    )
    l4d2_bot_qq_groups: list[str] = Field(default_factory=lambda: [])
    l4d2_bot_server_names: dict[str, str] = Field(default_factory=dict)
    l4d2_bot_hmac_window_sec: int = 30
    l4d2_bot_msg_dedup_window_sec: int = 600
    l4d2_bot_ws_max_size: int = 8 * 1024 * 1024

    def display_name(self, server_id: str) -> str:
        return self.l4d2_bot_server_names.get(server_id, server_id)

    _ul_path: Path | None = PrivateAttr(default=None)

    @property
    def upload_path(self) -> Path:
        if self._ul_path is None:
            p = Path(self.l4d2_bot_upload_dir)
            p.mkdir(parents=True, exist_ok=True)
            self._ul_path = p
        return self._ul_path


_config: BridgeConfig | None = None


def init_config(config: BridgeConfig):
    global _config
    _config = config


def get_config() -> BridgeConfig:
    if _config is None:
        raise RuntimeError("BridgeConfig not initialized, call init_config() first")
    return _config

