"""A small, explicit tool registry with workspace boundaries."""

from dataclasses import dataclass
from copy import deepcopy
import difflib
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
from app.services.workspace_paths import scoped_path
from urllib.parse import quote, urlsplit

from app.services.music_provider import LocalMusicProvider
from app.core.config import settings
from app.services.application_catalog import application_ids


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: list[str]
    risk_level: str
    requires_confirmation: bool = False

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name.replace(".", "_"),
                "description": self.description,
                "parameters": {"type": "object", "properties": deepcopy(self.properties),
                               "required": self.required, "additionalProperties": False}}}


STRING = {"type": "string"}
APP_ID = {"type": "string", "enum": ["netease", "qqmusic", "vscode", "notepad", "browser", "qq", "explorer", "calculator", "settings"]}
TOOLS = [
    Tool('workspace.list', 'Lazily list a selected project directory.', {'path': STRING}, ['path'], 'low'),
    Tool('workspace.read', 'Read a selected project UTF-8 file, up to 16 KB.', {'path': STRING}, ['path'], 'low'),
    Tool('workspace.search', 'Search filenames and small text files in the project.', {'query': STRING}, ['query'], 'low'),
    Tool('workspace.write', 'Write an existing project file with backup and diff.', {'path': STRING, 'content': STRING}, ['path', 'content'], 'medium'),
    Tool('workspace.create', 'Create a new project file without overwriting.', {'path': STRING, 'content': STRING}, ['path', 'content'], 'low'),
    Tool('workspace.rename', 'Rename one project file without overwriting.', {'path': STRING, 'destination': STRING}, ['path', 'destination'], 'medium'),
    Tool('workspace.delete', 'Delete one project file with a recovery backup.', {'path': STRING}, ['path'], 'high', True),
    Tool("browser.open_url", "Request that the desktop app open a public HTTPS page in the Workbench after confirmation.", {"url": STRING}, ["url"], "medium", True),
    Tool("browser.search", "Request a public web search page; this does not read results.", {"query": STRING}, ["query"], "medium", True),
    Tool("code.create_file", "Create a new UTF-8 file in the agent workspace; never overwrite.", {"path": STRING, "content": STRING}, ["path", "content"], "low"),
    Tool("code.edit_file", "Replace an existing workspace file with new UTF-8 content.", {"path": STRING, "content": STRING}, ["path", "content"], "medium", True),
    Tool("code.read_file", "Read a workspace file up to 16 KB.", {"path": STRING}, ["path"], "low"),
    Tool("code.run", "Run one Python script in the agent workspace; user approval required.", {"path": STRING}, ["path"], "medium", True),
    Tool("filesystem.list", "List the agent workspace or a subdirectory.", {"path": STRING}, ["path"], "low"),
    Tool("filesystem.read", "Read a workspace file up to 16 KB.", {"path": STRING}, ["path"], "low"),
    Tool("filesystem.write", "Create a new workspace file; never overwrite.", {"path": STRING, "content": STRING}, ["path", "content"], "low"),
    Tool('filesystem.delete', 'Delete one file inside the selected workspace only, always requires user confirmation.', {'path': STRING}, ['path'], 'high', True),
    Tool("app.detect", "Check whether a known Windows application is installed in the desktop UI.", {"app": APP_ID}, ["app"], "low"),
    Tool("app.status", "Check a known Windows application's running status in the desktop UI.", {"app": APP_ID}, ["app"], "low"),
    Tool("app.launch", "Request launching an installed known Windows application by registry ID.", {"app": APP_ID}, ["app"], "low", True),
    Tool("app.focus", "Request focusing a running known Windows application after approval.", {"app": APP_ID}, ["app"], "medium", True),
    Tool("app.close", "Request closing a running known Windows application after approval.", {"app": APP_ID}, ["app"], "high", True),
    Tool("music.search", "Autonomously start the desktop music app and search for a song; return observed candidates.", {"query": STRING, "provider": {"type": "string", "enum": ["netease", "qqmusic"]}}, ["query", "provider"], "low", True),
    Tool("music.play", "Autonomously start, search, select and play a song in a trusted desktop client, then verify its title and playing state. If the user supplies an artist, preserve both as query: 《song title》 artist.", {"query": STRING, "provider": {"type": "string", "enum": ["local", "netease", "qqmusic"]}}, ["query", "provider"], "low", True),
    Tool("music.pause", "Request pause of the local music player.", {}, [], "low"),
    Tool('system.get_status', 'Read CPU, memory, disk and network status, without modifying the system.', {}, [], 'low'),
    Tool('browser.search_site', 'Open a public website, search with a stable URL or semantic DOM controls, and verify real results.', {'site': STRING, 'query':STRING, 'url':STRING}, ['site','query'], 'low'),
    Tool('coding.task', 'Generate virtual code drafts or read a project and propose changes. Only explicit user write intent can apply local edits. Emit code/diff/terminal events and verify any requested tests.', {'goal':STRING}, ['goal'], 'high', True),
    *[Tool('media.'+operation, 'Read or control the authorized music application media session.', {'app': {'type':'string','enum':['netease','qqmusic']}}, ['app'], 'low', True) for operation in ('current','sessions','play','pause','next','previous')],
]


