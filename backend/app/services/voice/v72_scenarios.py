"""Fixed listening set for comparing contextual Fish speech direction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceScenario:
    name: str
    user: str
    reply: str
    history: tuple[dict[str, str], ...] = ()


SCENARIOS = (
    VoiceScenario("greeting", "你好。", "你好，今天想聊些什么？"),
    VoiceScenario("thanks", "谢谢你。", "不用客气。能帮上忙就好。"),
    VoiceScenario("break", "我想休息一会儿。", "可以，先休息一下。之后我们再继续。"),
    VoiceScenario("daily_plan", "今天有什么安排？", "先把最需要做的事列出来，一件一件完成。"),
    VoiceScenario("interview_success", "我面试通过了。", "真的？！太好了！快告诉我，他们怎么说的？"),
    VoiceScenario("tired", "今天累死了。", "先坐下来歇一会儿吧。你已经做得够多了。"),
    VoiceScenario("no_sleep", "我三天没睡。", "等等，三天没睡？现在先别硬撑了，去休息，好吗？"),
    VoiceScenario("goodnight", "晚安。", "晚安……好好睡，明天见。"),
    VoiceScenario("late_return", "我刚回到家。", "到家就先放松一下。今天过得怎么样？"),
    VoiceScenario("night_work", "今晚要赶一份报告。", "先把重点列出来吧。做完记得休息。"),
    VoiceScenario("really_happy", "我面试通过了。", "真的？太好了，我就知道你做得到！"),
    VoiceScenario("really_prank", "我刚才说成功了是骗你的，开玩笑。", "真的？你怎么又逗我呀。"),
    VoiceScenario("really_worried", "我三天没睡，已经快撑不住了。", "真的？先坐下，别再硬撑了。"),
    VoiceScenario("really_serious", "今天发生严重事故。", "真的？先确认你现在安全吗。"),
    VoiceScenario("reunion", "好久不见，我回来了。", "你终于回来啦！我还以为要等很久呢。"),
    VoiceScenario("compliment", "这个办法很好用。", "太好了。后面遇到类似问题也可以这样处理。"),
    VoiceScenario("celebration", "项目终于成功了！", "太棒啦！这次一定要好好庆祝。"),
    VoiceScenario("curious", "我发现一个秘密。", "什么秘密？快说给我听听。"),
    VoiceScenario("lonely", "今天有点孤单。", "那我陪你待一会儿。你想说什么，我都听着。"),
    VoiceScenario("sad", "今天有点难过。", "嗯，我在这里。想哭也没关系，不用急着变好。"),
    VoiceScenario("injured", "我刚才摔倒受伤了。", "你哪里受伤了？先看看有没有流血。"),
    VoiceScenario("danger", "有人在外面追我，我很危险。", "先去安全的地方，马上联系身边能帮你的人。"),
    VoiceScenario("self_neglect", "我今天还没吃饭。", "先吃点东西吧。有力气了再继续忙。"),
    VoiceScenario("nostalgic", "想起小时候的事了。", "那时候的事，现在想起来还会觉得很特别吧。"),
    VoiceScenario("playful", "这是个小玩笑。", "原来是玩笑呀，差点当真了。"),
    VoiceScenario("encouragement", "我有点不敢开始。", "先试一个小步骤吧，不用一下子做到完美。"),
    VoiceScenario("work_progress", "帮我分析项目代码。", "我先看一下结构，再告诉你我发现了什么。"),
    VoiceScenario("work_success", "任务完成，测试通过了。", "修好啦！这次测试也通过了。"),
    VoiceScenario("work_problem", "运行失败，出现报错。", "我看到错误了。先找出原因，再一步一步处理。"),
    VoiceScenario("mixed_turn", "今天终于做完了，但有点累。", "完成了就很好。先休息一下，之后再看看成果。"),
)
