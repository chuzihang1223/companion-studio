"""Entry point used by the standalone Windows backend."""

import logging
import os
import sys
import threading
from pathlib import Path

import uvicorn


def watch_parent(parent_pid: int) -> None:
    """Exit if Electron is terminated without running its quit handlers."""
    if sys.platform != "win32" or parent_pid <= 0:
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
    if not handle:
        logging.warning("Could not watch parent process %s", parent_pid)
        return

    def wait_for_parent() -> None:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel32.CloseHandle(handle)
        os._exit(0)

    threading.Thread(target=wait_for_parent, daemon=True).start()


def main() -> None:
    if sys.argv[1:] == ["--evaluation-worker"]:
        from app.services.evaluation.worker import main as evaluation_main

        evaluation_main()
        return
    data_dir = Path(os.environ["COMPANION_DATA_DIR"])
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "backend.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    watch_parent(int(os.environ.get("COMPANION_PARENT_PID", "0")))
    try:
        # Import after reading the environment, so config and SQLite use UserData.
        from app.main import app

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(os.environ.get("BACKEND_PORT", "8000")),
            log_config=None,
        )
    except Exception:
        logging.exception("Backend failed")
        raise


if __name__ == "__main__":
    main()
