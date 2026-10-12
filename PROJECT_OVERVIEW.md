  # 🦙 Ollambo — Comprehensive Project Overview & Capabilities

  > **Your Autonomous Local AI Coding Companion & Chat Assistant — Powered by Ollama**  
  > *100% Offline • Zero API Keys • Privacy-First • Native Desktop Architecture*

  ---

  ## 📑 Table of Contents
  1. [Executive Summary (Compact Overview)](#-executive-summary-compact-overview)
  2. [General Description & Architecture](#-general-description--architecture)
  3. [Core Capabilities & Features (Detailed Deep-Dive)](#-core-capabilities--features-detailed-deep-dive)
    - [Autonomous Task Classifier & Triple Modes](#1-autonomous-task-classifier--triple-modes)
    - [Plan-First Coding Architecture](#2-plan-first-coding-architecture)
    - [Background Builds & Resumable Sessions](#3-background-builds--resumable-sessions)
    - [Long Code Continuation & Stitching](#4-long-code-continuation--stitching)
    - [Autonomous Code Validation & Self-Healing](#5-autonomous-code-validation--self-healing)
    - [Keep-Alive Model Connection & Memory Management](#6-keep-alive-model-connection--memory-management)
    - [Smart Context Compaction](#7-smart-context-compaction)
    - [Zero-Flicker Streaming & Rich UI](#8-zero-flicker-streaming--rich-ui)
    - [Conversation History & Ghost-Chat Protection](#9-conversation-history--ghost-chat-protection)
    - [Interactive Retry & Recovery Flow](#10-interactive-retry--recovery-flow)
    - [Artifacts Panel & Export Options](#11-artifacts-panel--export-options)
    - [First-Run Setup Wizard](#12-first-run-setup-wizard)
  4. [Technology Stack & Directory Structure](#-technology-stack--directory-structure)
  5. [Current State vs. Roadmap](#-current-state-vs-roadmap)

  ---

  ## ⚡ Executive Summary (Compact Overview)

  **Ollambo** is a modern, dark-themed native desktop software engineering client and AI assistant designed to run exclusively against locally hosted [Ollama](https://ollama.com/) language models (such as `llama3.2`, `codellama`, `deepseek-coder`, `qwen2.5-coder`, and `mistral`). 

  Unlike conventional chat interfaces that simply spit out raw text snippets, Ollambo is an **autonomous software development environment** that:
  - **Plans Before Coding**: Deconstructs user requests into structured, multi-file software engineering blueprints.
  - **Writes Complete Projects**: Uses concurrent worker threads to scaffold, code, validate, and test full software applications file-by-file.
  - **Solves Context Limits**: Seamlessly recovers from output cutoffs using intelligent multi-part code continuation and AST signature tracking.
  - **Self-Heals Bugs**: Automatically runs headless syntax checks and tests on generated code, prompting the model to fix its own bugs before completion.
  - **Keeps Models Warm**: Maintains a dynamic keep-alive connection in RAM/VRAM with a 3-minute idle countdown timer for instant responses, automatically unloading when idle to save system resources.
  - **Multitasks in Background**: Allows building complex projects in the background while users continue chatting, with complete session checkpointing.
  - **Runs 100% Offline**: Requires zero cloud subscriptions, zero API tokens, and guarantees full data privacy.

  ---

  ## 🏛 General Description & Architecture

  Ollambo is built in Python utilizing **PySide6 (Qt6)** for its graphical front-end and a modular, decoupled engine for orchestration:

  ```
  ┌────────────────────────────────────────────────────────┐
  │                   Ollambo Desktop UI                   │
  │   (Chat Page • Sidebar • Plan Tab • Artifacts Panel)   │
  └──────────────────────────┬─────────────────────────────┘
                            │ Signals & Slots (Non-blocking)
  ┌──────────────────────────▼─────────────────────────────┐
  │                 Background Workers Pool                │
  │  (ChatWorker • PlanWorker • CodeWorker • SetupWorker)  │
  └────────────┬─────────────────────────────┬─────────────┘
              │ Task Orchestration          │ Persistent I/O
  ┌────────────▼──────────────┐ ┌────────────▼─────────────┐
  │       Core Engine         │ │      Local Storage       │
  │ • Task Classifier         │ │ • Atomic JSON Store      │
  │ • Planner & Code Writer   │ │ • Work Sessions Store    │
  │ • Code Tester & Validator │ │ • Settings & Config      │
  │ • Context Compaction Mgr  │ └──────────────────────────┘
  └────────────┬──────────────┘
              │ HTTP Connection Pooling (Keep-Alive)
  ┌────────────▼───────────────────────────────────────────┐
  │               Local Ollama REST API (11434)            │
  │         (llama3.2, deepseek-coder, qwen, etc.)         │
  └────────────────────────────────────────────────────────┘
  ```

  The system is strictly partitioned into three layers:
  1. **`ui/` (Presentation Layer)**: High-performance, zero-flicker widgets, custom Markdown renderer, smooth scroll handling, and responsive layout management.
  2. **`workers/` (Async Execution Layer)**: Dedicated `QThread` workers that execute long-running inference, network I/O, compilation, and downloads without ever freezing the main GUI thread.
  3. **`core/` (Business Logic Layer)**: Pure Python domain logic containing prompt engineering templates, AST analysis, context window token estimations, model lifetime managers, and file persistence.

  ---

  ## 🚀 Core Capabilities & Features (Detailed Deep-Dive)

  ### 1. Autonomous Task Classifier & Triple Modes
  Ollambo supports three operational modes accessible from the top control bar:
  - **🤖 Auto Detect Mode (Default)**: Employs a blazing-fast (`<0.1ms`), zero-network heuristic intent classifier. It differentiates between general conversations, software engineering tasks, and hybrid queries (e.g., *"Explain quicksort and write a script for it"*). By executing heuristics locally, it completely eliminates 5–15 second network classification freezes.
  - **💻 Code Mode**: Enforces the Plan-First software engineering pipeline regardless of prompt phrasing.
  - **💬 Chat Only Mode**: Standard conversational mode with zero project scaffolding.

  ### 2. Plan-First Coding Architecture
  When a coding task is submitted, Ollambo does not write raw code immediately:
  1. **Architect Planning**: The model first analyzes the requirements and outputs a structured blueprint (Goal, Assumptions, File List, and Step-by-Step implementation plan).
  2. **Interactive Plan Review**: The UI automatically switches to the **📋 Plan Tab**, displaying the sequential checklist with file targets.
  3. **User Approval**: The user reviews the plan and clicks **"Approve & Start Coding"** (or edits the prompt).

  ### 3. Background Builds & Resumable Sessions
  - **Decoupled Execution**: Once approved, code generation runs asynchronously in `CodeWorker`. Users can freely switch tabs, browse chat history, or start a new conversation.
  - **Work-In-Progress Banner**: The sidebar displays a gold **"🔨 Work In Progress"** indicator showing active builds. Clicking it jumps directly back to the active session.
  - **Crash-Proof Checkpoints**: Every completed file and plan step is saved atomically to disk under `%LOCALAPPDATA%/ollambo/sessions/`. If the application closes, the session can be resumed from the exact step.

  ### 4. Long Code Continuation & Stitching
  Local models are inherently constrained by output context windows (token limits). Traditional tools truncate code mid-file:
  - **Continuation Detection**: Ollambo monitors Ollama's `done_reason`. When `length` is detected, it automatically issues follow-up continuation prompts up to `max_continuations` (default: 8 parts).
  - **Intelligent Stitching**: Automatically strips repeated code fences (` ``` `), detects overlapping lines, and stitches partial snippets into a cohesive source file.
  - **AST Project Memory**: When generating subsequent files, Ollambo extracts top-level Python `class` and `def` signatures of previously generated files using Python's `ast` parser, providing essential architectural context without bloating token budgets.

  ### 5. Autonomous Code Validation & Self-Healing
  - **Automatic Code Tester**: After files are generated, Ollambo uses `code_tester.py` to compile Python source code via `compile()` to check for syntax errors.
  - **Self-Healing Loop**: If a syntax error is discovered, the tester captures the line number, error message, and traceback, sends them back to the model, and requests an in-place fix before marking the step completed.

  ### 6. Keep-Alive Model Connection & Memory Management
  - **Warm-Loaded Inference**: Ollama models traditionally take 4–10 seconds to load into memory on every request. Ollambo instructs Ollama to keep the model loaded (`keep_alive="3m"`).
  - **Dynamic Inactivity Countdown**: The top bar displays a real-time badge:
    - `🟢 Active (3m 00s)` counting down when idle.
    - `⚡ Working…` during generation.
    - Automatically resets to 3 minutes whenever a new prompt or retry is sent.
  - **Resource Recovery**: After 3 minutes of inactivity, Ollambo cleanly issues an unload call (`keep_alive=0`), releasing system RAM and GPU VRAM.
  - **Manual Preload**: Includes a `⚡ Preload` button to warm the model before starting work.

  ### 7. Smart Context Compaction
  - **Token Tracking**: Estimates tokens using character-to-token heuristics based on active model context limits (e.g., 8,192 tokens).
  - **Proactive Compaction**: If conversation history exceeds 70% of the active context length, a background worker summarizes older dialogue turns into a condensed recap, preserving system instructions and the most recent messages while preventing out-of-memory errors.

  ### 8. Zero-Flicker Streaming & Rich UI
  - **In-Place Segment Updating**: Tokens stream at ~40 FPS into differential widgets without destroying and recreating layout components.
  - **Dark Mode Rich Markdown**: Native formatting for headings, bulleted lists, bold text, italics, blockquotes, and inline badges.
  - **Monospace Code Containers**: Monospace code blocks with syntax language tags, clean borders, line limits, and one-click **"Copy"** buttons.
  - **Smart Scroll Stability**: When the user scrolls up to read earlier responses, autoscrolling is automatically paused so the viewport never jerks or fights the mouse wheel. Autoscroll resumes when the user scrolls near the bottom or submits a new message.

  ### 9. Conversation History & Ghost-Chat Protection
  - **Atomic JSON Storage**: Conversations are stored in individual JSON documents with atomic rename operations (`_atomic_write`), preventing corrupted files.
  - **Ghost-Chat Suppression**: Empty chats (no messages and no artifacts) are never saved to disk. `list_chats()` automatically prunes stale empty files.
  - **Instant Search & Delete**: Delete actions remove conversations cleanly without accidentally spawning replacement chats.

  ### 10. Interactive Retry & Recovery Flow
  - **"🔄 Retry Request" Button**: Automatically placed beneath the final message or cancelled request.
  - **Full Recovery**: If generation is stopped mid-way or fails, clicking **Retry Request** pops the failed response, restores the prompt, and re-executes generation cleanly.

  ### 11. Artifacts Panel & Export Options
  - **Tabbed File Viewer**: Displays all generated source files in an integrated multi-tab editor.
  - **One-Click Actions**:
    - **Copy Code**: Copies the active file's source code to the clipboard.
    - **Save File**: Exports the active file to a local destination.
    - **Download Zip**: Packages the entire project directory into a `.zip` archive for instant deployment.

  ### 12. First-Run Setup Wizard
  - Automatically detects if Ollama is installed and running on `localhost:11434`.
  - If missing, offers platform-specific installation assistance (Windows installer, macOS Homebrew/direct, Linux curl script).
  - Automatically discovers installed models and prompts the user to download recommended models (e.g., `llama3.2`, `codellama`).

  ---

  ## 📂 Technology Stack & Directory Structure

  ```
  ollambo/
  ├── main.py                     # Application bootstrap & Qt initialization
  ├── requirements.txt            # Python dependencies (PySide6, httpx, platformdirs)
  ├── README.md                   # Quickstart guide
  ├── PROJECT_OVERVIEW.md         # Full project architectural documentation
  │
  ├── core/                       # Pure logic & core subsystems
  │   ├── ollama_client.py        # Persistent HTTP connection pool to Ollama REST API
  │   ├── task_classifier.py      # <0.1ms heuristic intent classifier (Chat/Code/Both)
  │   ├── planner.py              # Structured JSON planning engine & parser
  │   ├── code_writer.py          # Sequential multi-file generator & signature builder
  │   ├── code_tester.py          # Headless syntax verification & self-healing tester
  │   ├── context_manager.py      # Token estimator & sliding-window compaction
  │   ├── build_session.py        # Work session persistence & checkpoint management
  │   ├── storage.py              # Atomic JSON persistence for chats & settings
  │   ├── walkthrough.py          # End-of-build project summary & execution guide
  │   ├── markdown_formatter.py   # High-performance markdown-to-HTML formatter
  │   ├── setup_checker.py        # Environment & Ollama health verification
  │   ├── installer.py            # OS-specific Ollama installer scripts
  │   ├── prompts.py              # System prompts & engineering templates
  │   ├── assets.py               # Application icons and branding assets
  │   └── tools_registry.py       # Future tool & MCP server interface
  │
  ├── ui/                         # PySide6 GUI Widgets
  │   ├── main_window.py          # Root window shell & navigation manager
  │   ├── chat_page.py            # Core chat interface, tabs, & orchestration
  │   ├── chat_sidebar.py         # Chat history list, WIP badges, delete & settings
  │   ├── chat_top_bar.py         # Model selector, mode picker, keep-alive countdown
  │   ├── message_widget.py       # Zero-flicker chat bubbles & code block widgets
  │   ├── plan_panel.py           # Plan tab: interactive step checklist & progress
  │   ├── artifacts_panel.py      # Artifacts tab: tabbed file viewer, copy, & zip export
  │   ├── settings_dialog.py      # Configuration dialog (context length, agents, test)
  │   ├── setup_wizard.py         # First-run guided setup wizard
  │   └── terminal_widget.py      # Live streaming log & command output viewer
  │
  ├── workers/                    # Background QThread workers
  │   ├── chat_worker.py          # Streaming chat completions & auto-compaction
  │   ├── code_worker.py          # Autonomous multi-file generation & self-healing
  │   ├── plan_worker.py          # Background plan generation
  │   ├── setup_worker.py         # System health & port probe thread
  │   ├── install_worker.py       # Background Ollama download & install runner
  │   ├── pull_worker.py          # Real-time model pull progress listener
  │   └── models_worker.py        # Model list polling worker
  │
  └── tests/                      # Unit test suite (zero UI dependencies)
      ├── test_context_manager.py # Verifies token estimation & compaction logic
      ├── test_continuation.py    # Verifies multi-part code stitching
      └── test_planner.py         # Verifies JSON schema parsing & plan fallback
  ```

  ---

  ## 🔮 Current State vs. Roadmap

  | Feature Area | Current State | Future Roadmap |
  | :--- | :--- | :--- |
  | **Model Hosting** | 100% Local Ollama REST API | Multi-endpoint support (vLLM, LM Studio) |
  | **Code Generation** | Sequential file generation with signature extraction | Multi-agent parallel subagents with file lock |
  | **Verification** | AST syntax check & self-healing loop | Dockerized sandbox test execution |
  | **Continuation** | Automatic 8-part code continuation & stitching | Infinite file streaming |
  | **Memory** | 3-minute keep-alive countdown with auto-unload | Dynamic VRAM-aware GPU cache tiering |
  | **Tools** | Foundation registry ready (`tools_registry.py`) | MCP (Model Context Protocol) server support |
  | **Project Export** | Direct copy, single file save, & `.zip` export | Direct Git commit & GitHub push |

  ---
  *Created for Ollambo. Fully offline, private, and autonomous.*
