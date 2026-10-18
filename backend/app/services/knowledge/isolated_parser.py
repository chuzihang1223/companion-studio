"""Expensive file formats run with a deadline and an owned Windows memory limit."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from app.core.config import settings
from app.services.evaluation.environment import OwnedJob
from app.services.knowledge.contracts import KnowledgeError
from app.services.knowledge.parser import parse


def parse_isolated(raw, file_type, stop=None):
    if file_type not in {".pdf", ".docx"}:
        return parse(raw, file_type)
    command = (
        [sys.executable, "--knowledge-parse-worker"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "app.services.knowledge.parser_worker"]
    )
    home = settings.data_root / "knowledge" / "parser-temp"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}
    }
    env.update(
        TEMP=str(home), TMP=str(home), PYTHONDONTWRITEBYTECODE="1", COMPANION_DATA_DIR=str(home)
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[3] if not getattr(sys, "frozen", False) else home,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    job = None
    try:
        job = OwnedJob(process.pid, memory_bytes=512 * 1024 * 1024)
        if stop and stop.is_set():
            raise KnowledgeError("KNOWLEDGE_CANCELLED")
        data = json.dumps(
            {"raw": base64.b64encode(raw).decode("ascii"), "type": file_type}
        ).encode()
        deadline = time.monotonic() + 15
        while True:
            if stop and stop.is_set():
                raise KnowledgeError("KNOWLEDGE_CANCELLED")
            if time.monotonic() >= deadline:
                raise KnowledgeError("KNOWLEDGE_PARSER_TIMEOUT")
            try:
                output, _ = process.communicate(data, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                data = None
        if stop and stop.is_set():
            raise KnowledgeError("KNOWLEDGE_CANCELLED")
        if process.returncode != 0 or len(output) > 4 * 1024 * 1024:
            raise KnowledgeError("KNOWLEDGE_PARSER_RESOURCE_LIMIT")
        result = json.loads(output)
        if result.get("error"):
            raise KnowledgeError(result["error"])
        return result["blocks"]
    except subprocess.TimeoutExpired:
        raise KnowledgeError("KNOWLEDGE_PARSER_TIMEOUT") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if job:
            job.close()
