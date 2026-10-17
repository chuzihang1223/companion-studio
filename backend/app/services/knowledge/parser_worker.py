"""Single bounded request, no network or arbitrary executable invocation."""

import base64
import json
import sys

from app.services.knowledge.contracts import KnowledgeError
from app.services.knowledge.parser import parse


def main():
    try:
        raw = sys.stdin.buffer.read(24 * 1024 * 1024 + 1)
        if len(raw) > 24 * 1024 * 1024:
            raise KnowledgeError("KNOWLEDGE_FILE_TOO_LARGE")
        payload = json.loads(raw)
        result = {"blocks": parse(base64.b64decode(payload["raw"], validate=True), payload["type"])}
    except KnowledgeError as error:
        result = {"error": error.code}
    except Exception:
        result = {"error": "KNOWLEDGE_PARSER_FAILED"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
