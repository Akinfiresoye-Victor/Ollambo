# Project Bug Scan

**Date:** 2026-10-15  
**Scope:** First-party application source, UI, workers, tests, installers/build configuration, and behavior-relevant documentation. The virtual environment, third-party dependencies/assets, and generated files were excluded.  
**Outcome:** Four actionable bugs were identified. No application code was changed.

## Findings

### 1. Closing the window can terminate the application while a worker thread is still running

- **Severity:** High
- **Confidence:** High
- **Affected code:** [ui/chat_page.py:2782](ui/chat_page.py#L2782), [ui/worker_guard.py:32](ui/worker_guard.py#L32), [core/ollama_client.py:157](core/ollama_client.py#L157)
- **Trigger:** Close the application while a worker is blocked waiting for an Ollama stream or another long-running operation.
- **Evidence:** The close path requests worker shutdown but waits only 500 ms. The Ollama client can wait for a response for up to 300 seconds, and the worker may not be able to observe the stop request while blocked in that call.
- **Impact:** The window/application may destroy a still-running `QThread`, causing Qt's “QThread: Destroyed while thread is still running” failure or an abrupt process exit. Work in progress may be lost.

### 2. The Ollama server started by the setup worker can be left running after application exit

- **Severity:** Medium
- **Confidence:** High
- **Affected code:** [workers/setup_worker.py:61](workers/setup_worker.py#L61), [ui/main_window.py:35](ui/main_window.py#L35), [ui/main_window.py:118](ui/main_window.py#L118)
- **Trigger:** Start Ollama through the application's setup flow, then close the application.
- **Evidence:** The setup worker starts `ollama serve` but keeps the process handle in a local variable rather than assigning it to the main window's `_server_proc`. On exit, the main window's cleanup path issues `ollama stop` instead of terminating that specific process.
- **Impact:** An Ollama server process started by the application may continue running after the UI closes, consuming resources and leaving the user with an unexpected background service.

### 3. Editing a supported large file can silently truncate its contents

- **Severity:** Medium
- **Confidence:** High
- **Affected code:** [core/workspace.py:375](core/workspace.py#L375), [core/workspace.py:393](core/workspace.py#L393), [workers/code_worker.py:120](workers/code_worker.py#L120), [workers/code_worker.py:165](workers/code_worker.py#L165)
- **Trigger:** Ask the coding workflow to modify an existing file larger than 500 KB but within the workspace listing's 5 MB file-size limit.
- **Evidence:** `read_file()` reads at most 500,000 bytes, while workspace listing allows files up to 5 MB. The code worker uses the truncated content as input and subsequently writes the generated result back to the workspace file.
- **Impact:** The file's bytes after the 500 KB boundary can be lost when the generated version is written, even though the file was treated as an eligible workspace file.

### 4. Mermaid diagrams are unavailable in the advertised one-file build

- **Severity:** Low
- **Confidence:** High
- **Affected code:** [install.py:649](install.py#L649), [ui/report_viewer.py:49](ui/report_viewer.py#L49)
- **Trigger:** Build or run the application using the `--onefile` packaging option and open a report containing Mermaid diagrams.
- **Evidence:** The one-file build includes icon data but does not bundle `assets/mermaid.min.js`. The report viewer looks for that script under the bundle's `assets` directory.
- **Impact:** Mermaid diagrams in research reports do not render in one-file installations.

## Validation and limitations

- The test suite passed: **185 tests** via `python -m unittest discover -s tests -v`.
- Syntax parsing passed for **95 first-party Python files**.
- A separate `python -m pytest` attempt could not run because `pytest` is not installed in the available Python environments; unittest discovery did run the project tests successfully.
- Live Ollama behavior, interactive GUI shutdown, and packaged installer outputs were not exercised. Findings involving those paths are based on source-level behavior.
