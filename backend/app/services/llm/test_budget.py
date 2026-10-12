"""Optional request cap for authorized acceptance runs; dormant in normal use."""
import os
from pathlib import Path
from threading import Lock
lock=Lock()


def record_test_request(base_url=None):
    # Deterministic loopback fixtures never consume the external-model allowance.
    if base_url:
        from urllib.parse import urlsplit
        if urlsplit(base_url).hostname in {'localhost','127.0.0.1','::1'}:return
    name=os.getenv('COMPANION_TEST_LLM_COUNTER')
    if not name:return
    with lock:
        path=Path(name)
        count=int(path.read_text() if path.exists() else '0')
        limit=int(os.getenv('COMPANION_TEST_LLM_LIMIT','0'))
        if count>=limit:raise RuntimeError('本轮已授权的模型测试次数已用完')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(str(count+1))
