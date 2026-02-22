#if defined _lybot_included
 #endinput
#endif
#define _lybot_included

/**
 * Connect to the NoneBot bridge via WebSocket.
 *
 * @param wsUrl       WebSocket server URL (e.g. "ws://127.0.0.1:18080/ws/l4d2").
 * @param httpUrl     HTTP server base URL for file transfers.
 * @param token       Authentication token (HMAC-SHA256 signing key).
 * @param serverId    Unique server identifier string.
 * @return            True if the connection attempt was initiated.
 */
native bool NB_Connect(const char[] wsUrl, const char[] httpUrl,
                       const char[] token, const char[] serverId);

/**
 * Disconnect from the NoneBot bridge and stop auto-reconnect.
 */
native void NB_Disconnect();

/**
 * Check whether the bridge is currently connected and authenticated.
 *
 * @return            True if connected.
 */
native bool NB_IsConnected();

/**
 * Upload a local file to the specified channel via the bridge.
 *
 * @param channel     Target channel identifier.
 * @param localPath   Absolute path to the local file.
 * @param caption     Optional caption text attached to the file.
 * @return            Task ID for tracking the result via NB_OnTaskResult.
 */
native int NB_SendFile(const char[] channel, const char[] localPath,
                       const char[] caption = "");

/**
 * Download a file from the bridge by its file ID.
 *
 * @param fileId      Remote file identifier.
 * @param savePath    Local path to save the downloaded file.
 * @return            Task ID for tracking the result via NB_OnTaskResult.
 */
native int NB_DownloadFile(const char[] fileId, const char[] savePath);

/**
 * Called when the bridge connection is established and authenticated.
 */
forward void NB_OnConnected();

/**
 * Called when the bridge connection is lost.
 *
 * @param code        Disconnect status code (WebSocket close code, or -1 on error).
 * @param reason      Human-readable disconnect reason.
 */
forward void NB_OnDisconnected(int code, const char[] reason);

/**
 * Called when a file transfer notice is received from the bridge.
 *
 * @param channel     Source channel identifier.
 * @param sender      Sender name or ID.
 * @param fileId      Remote file identifier (use with NB_DownloadFile).
 * @param fileName    Original file name.
 * @param fileSize    File size in bytes.
 * @param sha256      SHA-256 checksum of the file.
 */
forward void NB_OnFileNotice(const char[] channel, const char[] sender,
                             const char[] fileId, const char[] fileName,
                             int fileSize, const char[] sha256);

/**
 * Called when an async task (SendFile / DownloadFile) completes.
 *
 * @param taskId      Task ID returned by the originating native call.
 * @param success     True if the task completed successfully.
 * @param errCode     Error code (0 on success).
 * @param errMsg      Error message (empty on success).
 */
forward void NB_OnTaskResult(int taskId, bool success, int errCode,
                             const char[] errMsg);


public Extension __ext_lybot =
{
    name = "LyBot",
    file = "lybot.ext",
#if defined AUTOLOAD_EXTENSIONS
    autoload = 1,
#else
    autoload = 0,
#endif
#if defined REQUIRE_EXTENSIONS
    required = 1,
#else
    required = 0,
#endif
};

#if !defined REQUIRE_EXTENSIONS
public void __ext_lybot_SetNTVOptional()
{
    MarkNativeAsOptional("NB_Connect");
    MarkNativeAsOptional("NB_Disconnect");
    MarkNativeAsOptional("NB_IsConnected");
    MarkNativeAsOptional("NB_SendFile");
    MarkNativeAsOptional("NB_DownloadFile");
}
#endif
