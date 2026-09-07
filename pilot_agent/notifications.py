"""Direct Discord REST push (2026-09-07 addition, Henry's explicit request
for a 08:15 / 22:00 proactive Morning Brief / Daily Close -- Functional
Spec v1.0 SS5.1/5.3).

This deliberately does NOT reintroduce a Discord client/gateway connection
-- it is a single outbound HTTP POST to Discord's REST "create message"
endpoint using the same bot token Channels already uses. Receiving
messages is untouched and stays entirely owned by the official
`plugin:discord@claude-plugins-official` + Channels integration (see
adapters/discord_adapter.py's docstring for why that boundary matters).
The reason this needs to exist at all: Task Scheduler firing at a fixed
time has no live Claude Code session to relay through, so the scheduled
push has to reach Discord on its own.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_API_BASE = "https://discord.com/api/v10"
_MAX_CONTENT_CHARS = 1900  # Discord's real cap is 2000; leave headroom for the truncation notice


class DiscordPushError(RuntimeError):
    pass


def send_message(content: str, *, channel_id: "str | None" = None, token: "str | None" = None) -> None:
    """Posts `content` to a Discord channel via the Bot REST API. Raises
    DiscordPushError with an honest, specific reason on any failure --
    never silently swallows a failed send."""
    token = token or os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = channel_id or os.environ.get("DISCORD_PILOT_CHANNEL_ID")
    if not token:
        raise DiscordPushError("DISCORD_BOT_TOKEN 環境變數未設定,無法發送訊息。")
    if not channel_id:
        raise DiscordPushError("DISCORD_PILOT_CHANNEL_ID 環境變數未設定,不知道要發到哪個頻道。")

    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS] + "\n…(內容過長,已截斷)"

    url = f"{_API_BASE}/channels/{channel_id}/messages"
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "AIagentAssistantPilot/1.0 (Henry's personal Discord push, local-only)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status not in (200, 201):
                raise DiscordPushError(f"Discord API 回應非預期狀態碼: {resp.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DiscordPushError(f"Discord API 呼叫失敗(HTTP {exc.code}): {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise DiscordPushError(f"無法連線到 Discord API: {exc}") from exc
