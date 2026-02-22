#include <sourcemod>
#include <lybot>

#pragma semicolon 1
#pragma newdecls required

#define LYBOT_VERSION "1.0.2"
#define MAX_VPK_LIST 128
#define ALLOWED_EXT_COUNT 3

ConVar g_cvWsUrl;
ConVar g_cvHttpUrl;
ConVar g_cvToken;
ConVar g_cvServerId;

char g_vpkList[MAX_VPK_LIST][PLATFORM_MAX_PATH];
int  g_vpkCount;

public Plugin myinfo =
{
    name        = "Lybot-bridge",
    author      = "落樱",
    description = "L4D2 <-> NoneBot2 文件桥接服务",
    version     = LYBOT_VERSION,
    url         = ""
};

public void OnPluginStart()
{
    g_cvWsUrl       = CreateConVar("lybot_ws_url",       "",           "服务器 WebSocket 地址");
    g_cvHttpUrl     = CreateConVar("lybot_http_url",     "",           "服务器 HTTP 地址");
    g_cvToken       = CreateConVar("lybot_token",        "",           "服务器认证令牌", FCVAR_PROTECTED);
    g_cvServerId    = CreateConVar("lybot_server_id",    "",           "服务器标识");
    CreateConVar("lybot_auto_connect", "1", "启动时自动连接", _, true, 0.0, true, 1.0);

    RegAdminCmd("sm_lybot_connect",    Cmd_Connect,    ADMFLAG_ROOT, "连接 LyBot 服务器");
    RegAdminCmd("sm_lybot_disconnect", Cmd_Disconnect, ADMFLAG_ROOT, "断开 LyBot 服务器");
    RegAdminCmd("sm_lybot_status",     Cmd_Status,     ADMFLAG_ROOT, "查看 LyBot 服务器状态");
    RegAdminCmd("sm_lybot_listfiles",  Cmd_ListFiles,  ADMFLAG_ROOT, "列出 addons 目录 VPK 文件");
    RegAdminCmd("sm_lybot_sendfile",   Cmd_SendFile,   ADMFLAG_ROOT, "上传文件到QQ群 (编号/文件名)");

    AutoExecConfig(true, "lybot_bridge");
}

public void OnPluginEnd()
{
    if (NB_IsConnected())
        NB_Disconnect();
}

public Action Cmd_Connect(int client, int args)
{
    if (NB_IsConnected())
    {
        ReplyToCommand(client, "[LyBot] 已连接，请先断开");
        return Plugin_Handled;
    }

    char wsUrl[256], httpUrl[256], token[128], serverId[64];
    g_cvWsUrl.GetString(wsUrl, sizeof(wsUrl));
    g_cvHttpUrl.GetString(httpUrl, sizeof(httpUrl));
    g_cvToken.GetString(token, sizeof(token));
    g_cvServerId.GetString(serverId, sizeof(serverId));

    if (strlen(token) == 0 || strlen(wsUrl) == 0)
    {
        ReplyToCommand(client, "[LyBot] Token 或 WebSocket 地址未配置");
        return Plugin_Handled;
    }

    NB_Connect(wsUrl, httpUrl, token, serverId);
    ReplyToCommand(client, "[LyBot] 连接请求已发起");
    return Plugin_Handled;
}

public Action Cmd_Disconnect(int client, int args)
{
    if (!NB_IsConnected())
    {
        ReplyToCommand(client, "[LyBot] 当前未连接");
        return Plugin_Handled;
    }

    NB_Disconnect();
    ReplyToCommand(client, "[LyBot] 已断开");
    return Plugin_Handled;
}

public Action Cmd_Status(int client, int args)
{
    char serverId[64];
    g_cvServerId.GetString(serverId, sizeof(serverId));

    ReplyToCommand(client, "[LyBot] 状态: %s | 服务器: %s",
        NB_IsConnected() ? "已连接" : "未连接", serverId);
    return Plugin_Handled;
}

bool IsAllowedExt(const char[] filename)
{
    int len = strlen(filename);
    if (len >= 4 && strcmp(filename[len - 4], ".vpk", false) == 0)
        return true;
    return false;
}