class ToolRegistry:
    def __init__(self):
        self.by_api_name = {tool.name.replace(".", "_"): tool for tool in TOOLS}

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in TOOLS]

    def schemas_for(self, user_text: str) -> list[dict]:
        """Give the model only the tools needed by an explicit, narrow request."""
        text = user_text.lower()
        names: set[str] | None = None
        if any(word in text for word in ('暂停','下一首','上一首','继续播放')) and any(word in text for word in ('音乐','歌','网易云','下一首','上一首')):
            names={'media.pause','media.play','media.next','media.previous','media.current'}
        elif any(word in text for word in ('修改','重构','修复','改成','改为')) and any(word in text for word in ('项目','按钮','代码','仓库')):
            names = {'coding.task'}
        elif any(word in text for word in ('cpu','内存','ram','电脑卡','系统状态')):
            names = {'system.get_status'}
        elif '搜索' in text and any(word in text for word in ('b站','哔哩','github','百度')):
            names = {'browser.search_site'}
        elif any(word in text for word in ('播放','放点','放歌','歌曲','放远航','搜索歌曲')):
            names = {'music.play','music.search', 'media.pause','media.current'}
        elif any(word in text for word in ('代码', '算法', '爬虫', '贪吃蛇', '排序', '优化', 'main.py', 'patch', '补丁')) and any(word in text for word in ('写', '生成', '改', '看看', '优化', '检查', '分析', '修复', '应用', '保存')):
            names = {'coding.task'}
        elif any(word in text for word in ("运行", "执行", "跑一下", "run ")) and any(word in text for word in (".py", "脚本", "代码", "程序")):
            names = {"code.run"}
        elif any(word in text for word in ("创建", "写一个", "写个", "编写")) and any(word in text for word in (".py", "代码", "脚本", "文件")):
            names = {"code.create_file", "filesystem.write"}
        elif any(word in text for word in ("打开", "open ")) and any(word in text for word in ("http", "github", "b站", "网页", "网站")):
            names = {"browser.open_url", "browser.search"}
        elif any(word in text for word in ("关闭", "退出", "停止")) and any(word in text for word in ("vscode", "vs code", "记事本", "网易云", "qq音乐")):
            names = {"app.close"}
        elif any(word in text for word in ("聚焦", "切换到", "置顶")) and any(word in text for word in ("vscode", "vs code", "记事本", "网易云", "qq音乐")):
            names = {"app.focus"}
        elif any(word in text for word in ("安装", "检测", "运行状态", "运行吗")) and any(word in text for word in ("vscode", "vs code", "记事本", "网易云", "qq音乐")):
            names = {"app.detect", "app.status"}
        elif any(word in text for word in ("打开", "启动", "open ")) and any(word in text for word in ("vscode", "vs code", "记事本", "网易云", "qq音乐")):
            names = {"app.launch"}
        elif any(word in text for word in ("音乐", "歌曲", "网易云", "qq音乐", "放点歌", "播放歌")):
            names = {"music.search", "music.play", "music.pause", "app.launch"}
        # A narrow file request should not tempt the model to edit unrelated code.
        if names is None and any(word in text for word in ('读取', '文件', '目录', '工作区', '工作文件夹', '项目文件')):
            names = {'workspace.list', 'workspace.read', 'workspace.search', 'workspace.create',
                     'workspace.write', 'workspace.rename', 'workspace.delete'}
        from app.core.config import settings
        try:
            custom=json.loads((settings.data_root/'known-applications.json').read_text(encoding='utf-8')).get('custom',{})
        except (OSError,ValueError):
            custom={}
        schemas=[tool.schema() for tool in TOOLS if names is None or tool.name in names]
        for schema in schemas:
            props=schema['function']['parameters']['properties']
            if 'app' in props and len(props['app'].get('enum',[]))>2:
                props['app']={**APP_ID,'enum':sorted(set(APP_ID['enum'])|application_ids())}
                schema['function']['description']+=' Known custom applications: '+json.dumps({key:item['name'] for key,item in custom.items()},ensure_ascii=False)
        return schemas

    def parse_calls(self, message: dict) -> list[dict]:
        raw = message.get("tool_calls") or []
        if not isinstance(raw, list) or len(raw) > 5:
            raise ValueError("工具调用数量异常")
        calls = []
        for item in raw:
            function = item.get("function") or {}
            tool = self.by_api_name.get(function.get("name"))
            if not tool:
                raise ValueError("模型请求了未授权工具")
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict) or set(arguments) - set(tool.properties):
                raise ValueError("工具参数无效")
            if any(key not in arguments for key in tool.required):
                raise ValueError("工具缺少必要参数")
            for key, value in arguments.items():
                spec = tool.properties[key]
                if spec.get('type') == 'string' and not isinstance(value, str):
                    raise ValueError('工具参数类型无效')
                if spec.get('enum') and value not in spec['enum']:
                    try:custom=json.loads((settings.data_root/'known-applications.json').read_text(encoding='utf-8')).get('custom',{})
                    except (OSError,ValueError):custom={}
                    if key!='app' or len(spec['enum'])<=2 or value not in application_ids():
                        raise ValueError('工具参数不在允许范围内')
            call_id = item.get('id')
            if not isinstance(call_id, str) or not call_id or len(call_id) > 100 or any(row['id'] == call_id for row in calls):
                raise ValueError('工具调用编号无效')
            calls.append({"id": call_id, "name": tool.name,
                          "arguments": arguments, "risk_level": tool.risk_level,
                          "requires_confirmation": tool.requires_confirmation})
        return calls


