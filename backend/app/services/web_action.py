"""Deterministic website intents; no model or installed-app lookup is needed."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

@dataclass(frozen=True)
class WebAction:
    operation: str
    arguments: dict
    target: str

class WebActionResolver:
    SITES = {'b站': ('bilibili', 'https://www.bilibili.com/'),
             '哔哩哔哩': ('bilibili', 'https://www.bilibili.com/'),
             'bilibili': ('bilibili', 'https://www.bilibili.com/'),
             '百度': ('baidu', 'https://www.baidu.com/'),
             'baidu': ('baidu', 'https://www.baidu.com/'),
             'github': ('github', 'https://github.com/')}

    def resolve(self, text: str) -> WebAction | None:
        match = re.fullmatch(r'(?:(?:助手|伴舟|companion\s*studio)[，,\s]*)?(?:(?:请|帮我|替我)\s*)*'
                             r'(?:打开|访问|进入)\s*(.+?)(?:\s*(?:并|然后|再)?搜索\s*(.+))?',
                             text.strip().rstrip('。！!'), re.I)
        if not match:
            return None
        target, query = match.groups()
        target = target.strip().removesuffix('网站').strip()
        known = self.SITES.get(target.casefold())
        if known:
            site, url = known
        else:
            url = target if target.startswith('https://') else 'https://' + target
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or not parsed.hostname or '.' not in parsed.hostname
                    or re.search(r'[;&|`$]|\.(?:py|exe|bat|cmd|ps1)$', target, re.I)
                    or parsed.username or parsed.password or re.search(r'[\s\\]', target)):
                return None
            site = parsed.hostname.casefold()
            for name, entry in self.SITES.values():
                if parsed.hostname in {urlsplit(entry).hostname, name + '.com'}:
                    site, url = name, entry
                    break
        if query:
            query = query.strip().strip('“”"')[:160]
            if not query:
                return None
            return WebAction('browser.search_site', {'site': site, 'query': query, 'url': url}, target)
        return WebAction('browser.open_url', {'url': url}, target)
