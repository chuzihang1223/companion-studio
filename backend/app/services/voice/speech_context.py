"""Resolve the current situation before choosing a sentence's delivery."""
import re


def _current_scene(context: str) -> str:
    text = context.lower()
    if any(word in text for word in ("受伤", "流血", "摔倒")):
        return "injury"
    if any(word in text for word in ("危险", "伤害别人", "害人", "偷偷伤害")):
        return "danger"
    if any(word in text for word in ("去世", "死亡", "严重", "事故", "辞职", "分手", "冲动的决定")):
        return "serious"
    if re.search(r"请认真|认真.{0,8}(?:看看|讨论|对待|说)|严肃|很重要|重要决定", text):
        return "serious"
    # A negative outcome must not match the substring '成功' or '通过'.
    if re.search(r"失败|没.{0,2}成功|未.{0,2}成功|没.{0,2}通过|未.{0,2}通过|没完成|未完成|报错|出错了", text):
        technical = any(word in text for word in ("代码", "修复", "运行", "测试", "任务", "项目", "程序", "报错"))
        return "agent_problem" if technical else "sad"
    if any(word in text for word in ("难过", "伤心", "失落", "失业")):
        return "sad"
    # Explicit invitations to tease remain playful even if the topic is bedtime.
    if re.search(r"逗逗我|逗我|开个玩笑|调皮一点|调侃我", text):
        return "invited_teasing"
    if any(word in text for word in ("没吃饭", "不吃饭", "忘记吃饭", "硬撑")):
        return "self_neglect"
    if any(word in text for word in ("没睡", "熬夜", "撑不住", "好累", "很累", "睡得很少", "睡得少", "睡不好")):
        return "tired"
    if any(word in text for word in ("有点累", "疲惫", "疲劳")):
        return "tired_mild"
    if any(word in text for word in ("孤独", "孤单", "只有自己", "一个人")):
        return "lonely"
    if any(word in text for word in ("骗你的", "开玩笑", "逗你")):
        return "prank"
    if any(word in text for word in ("我想你", "想我了", "抱抱", "想抱你")):
        return "affection"
    if any(word in text for word in ("你怎么才", "好久不见", "终于回来了", "回来了")):
        return "reunion"
    if any(word in text for word in ("夸你", "喜欢你", "脸红")):
        return "flattered"
    if any(word in text for word in ("修好了", "测试通过", "任务完成")):
        return "agent_success"
    if any(word in text for word in ("成功", "通过了", "面试过了", "录取", "offer", "好消息", "赢了", "生日")):
        return "good_news"
    if "晚安" in text or "睡觉" in text:
        return "goodnight"
    if any(word in text for word in ("以前", "小时候", "回忆")):
        return "memory"
    if any(word in text for word in ("项目", "代码", "查资料", "分析")):
        return "agent_working"
    return "casual"


def resolve_scene(user: str, history: list[dict[str, str]]) -> str:
    scene = _current_scene(user)
    if scene != "casual" or len(user.strip()) > 6:
        return scene
    # '真的？' can depend on history; a new explicit '晚安' must not inherit it.
    nearby = [item.get("content", "") for item in reversed(history)
              if item.get("content", "").strip() != user.strip()][:2]
    return _current_scene(user + " " + " ".join(nearby))
