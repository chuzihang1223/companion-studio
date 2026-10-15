"""在导入应用前隔离测试数据与外部服务，避免 pytest 访问真实档案。"""

import ipaddress
import os
from pathlib import Path
import socket
import uuid

import pytest


PROJECT = Path(__file__).resolve().parents[2]
TEST_ROOT = PROJECT / ".cache" / "test-profiles" / uuid.uuid4().hex
TEST_ROOT.mkdir(parents=True, exist_ok=False)
# app.main 的导入会创建全局数据库/日志对象，必须早于测试模块设置环境。
os.environ.update(
    {
        "COMPANION_DATA_DIR": str(TEST_ROOT),
        "COMPANION_CLEAN_DISTRIBUTION": "1",
        "COMPANION_DEFAULT_CHARACTER": "assistant",
        "OPENAI_API_KEY": "",
        "OPENAI_MODEL": "",
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
        "FISH_API_KEY": "",
        "FISH_REFERENCE_ID": "",
        "VOICE_PREFETCH_ENABLED": "false",
        "VOICE_MODEL_DIR": str(TEST_ROOT / "no-model"),
        "VOICE_INDEX_MODEL_DIR": str(TEST_ROOT / "no-index-model"),
        "VOICE_REFERENCES_DIR": str(TEST_ROOT / "no-references"),
    }
)


def is_loopback(host: object) -> bool:
    if host is None or host in {"localhost", b"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


@pytest.fixture(autouse=True)
def reject_external_network(monkeypatch):
    """只允许测试本机端点；云服务必须通过 transport/client Mock 验证。"""
    original_connect = socket.socket.connect
    original_lookup = socket.getaddrinfo

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and not is_loopback(address[0]):
            raise RuntimeError("External network is disabled in tests; use a mock transport")
        return original_connect(sock, address)

    def guarded_lookup(host, *args, **kwargs):
        if not is_loopback(host):
            raise RuntimeError("External DNS is disabled in tests; use a mock transport")
        return original_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_lookup)