class PermissionManager:
    @staticmethod
    def permitted(call: dict, approved_ids: set[str]) -> bool:
        return not call["requires_confirmation"] or call["id"] in approved_ids


class ToolExecutor:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _run_python(self, target: Path, on_output=None, cancelled=None, paused=None) -> dict:
        if target.suffix.lower()!='.py' or not target.is_file():
            raise ValueError('只能执行项目中的 Python 脚本')
        executable = Path(os.getenv("AGENT_PYTHON", sys.executable))
        if not executable.is_file():
            raise FileNotFoundError("Agent Python 运行环境不存在")
        env = {key: value for key, value in os.environ.items() if key.upper() in
               {"PATH", "SYSTEMROOT", "WINDIR", "PROCESSOR_ARCHITECTURE"}}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                    "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                    "TEMP": str(self.workspace), "TMP": str(self.workspace)})
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        # -I removes Python's automatic script-directory entry. Add only this
        # approved workspace so real project tests can import sibling modules.
        bootstrap = ('import os,runpy,sys;root,target=sys.argv[1:3];'
                     'sys.path[:0]=[os.path.dirname(target),root];sys.argv=[target];'
                     'runpy.run_path(target,run_name="__main__")')
        process = subprocess.Popen([str(executable), "-I", "-B", "-u", "-X", "utf8",
                                   "-c", bootstrap, str(self.workspace), str(target)], cwd=self.workspace, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=flags)
        output = bytearray()
        exceeded = threading.Event()

        def read_output():
            assert process.stdout is not None
            while chunk := process.stdout.readline():
                output.extend(chunk[:max(0, 16_384 - len(output))])
                if on_output:
                    on_output(output.decode('utf-8', errors='replace'))
                if len(output) >= 16_384:
                    exceeded.set()
                    process.kill()
                    break

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            import time
            from app.services.coding.process_control import ProcessTree
            tree=ProcessTree(process.pid)
            deadline = time.monotonic()+15
            while process.poll() is None:
                if cancelled and cancelled():
                    process.kill()
                    raise RuntimeError('任务已停止')
                if time.monotonic()>deadline:
                    raise subprocess.TimeoutExpired(process.args,15)
                if paused and paused():
                    tree.pause();deadline+=.05
                else:tree.resume()
                time.sleep(.05)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
            raise TimeoutError("代码运行超过 15 秒，已停止")
        finally:
            tree.resume()
            if process.poll() is None:process.kill()
        reader.join(timeout=2)
        return {"exit_code": process.returncode, "output": output.decode("utf-8", errors="replace"),
                "output_truncated": exceeded.is_set()}

    def _path(self, value: str) -> Path:
        return scoped_path(self.workspace, value)

    def execute(self, call: dict) -> dict:
        name, args = call["name"], call["arguments"]
        if name == 'workspace.list':
            from app.services.workspace_service import tree
            return tree(str(self.workspace), args['path'])
        if name == 'workspace.search':
            from app.services.workspace_service import search
            return search(str(self.workspace), args['query'])
        if name == 'workspace.rename':
            target, destination = self._path(args['path']), self._path(args['destination'])
            if not target.is_file() or destination.exists():
                raise ValueError('只能重命名单个文件，且不能覆盖已有目标')
            destination.parent.mkdir(parents=True, exist_ok=True)
            target.rename(destination)
            return {'path': str(destination), 'previous_path': str(target), 'renamed': True}
        name = {'workspace.list': 'filesystem.list', 'workspace.read': 'filesystem.read',
                'workspace.write': 'code.edit_file', 'workspace.create': 'filesystem.write',
                'workspace.delete': 'filesystem.delete'}.get(name, name)
        if name in {"code.create_file", "filesystem.write", "code.edit_file"}:
            target = self._path(args["path"])
            content = args["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > 100_000:
                raise ValueError("文件内容过长")
            if name != "code.edit_file" and target.exists():
                raise FileExistsError("文件已存在，拒绝覆盖")
            if name == "code.edit_file" and not target.is_file():
                raise FileNotFoundError("待编辑文件不存在")
            if name == 'code.edit_file' and target.stat().st_size > 100_000:
                raise ValueError('待编辑文件超过 100 KB')
            previous = target.read_text(encoding="utf-8") if name == "code.edit_file" else ""
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == "code.edit_file":
                backup = self._path(str(target.with_suffix(target.suffix + '.agent-backup').relative_to(self.workspace)))
                if not backup.exists():
                    backup.write_bytes(target.read_bytes())
            target.write_text(content, encoding="utf-8")
            diff = "".join(difflib.unified_diff(previous.splitlines(keepends=True),
                           content.splitlines(keepends=True), fromfile=f"a/{args['path']}",
                           tofile=f"b/{args['path']}"))[:16_000]
            return {"path": str(target), "bytes": len(content.encode("utf-8")), "diff": diff}
        if name in {"code.read_file", "filesystem.read"}:
            target = self._path(args["path"])
            if target.stat().st_size > 16_384:
                raise ValueError("文件超过 16 KB")
            return {"path": str(target), "content": target.read_text(encoding="utf-8")}
        if name == "filesystem.list":
            from app.services.workspace_service import tree
            result = tree(str(self.workspace), args['path'])
            return {'path': str(self._path(args['path'])), 'items': [p['name'] for p in result['items']],
                    'truncated': result['truncated']}
        if name == 'filesystem.delete':
            target = self._path(args['path'])
            if not target.is_file():
                raise ValueError('只能删除工作区内的单个文件')
            # Preserve a local recovery copy before removing the original file.
            backup = self._path(str(target.with_suffix(target.suffix + '.deleted-backup').relative_to(self.workspace)))
            if backup.exists():
                raise ValueError('已有恢复副本，请先检查，避免覆盖')
            backup.write_bytes(target.read_bytes())
            target.unlink()
            return {'path': str(target), 'deleted': True, 'recovery': str(backup)}
        if name == "code.run":
            target = self._path(args["path"])
            if target.suffix.lower() != ".py" or not target.is_file():
                raise ValueError("只允许运行工作区中的 Python 文件")
            return self._run_python(target)
        if name == "app.launch":
            return {"action": "launch_app", "app": args["app"], "queued": True,
                    "note": "桌面界面确认安装状态并请求启动；工具尚未确认应用已运行"}
        if name in {"app.detect", "app.status", "app.focus", "app.close"}:
            action = {"app.detect": "query_app", "app.status": "query_app",
                      "app.focus": "focus_app", "app.close": "close_app"}[name]
            return {"action": action, "app": args["app"], "queued": True,
                    "note": "桌面界面将处理此应用操作；工具尚未确认结果"}
        if name in {"music.search", "music.play"} and args["provider"] in {"netease", "qqmusic"}:
            return {"action": "launch_app", "app": args["provider"], "query": args["query"][:160],
                    "queued": True, "note": "启动官方客户端后需要在客户端内搜索歌曲；不会假定歌曲已播放"}
        if name in {"browser.open_url", "browser.search", "music.play"}:
            if name == "browser.open_url":
                url = args["url"]
                parts = urlsplit(url)
                if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
                    raise ValueError("只允许打开公开 HTTPS 页面")
            elif name == "browser.search":
                url = "https://www.bing.com/search?q=" + quote(args["query"][:160])
            else:
                matches = LocalMusicProvider().search(args["query"])["tracks"]
                return {"action": "local_music_search", "query": args["query"][:160],
                        "found": bool(matches), "track": matches[0] if matches else None,
                        "note": "应用界面会尝试播放匹配项；此工具不会假定声音已经输出"}
            return {"action": "open_url", "url": url, "queued": True,
                    "note": "桌面界面随后尝试打开网页；工具本身不声称网页已经加载或音乐已经播放"}
        if name == "music.pause":
            return {"action": "local_music_pause"}
        raise ValueError("未知工具")
