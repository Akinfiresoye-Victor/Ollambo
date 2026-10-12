# 🦙 Ollambo

**Your autonomous local AI coding companion & chat assistant — powered by Ollama.**  
*100% Offline • Zero API Keys • Zero Cloud Subscriptions • Full Privacy*

Ollambo is a cross-platform desktop application that runs directly on your computer, connecting to locally hosted models (e.g. `llama3.2`, `codellama`, `deepseek-coder`, `qwen2.5-coder`). It works seamlessly on Windows, macOS, and Linux.

---

## 🚀 Fast Installation (Standalone Desktop App)

You can build and install Ollambo as a standalone desktop application with a single terminal command:

### 1. One-Command Installer
```bash
python install.py
```

- **Windows (Double-Click)**: Simply double-click `install.bat` or run `.\install.ps1`.
- **macOS / Linux**: Run `./install.sh`.

#### 📁 Clean Workspace & Custom Location:
When launched, `install.py` asks where you would like Ollambo to be installed:
- **Default Location**: Installs to `%LOCALAPPDATA%\Programs\Ollambo` on Windows (`~/.local/share/ollambo` on Linux, `/Applications/Ollambo` on macOS).
- **Browse with File Explorer**: Press `[B]` to open a native File Explorer window to choose any directory you want.
- **Zero Clutter**: Any temporary build files (`build/` and `dist/`) are automatically cleaned up afterward so your working directory / git repository stays completely clean!
- **Desktop & Start Menu Shortcuts**: Automatically adds a desktop shortcut and a Windows Start Menu shortcut pointing to your installed app.

### Installer Options
```bash
python install.py                   # Interactive install (prompts for location & B to browse)
python install.py --dir "C:\Apps"   # Install directly to a specific folder
python install.py --onefile         # Package into a single standalone .exe file
python install.py --dev             # Install in editable developer mode (pip install -e .)
python install.py --no-shortcut     # Skip creating Windows Desktop/Start Menu shortcuts
```

---

## 💻 Developer Setup (Running from Source)

If you prefer to run and modify the source code directly:

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/ollambo.git
cd ollambo

# 2. Create and activate a virtual environment (recommended)
# Windows:
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux:
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch Ollambo
python main.py
```

Alternatively, install in editable mode to launch with the `ollambo` command anywhere:
```bash
pip install -e .
ollambo
```

---

## 📋 System Requirements

- **Python 3.9 – 3.14** (PySide6 6.10+ supports Python 3.14; PySide6 6.8+ supports Python 3.9–3.13)
- **Ollama**: Installed and running locally ([Download Ollama](https://ollama.com/)).  
  *If Ollama is not installed, Ollambo's first-run setup wizard will detect it and guide you through installation.*

---

## 🌟 Key Features

- **Autonomous Task Classification**: Instant (`<0.1ms`) intent detection routing prompts between Chat, Code, or Hybrid modes without latency.
- **Plan-First Coding Architecture**: Breaks complex tasks into structured roadmaps (Goal, Assumptions, Files, Steps) before writing code.
- **Background Builds**: Write entire multi-file projects in the background while continuing conversations in other tabs.
- **Long Code Continuation**: Overcomes context output cutoffs by automatically continuing and stitching truncated code blocks.
- **Autonomous Self-Healing**: Automatically runs syntax verification on generated files and prompts the model to self-correct bugs.
- **Keep-Alive Model Window**: Keeps models resident in RAM/VRAM with a 3-minute idle countdown timer for instant responses, automatically unloading when idle to save system memory.
- **Smart Context Compaction & Progress**: Checks tokens proactively and compacts conversation history with real-time progress indicators when exceeding 75% of context limit, using instant extractive summaries to eliminate wait times.
- **Zero-Flicker Streaming UI**: Differential Markdown and monospace code rendering with a one-click "Copy" button.
- **Artifacts Panel**: View, copy, save, or download entire projects as a `.zip` archive.
- **One-Click Retry**: Re-run or resume interrupted prompts instantly with `🔄 Retry Request`.

---

## 🧪 Running Unit Tests

Ollambo includes standalone pure-logic unit tests (zero GUI dependencies):

```bash
python -m unittest discover tests
```

---

## 📂 Project Structure

```
ollambo/
├── main.py                 # Application entry point
├── install.py              # Standalone desktop installer & packager
├── install.bat             # Windows one-click batch installer
├── install.ps1             # Windows PowerShell installer
├── install.sh              # macOS/Linux shell installer
├── setup.py                # Package setup for `pip install -e .`
├── ollambo.spec            # PyInstaller build specification
├── requirements.txt        # Pinned dependencies
├── PROJECT_OVERVIEW.md     # Deep-dive architecture & feature guide
│
├── core/                   # Pure business logic (no UI dependencies)
│   ├── ollama_client.py    # Persistent HTTP connection pool to Ollama
│   ├── task_classifier.py  # Fast heuristic intent classifier
│   ├── planner.py          # Structured plan generator
│   ├── code_writer.py      # Multi-file writer & signature analyzer
│   ├── code_tester.py      # Automated syntax validation & self-healing
│   ├── context_manager.py  # Token counting & proactive compaction
│   ├── build_session.py    # Persistent work session checkpoints
│   ├── storage.py          # Atomic JSON persistence & ghost-chat pruning
│   ├── walkthrough.py      # Post-build project summary generator
│   └── markdown_formatter.py # High-performance Markdown parser
│
├── ui/                     # PySide6 Desktop Interface
│   ├── main_window.py      # Main window shell & tab navigation
│   ├── chat_page.py        # Chat orchestrator, streaming, and tabs
│   ├── chat_sidebar.py     # Conversation list & WIP banner
│   ├── chat_top_bar.py     # Model picker, mode switcher, keep-alive timer
│   ├── message_widget.py   # Stable streaming bubbles & code containers
│   ├── plan_panel.py       # Plan checklist & approve controls
│   ├── artifacts_panel.py  # Generated code viewer & zip export
│   └── setup_wizard.py     # First-run guided Ollama setup
│
├── workers/                # Non-blocking background QThread workers
│   ├── chat_worker.py      # Streaming chat completions
│   ├── code_worker.py      # Multi-file background generator
│   └── plan_worker.py      # Project plan generator
│
└── tests/                  # Unit test suite
    ├── test_context_manager.py
    ├── test_continuation.py
    └── test_planner.py
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
