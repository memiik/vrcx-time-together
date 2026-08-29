from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    log_directory = base / "VRCX Time Together"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "application.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        root.addHandler(handler)
    return log_path
