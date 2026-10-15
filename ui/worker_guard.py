"""Safe QThread helpers.

deleteLater() can destroy a worker before we check isRunning(), which raises
"Internal C++ object already deleted". Always clear the Python reference in
the finished handler, and guard every isRunning/stop call.
"""

from __future__ import annotations

from typing import Any


def worker_is_running(worker: Any) -> bool:
    if worker is None:
        return False
    try:
        return bool(worker.isRunning())
    except RuntimeError:
        return False


def request_stop(worker: Any) -> None:
    if worker is None:
        return
    try:
        if worker.isRunning() and hasattr(worker, "stop"):
            worker.stop()
    except RuntimeError:
        pass


def wait_briefly(worker: Any, msec: int = 500) -> None:
    if worker is None:
        return
    try:
        if worker.isRunning():
            worker.wait(msec)
    except RuntimeError:
        pass
