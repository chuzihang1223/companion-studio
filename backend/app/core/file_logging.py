"""Small bounded logs for non-secret desktop events."""

import logging
from logging.handlers import RotatingFileHandler

from app.core.config import settings


def file_logger(name: str, filename: str) -> logging.Logger:
    folder = settings.data_root / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    logger = logging.getLogger(name)
    if not any(getattr(handler, "baseFilename", None) == str(target) for handler in logger.handlers):
        handler = RotatingFileHandler(target, maxBytes=5_000_000, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
