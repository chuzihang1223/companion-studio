"""Platform identity belongs to a task, never to the last active player."""

import re

NAMES = {"qqmusic": "QQ音乐", "netease": "网易云音乐"}


def music_context(context, provider: str, query: str) -> dict:
    if provider not in NAMES:
        raise ValueError("未知音乐平台")
    quoted = re.search(r"《([^》]+)》", query)
    title = quoted.group(1) if quoted else query
    artist = query.replace(quoted.group(0), "").strip() if quoted else None
    return {
        "taskId": context.task_id,
        "providerId": provider,
        "applicationId": provider,
        "applicationName": NAMES[provider],
        "targetSong": title,
        "targetArtist": artist,
        "currentStep": "prepare",
    }


def music_feedback(call: dict, result: dict, failed=False) -> str:
    provider = call["arguments"].get("provider")
    name = NAMES.get(provider, "音乐客户端")
    saved = result.get("music_context") or {}
    # Reject another platform's context instead of echoing its display name.
    if saved.get("providerId") == provider and saved.get("applicationName") == name:
        public = result.get("public_error")
        if (
            failed
            and isinstance(public, str)
            and name in public
            and not any(other in public for key, other in NAMES.items() if key != provider)
        ):
            return public
    if failed:
        return f"我还没确认这首歌在{name}里开始播放，这次先停下了。"
    if call["name"] == "music.search":
        return f"我在{name}里找到单曲结果了，放在媒体模块里。"
    title = result.get("title") or saved.get("targetSong") or call["arguments"]["query"]
    return f"《{title}》已经在{name}里放好啦。"