void RefreshVpkList()
{
    g_vpkCount = 0;
    char addonsPath[PLATFORM_MAX_PATH];
    BuildPath(Path_SM, addonsPath, sizeof(addonsPath), "../../addons");

    DirectoryListing dir = OpenDirectory(addonsPath, true);
    if (dir == null)
        return;

    FileType type;
    char entry[PLATFORM_MAX_PATH];
    while (dir.GetNext(entry, sizeof(entry), type))
    {
        if (type != FileType_File)
            continue;

        if (!IsAllowedExt(entry))
            continue;

        if (g_vpkCount >= MAX_VPK_LIST)
            break;

        strcopy(g_vpkList[g_vpkCount], sizeof(g_vpkList[]), entry);
        g_vpkCount++;
    }
    delete dir;
}

public Action Cmd_ListFiles(int client, int args)
{
    RefreshVpkList();

    if (g_vpkCount == 0)
    {
        ReplyToCommand(client, "[LyBot] addons 目录下没有 VPK 文件");
        return Plugin_Handled;
    }

    ReplyToCommand(client, "[LyBot] VPK 文件列表 (%d 个):", g_vpkCount);
    for (int i = 0; i < g_vpkCount; i++)
    {
        ReplyToCommand(client, "  #%d  %s", i + 1, g_vpkList[i]);
    }
    ReplyToCommand(client, "[LyBot] 使用 sm_lybot_sendfile <编号> 上传");
    return Plugin_Handled;
}

public Action Cmd_SendFile(int client, int args)
{
    if (!NB_IsConnected())
    {
        ReplyToCommand(client, "[LyBot] 当前未连接");
        return Plugin_Handled;
    }

    if (args < 1)
    {
        ReplyToCommand(client, "[LyBot] 用法: sm_lybot_sendfile <编号/文件名> [群号] [说明]");
        return Plugin_Handled;
    }

    char arg1[PLATFORM_MAX_PATH], fullPath[PLATFORM_MAX_PATH];
    char fileName[PLATFORM_MAX_PATH];
    GetCmdArg(1, arg1, sizeof(arg1));

    int idx = StringToInt(arg1);
    if (idx > 0 && idx <= g_vpkCount)
    {
        strcopy(fileName, sizeof(fileName), g_vpkList[idx - 1]);
    }
    else
    {
        strcopy(fileName, sizeof(fileName), arg1);
    }

    BuildPath(Path_SM, fullPath, sizeof(fullPath), "../../addons/%s", fileName);

    if (!FileExists(fullPath, true))
    {
        ReplyToCommand(client, "[LyBot] 文件不存在 (先用 sm_lybot_listfiles 查看编号)");
        return Plugin_Handled;
    }

    char channel[80], caption[256];
    if (args >= 2)
    {
        char groupArg[20];
        GetCmdArg(2, groupArg, sizeof(groupArg));
        if (StringToInt(groupArg) > 100000)
        {
            FormatEx(channel, sizeof(channel), "qq_group:%s", groupArg);
            if (args >= 3)
                GetCmdArg(3, caption, sizeof(caption));
            else
                caption[0] = '\0';
        }
        else
        {
            channel[0] = '\0';
            GetCmdArg(2, caption, sizeof(caption));
        }
    }
    else
    {
        channel[0] = '\0';
        caption[0] = '\0';
    }

    int taskId = NB_SendFile(channel, fullPath, caption);
    ReplyToCommand(client, "[LyBot] 文件上传已提交: %s (taskId=%d)", fileName, taskId);
    return Plugin_Handled;
}

public void NB_OnConnected()
{
    char serverId[64];
    g_cvServerId.GetString(serverId, sizeof(serverId));
    PrintToServer("[LyBot] 已连接到服务器 (server_id: %s)", serverId);
    RefreshVpkList();
}

public void NB_OnDisconnected(int code, const char[] reason)
{
    PrintToServer("[LyBot] 已断开: code=%d reason=%s", code, reason);
}

public void NB_OnFileNotice(const char[] channel, const char[] sender,
                            const char[] fileId, const char[] fileName,
                            int fileSize, const char[] sha256)
{
    PrintToServer("[LyBot] 收到文件: %s (%d bytes, sha256=%s)",
        fileName, fileSize, sha256);
    RefreshVpkList();
}

public void NB_OnTaskResult(int taskId, bool success, int errCode,
                            const char[] errMsg)
{
    if (!success)
    {
        PrintToServer("[LyBot] 任务 %d 失败: code=%d msg=%s", taskId, errCode, errMsg);
    }
}
