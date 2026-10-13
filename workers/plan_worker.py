"""
Background worker for generating structured coding plans.

Runs generate_plan on a background QThread to keep the GUI responsive.
Supports immediate cancellation via stop().
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.ollama_client import OllamaClient
from core.planner import CodingPlan, generate_plan


class PlanWorker(QThread):
    """Generates a CodingPlan on a background thread."""

    # Emitted when the plan is generated successfully
    plan_ready = Signal(object)  # CodingPlan

    # Emitted on connection or model failure
    plan_error = Signal(str)

    def __init__(
        self,
        ollama_url: str,
        model: str,
        user_request: str,
        num_ctx: int = 8192,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._request = user_request
        self._num_ctx = num_ctx
        self._stop = False

    def stop(self) -> None:
        """Cancel plan generation immediately."""
        self._stop = True

    def run(self) -> None:
        client = OllamaClient(self._url)
        try:
            plan = generate_plan(
                ollama_url=self._url,
                model=self._model,
                user_request=self._request,
                num_ctx=self._num_ctx,
                stop_flag=lambda: self._stop,
                client=client,
            )
            if self._stop:
                self.plan_error.emit("Planning cancelled by user.")
            else:
                self.plan_ready.emit(plan)
        except Exception as e:
            self.plan_error.emit(f"Failed to generate plan: {e}")
        finally:
            client.close()
