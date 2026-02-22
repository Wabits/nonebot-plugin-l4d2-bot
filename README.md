<div align="center">

# nonebot-plugin-l4d2-bot

_✨ 求生之路2 SourceMod 文件传输桥接插件 ✨_

连接 QQ 群与 L4D2 游戏服务器，实现双向文件传输

[![license](https://img.shields.io/github/license/Wabits/nonebot-plugin-l4d2-bot?style=flat-square)](https://github.com/Wabits/nonebot-plugin-l4d2-bot/blob/master/LICENSE)
[![python](https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=edb641)](https://www.python.org/)
[![nonebot](https://img.shields.io/badge/nonebot-2.3.0+-ea5252?style=flat-square)](https://nonebot.dev/)
[![onebot](https://img.shields.io/badge/OneBot-v11-black?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA4AAAAOCAYAAAAfSC3RAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAADFSURBVDhPY/hPIGAB)](https://onebot.adapters.nonebot.dev/)

</div>

---

## 功能特性

### QQ 群 → 游戏服务器

| 功能 | 触发方式 | 说明 |
|:---|:---|:---|
| VPK 文件推送 | 在群内直接发送 `.vpk` 文件 | 自动检测并转发至所有在线服务器 |
| 直链下载推送 | 发送 `下载 <URL>` | 支持任意 HTTP 直链，自动获取文件名和大小 |
| 闪传文件转发 | 自动触发 | 检测闪传中的 VPK 文件并推送 |

### 游戏服务器 → QQ 群

| 功能 | 说明 |
|:---|:---|
| 文件上传通知 | 服务器上传文件时自动通知已配置的 QQ 群 |
| 分块传输 | 大文件自动分块传输，支持 SHA256 完整性校验 |
| 上传结果反馈 | 上传成功 / 失败结果同步到群内 |

### 安全机制

- **HMAC 签名认证** — 所有 WebSocket 数据包均经过 HMAC-SHA256 签名校验
- **时间戳防重放** — 可配置时间窗口，过期数据包自动拒绝
- **消息去重** — 滑动窗口去重，防止重复处理
- **心跳保活** — 自动检测断线并清理失效连接

---

## 安装

### Bot 端（NoneBot2 插件）

<details open>
<summary>pip</summary>

```bash
pip install nonebot-plugin-l4d2-bot
```

</details>

<details>
<summary>nb-cli</summary>

```bash
nb plugin install nonebot-plugin-l4d2-bot
```

</details>

<details>
<summary>pdm</summary>

```bash
pdm add nonebot-plugin-l4d2-bot
```

</details>

<details>
<summary>uv</summary>

```bash
uv add nonebot-plugin-l4d2-bot
```

</details>

<details>
<summary>poetry</summary>

```bash
poetry add nonebot-plugin-l4d2-bot
```

</details>

### 游戏服务端（SourceMod）

将仓库中 `left4dead2/` 目录下的文件复制到游戏服务器对应路径即可：

```
left4dead2/
├── addons/sourcemod/
│   ├── plugins/Lybot-bridge.smx           # 编译后的 SourceMod 插件
│   ├── extensions/
│   │   ├── lybot.ext.so                   # Linux 扩展（WebSocket 客户端）
│   │   └── lybot.ext.dll                  # Windows 扩展
│   └── scripting/
│       ├── Lybot-bridge.sp                # 插件源码（可自行编译修改）
│       └── include/lybot.inc              # Native 头文件
└── cfg/sourcemod/
    └── lybot_bridge.cfg                   # 连接配置（首次加载自动生成）
```

---

## 配置

### Bot 端配置（`.env`）

在 NoneBot2 项目的 `.env` 文件中添加：

```env
# ========== 必填 ==========
L4D2_BOT_TOKEN=your_secure_token          # 认证 Token（Bot 端与服务端必须一致）
L4D2_BOT_WS_MAX_SIZE=8388608              # WebSocket 最大消息大小（字节），默认 8MB
L4D2_BOT_QQ_GROUPS=["123456789"]          # 监听的 QQ 群号列表

# ========== 可选（下方均为默认值，按需修改） ==========
L4D2_BOT_WS_PATH=/ws/l4d2                 # WebSocket 路由路径
L4D2_BOT_FILE_PATH=/v1/files              # HTTP 文件接口路由前缀
L4D2_BOT_HEARTBEAT_INTERVAL=15            # 心跳检测间隔（秒），超过 3 倍间隔无响应则断开
L4D2_BOT_UPLOAD_MAX_MB=10240              # 单文件上传大小上限（MB）
L4D2_BOT_UPLOAD_DIR=data/Document         # 文件保存目录（相对于 Bot 工作目录）
L4D2_BOT_ALLOWED_EXTENSIONS=["vpk"]       # 允许转发的文件扩展名白名单
L4D2_BOT_SERVER_NAMES={"server-1": "1服"} # 服务器 ID → 群内显示名称映射
L4D2_BOT_HMAC_WINDOW_SEC=30               # HMAC 时间戳容差窗口（秒）
L4D2_BOT_MSG_DEDUP_WINDOW_SEC=600         # 消息 ID 去重窗口（秒）
```

### 游戏服务端配置（`lybot_bridge.cfg`）

首次加载插件后会在 `cfg/sourcemod/` 目录自动生成配置文件，也可手动编辑：

```cfg
// Bot 端 WebSocket 地址（注意路径需与 L4D2_BOT_WS_PATH 一致）
lybot_ws_url       "ws://你的Bot地址:7590/ws/l4d2"

// Bot 端 HTTP 地址（用于文件上传下载）
lybot_http_url     "http://你的Bot地址:7590"

// 认证令牌（必须与 Bot 端 .env 中 L4D2_BOT_TOKEN 完全一致）
lybot_token        "your_secure_token"

// 服务器标识（多服务器部署时用于区分来源，对应 L4D2_BOT_SERVER_NAMES 的 key）
lybot_server_id    "server-1"

// 插件加载时是否自动连接（1=启用 0=禁用）
lybot_auto_connect "1"
```

---

## 服务端控制台命令

以下命令需要 **ROOT** 管理员权限：

| 命令 | 说明 |
|:---|:---|
| `sm_lybot_connect` | 手动连接到 Bot 服务器 |
| `sm_lybot_disconnect` | 断开当前连接 |
| `sm_lybot_status` | 查看连接状态与服务器标识 |
| `sm_lybot_listfiles` | 列出 `addons/` 目录下所有 VPK 文件 |
| `sm_lybot_sendfile <编号/文件名> [群号] [说明]` | 上传指定文件到 QQ 群 |

**使用示例：**

```
// 列出可上传的文件
sm_lybot_listfiles

// 按编号上传（编号来自 listfiles）
sm_lybot_sendfile 1

// 按编号上传到指定群
sm_lybot_sendfile 1 123456789
```

---

## 许可

本项目使用 [MIT](./LICENSE) 许可证
