"""
Background worker for generating and testing code files across multiple agents.

Features:
  - Multi-agent asynchronous parallel code generation (configurable worker pool)
  - Self-healing code testing engine (syntax validation and automated bug repair)
  - Automatic multi-part continuation when files exceed output token limits
  - Resumption from checkpoints with previously completed files
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Empty, Queue

from PySide6.QtCore import QThread, Signal

from core.code_tester import test_and_repair
from core.code_writer import generate_single_file
from core.planner import CodingPlan, PlanStep


class CodeWorker(QThread):
    """Executes an approved CodingPlan using one or more parallel agents."""

    # (step_number: int, file_name: str)
    step_started = Signal(int, str)

    # (file_name: str, part_number: int, max_parts: int)
    part_started = Signal(str, int, int)

    # (file_name: str, status_msg: str)
    test_progress = Signal(str, str)

    # (file_name: str, code_content: str, step_number: int)
    file_completed = Signal(str, str, int)

    # (all_files: dict[str, str])
    all_completed = Signal(dict)

    # Emitted on cancellation or failure
    worker_error = Signal(str)

    def __init__(
        self,
        ollama_url: str,
        model: str,
        plan: CodingPlan,
        num_ctx: int = 8192,
        max_continuations: int = 8,
        *,
        max_agents: int = 2,
        auto_test: bool = True,
        start_step: int = 1,
        existing_files: dict[str, str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = ollama_url
        self._model = model
        self._plan = plan
        self._num_ctx = num_ctx
        self._max_continuations = max_continuations
        self._max_agents = max(1, max_agents)
        self._auto_test = auto_test
        self._start_step = start_step
        self._stop = False
        self._lock = threading.Lock()
        self._generated_files: dict[str, str] = dict(existing_files or {})

    def stop(self) -> None:
        """Cancel code generation immediately."""
        self._stop = True

    def run(self) -> None:
        try:
            steps_to_run = [
                s for s in self._plan.steps
                if s.step >= self._start_step and not s.completed
            ]
            if not steps_to_run:
                self.all_completed.emit(self._generated_files)
                return

            work_queue: Queue[PlanStep] = Queue()
            for step in steps_to_run:
                work_queue.put(step)

            num_workers = min(self._max_agents, len(steps_to_run))

            def agent_worker(agent_id: int) -> None:
                while not self._stop:
                    try:
                        step = work_queue.get_nowait()
                    except Empty:
                        break

                    try:
                        self.step_started.emit(step.step, step.file)

                        with self._lock:
                            curr_files = dict(self._generated_files)

                        code = generate_single_file(
                            ollama_url=self._url,
                            model=self._model,
                            plan=self._plan,
                            step=step,
                            generated_files=curr_files,
                            num_ctx=self._num_ctx,
                            max_continuations=self._max_continuations,
                            on_progress=lambda part, max_p: self.part_started.emit(step.file, part, max_p),
                            stop_flag=lambda: self._stop,
                        )

                        if self._stop:
                            work_queue.task_done()
                            return

                        # Self-healing test phase if enabled
                        if self._auto_test and code:
                            self.test_progress.emit(step.file, f"🧪 Agent {agent_id}: Testing `{step.file}`…")
                            code, passed, msg = test_and_repair(
                                ollama_url=self._url,
                                model=self._model,
                                filename=step.file,
                                code=code,
                                step_desc=step.description,
                                all_files=curr_files,
                                num_ctx=self._num_ctx,
                                stop_flag=lambda: self._stop,
                                on_repair_status=lambda s: self.test_progress.emit(step.file, s),
                            )

                        if self._stop:
                            work_queue.task_done()
                            return

                        with self._lock:
                            self._generated_files[step.file] = code
                            step.completed = True

                        self.file_completed.emit(step.file, code, step.step)

                    except Exception as e:
                        self.worker_error.emit(f"Error on `{step.file}`: {e}")
                    finally:
                        work_queue.task_done()

            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(agent_worker, i + 1) for i in range(num_workers)]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as e:
                        self.worker_error.emit(f"Agent error: {e}")

            if self._stop:
                self.worker_error.emit("Code generation cancelled by user.")
            else:
                self.all_completed.emit(self._generated_files)

        except Exception as e:
            self.worker_error.emit(f"Error during code generation: {e}")
