import functools

from nonebot import get_driver, get_plugin_config
from nonebot.plugin import PluginMetadata

from .config import BridgeConfig, init_config
from .ws_server import setup_ws_server
from .http_server import setup_http_server
from .forwarder import setup_forwarder

__plugin_meta__ = PluginMetadata(
    name="L4D2 Bot",
    description="求生之路2 SourceMod 文件传输桥接插件",
    usage="自动运行，服务端无需手动命令\n"
          "群内发送 .vpk 文件或者 下载 <URL> 即可推送到服务器",
    type="application",
    homepage="https://github.com/Wabits/nonebot-plugin-l4d2-bot",
    config=BridgeConfig,
    supported_adapters={"~onebot.v11"},
)

driver = get_driver()
_config = get_plugin_config(BridgeConfig)

_original_run = driver.run


@functools.wraps(_original_run)
def _patched_run(*args, **kwargs):
    kwargs.setdefault("ws_max_size", _config.l4d2_bot_ws_max_size)
    _original_run(*args, **kwargs)


driver.run = _patched_run


@driver.on_startup
async def _startup():
    init_config(_config)
    setup_ws_server()
    setup_http_server()
    setup_forwarder()
