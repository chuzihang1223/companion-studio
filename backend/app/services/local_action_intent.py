"""High confidence local actions are resolved before any model request."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LocalActionIntent:
    operation: str
    target: str = ''
    complex: bool = False
    arguments: dict | None = None


class LocalActionIntentResolver:
    PREFIX = re.compile(r'^(?:(?:助手|伴舟|companion\s*studio)[，,\s]*)?(?:(?:请|帮我|替我|麻烦你|给我|再|重新)\s*)*'
                        r'(打开|启动|运行|关闭|退出|聚焦|切换到|查看状态|看看状态|检测)(.+)$', re.I)

    def resolve(self, text: str) -> LocalActionIntent | None:
        text = text.strip().rstrip('。.!！?？')
        from app.services.web_action import WebActionResolver
        web = WebActionResolver().resolve(text)
        if web:
            return LocalActionIntent(web.operation, web.target, arguments=web.arguments)
        if re.fullmatch(r'(?:请|帮我)?(?:查看|看看|读取)?(?:电脑|系统)(?:的)?(?:状态|运行状态)', text):
            return LocalActionIntent('system.get_status')
        match = self.PREFIX.match(text)
        if not match:
            return None
        verb, target = match.groups()
        music = re.fullmatch(r'(QQ\s*音乐|网易云(?:音乐)?)\s*[，,；;]?\s*(?:并|然后|再)?\s*(播放|放|搜索)\s*(.+)', target, re.I)
        if music and verb in {'打开','启动'}:
            name, action, query = music.groups()
            return LocalActionIntent('music.search' if action == '搜索' else 'music.play', name,
                arguments={'provider':'qqmusic' if name.casefold().startswith('qq') else 'netease',
                           'query':query.strip('“”"')[:160]})
        tail = re.search(r'[，,；;]|然后|接着|(?:并|再)?(?:搜索|播放)|并(?:帮|查看|读取|修改)', target)
        complex_ = tail is not None
        if tail:
            target = target[:tail.start()]
        target = target.strip(' \t\n“”"「」').rstrip('。.!！?？').strip()
        if not target or len(target) > 150:
            return None
        if re.search(r'(?:\.py|脚本|代码|命令|项目|文件夹|补丁|算法|网页搜索)', target, re.I):
            return None
        if target.startswith(('https://', 'http://')) and verb in {'打开', '启动'}:
            url = urlsplit(target)
            if url.hostname and not url.username and not url.password:
                return LocalActionIntent('browser.open_url', target, complex_)
            return None
        if any(char in target for char in '\\/:$`\n\r;&|'):
            return None
        operation = {'打开':'app.launch', '启动':'app.launch', '运行':'app.launch',
                     '关闭':'app.close', '退出':'app.close', '聚焦':'app.focus',
                     '切换到':'app.focus', '查看状态':'app.status', '看看状态':'app.status', '检测':'app.detect'}[verb]
        return LocalActionIntent(operation, target, complex_)
