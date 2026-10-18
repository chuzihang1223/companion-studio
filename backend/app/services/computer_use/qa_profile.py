"""Never turn an already-running formal profile into a GUI test profile by API."""

import json
import os
from app.core.config import settings


def enabled():
    if os.environ.get("COMPANION_VISUAL_QA_PROFILE") != "1":
        return False
    marker = settings.data_root.parent / ".companion-visual-qa"
    try:
        return (
            marker.stat().st_size < 1024
            and json.loads(marker.read_text(encoding="utf-8")).get("purpose")
            == "companion-visual-qa"
        )
    except (OSError, ValueError):
        return False
