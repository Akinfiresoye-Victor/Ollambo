"""
Background worker that runs an OS install command and streams output.

The output is emitted line-by-line via a signal so the terminal widget
can display it in real time.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.installer import InstallPlan, run_install


class InstallWorker(QThread):
    """Runs an install command on a background thread."""

    # Each line of stdout/stderr.
    output_line = Signal(str)

    # (success: bool, message: str)
    finished_install = Signal(bool, str)

    def __init__(self, plan: InstallPlan, parent=None) -> None:
        super().__init__(parent)
        self._plan = plan
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        success = run_install(
            self._plan,
            on_output=self._emit_output,
            stop_flag=lambda: self._stop,
        )
        if success:
            self.finished_install.emit(True, "Installation completed successfully.")
        else:
            self.finished_install.emit(
                False,
                "Installation failed. See the output above, or try installing manually.",
            )

    def _emit_output(self, text: str) -> None:
        self.output_line.emit(text)
