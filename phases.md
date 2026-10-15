OLLAMBO FULL UI/UX REDESIGN AND CHAT-STATE REBUILD

ROLE AND WORKING RULES

You are a senior desktop engineer (PySide6 / Qt6, Python 3.9 to 3.14) and a product designer. You are redesigning the entire front end of Ollambo, a local-first desktop AI coding assistant that talks to Ollama. Before changing anything, read the whole repository: main.py, ui/, workers/, core/, tests/, ollambo.spec, install.py, requirements.txt.

Hard rules:
1. Do not break behaviour. Everything that works today must keep working: plan-first coding, background builds and resume, long-code continuation, self-healing tests, keep-alive countdown and unload, context compaction, Research mode (plan, engine, writer, resume), workspaces and attachments, retry, AI chat titles, setup wizard, installer, PyInstaller packaging.
2. core/ and workers/ are the engine. Do not change public function signatures, Signal names or Signal argument lists. Allowed: new files, new optional parameters with defaults, additive dict keys, and the specific exceptions named below.
3. Work in the numbered phases below, in order. After each phase: run "python -m unittest discover tests", launch the app with "python main.py", run that phase's manual checklist, then STOP and send me a short report (what changed, files touched, what you verified, anything you were unsure about). Do not start the next phase until I reply "continue".
4. No big-bang rewrites. Never replace a 1000-line file in one edit. Build the new piece next to the old one, move callers over one at a time, then delete the old code only after grep proves nothing references it.
5. No new dependencies. No QML, no web view for the main UI, no pip packages. PySide6.QtSvg is allowed (it ships with PySide6). QtWebEngine stays only inside the report viewer and stays lazy.
6. Explain as you go. Add one or two plain-English sentences for every non-obvious decision. I am a Python developer who wants to understand each change, so no mystery diffs.
7. Keep "from __future__ import annotations" at the top of every file and avoid syntax newer than Python 3.9.
8. If something is ambiguous, ask me. Do not guess.

TESTS AND NAMES THAT MUST KEEP WORKING

Before Phase 1, run the full test suite and record which tests already fail. Then keep the rest green. These tests touch UI code, so these names are protected:
- ChatTopBar: attributes _is_connected, _is_working, _keep_alive_timer, _seconds_remaining, lbl_connection; methods set_working, set_idle, set_disconnected, _on_timer_tick. The lbl_connection text must still contain "Standby" when disconnected, "Active" when working, and "03:00" right after set_idle with a 180 second window.
- ArtifactsPanel: add_file(), _files, _tree_items (keys include directory paths like "src", "src/utils" and full file paths).
- ChatMessageWidget: get_content(), set_content(text, is_final), _blocks, _content_layout.
- core.markdown_formatter.markdown_to_html: tests assert the substrings "<h1", "<h2", "<h3", "<b style=", "<i style=", "font-family: Consolas", "<a href='https://test.com'", "<ul", "<ol", "<li style=", "border-left: 3px solid", "<hr". You may restyle colors, but keep these substrings, or update the test in the same commit and tell me.
- ui.report_viewer.markdown_to_report_html: keep the signature and the security behaviour (raw HTML escaped, only details/summary re-enabled, Mermaid block becomes <pre class="mermaid">).
- core/research_engine.py and core/research_writer.py: tests assert emoji text such as "Web Search:" with its globe emoji. Do NOT edit those strings. Remove emojis from the UI by stripping them at display time with a helper (ui/text_utils.py: strip_emoji_prefix) and showing an icon instead.

DESIGN DIRECTION (applies to every phase)

Goal: a calm, quiet, professional tool, closer to Claude's desktop app than to a hackathon demo. It must never look vibe-coded. Rules:
- Dark, warm-neutral theme with exactly ONE accent color. No gradients, no glow, no neon, no rainbow status colors, no drop shadows (they are slow in Qt). Separate surfaces with 1px low-contrast borders and small background shifts.
- Hierarchy comes from type size and weight, not color. At most three text colors: primary, secondary, tertiary.
- Sentence-case labels. No ALL CAPS headers. Short, human microcopy ("Couldn't reach Ollama. Check that it's running." plus an action button).
- Zero emojis in the interface chrome: buttons, tabs, labels, tooltips, status text, empty states, dialogs, sidebar rows. Use the small line icons from Phase 3. Text the model itself writes is left untouched.
- One spacing scale (4, 8, 12, 16, 24, 32), two corner radii (10 for controls, 16 for large cards and the composer), one control height (32), one icon style.
- Every icon-only button has a tooltip and a visible keyboard focus ring.
- Text contrast at least 4.5:1, smallest font 12px.
- Every screen needs a designed empty state, loading state and error state.

Layout wireframe:

+---------------+--------------------------------------------+------------------+
| Sidebar 264px | Top bar: [toggle] [model] [mode] ... [pill]| Inspector 420px  |
| brand         |--------------------------------------------| (collapsible)    |
| + New chat    |                                            |  Plan | Files    |
| Search        |    centered column, max width 760px        |                  |
| Today         |    messages                                |                  |
|   chat row    |                                            |                  |
|   chat row    |    +--------------------------------+      |                  |
| Yesterday     |    | composer card                  |      |                  |
|   ...         |    +--------------------------------+      |                  |
| Library       |                                            |                  |
| Settings      |                                            |                  |
+---------------+--------------------------------------------+------------------+

PHASE 0: BASELINE
- Create a git branch "ui-redesign".
- Run the full test suite and list failing tests. Do not fix them yet.
- Grep and list every place that uses: self.tabs.setCurrentIndex / setCurrentWidget, btn_send, btn_stop, setStyleSheet, and every lambda connected to a worker signal in ui/. Send me the counts. We will need them later.
STOP and report.

PHASE 1: FIX THE CHAT BUG FIRST (no visual changes yet)

Symptom: start a reply in chat A, switch to chat B, come back to A. The conversation is missing the reply, generation is gone, sometimes the whole chat looks empty.

Root causes (verified by reading ui/chat_page.py):
a) ChatPage keeps ONE conversation's state in single variables: self._messages, self._current_assistant_widget, self._current_assistant_text, self._active_chat_worker, self._active_plan_worker, self._active_research_worker, self._last_user_prompt. Switching chats overwrites them.
b) _load_conversation() and _start_new_chat() call _stop_chat_generation(), which stops the worker and sets the widget to None. A stopped ChatWorker still emits chat_finished with the partial text, and _on_chat_finished appends it to self._messages, which by then belongs to the NEW chat, and saves it under the wrong id. Late tokens also leak into _current_assistant_text. The assistant reply is only added to messages when generation finishes, so the saved chat holds only the user's question.
c) _on_compaction_performed() replaces self._messages with the compacted list and saves it. Older messages are permanently deleted from disk, and the role "system" summary is later rendered like an Ollambo message.
d) _on_worker_error() shows errors but never stores them, so they vanish on switch.
e) _clear_history_ui() destroys and rebuilds all message widgets on every switch, so a streaming widget cannot survive.

Required architecture. Principle: the model is the truth and the view is a mirror.
1. core/chat_session.py (pure Python, no Qt imports). class ChatSession with: id, title, ai_title, messages (list of dicts with role and content plus optional keys such as attachments, stopped, error), streaming buffer, status ("idle", "generating", "planning", "researching", "building", "error"), summary_text, summary_upto, workspace_id, research_project_id, plan (dict or None), unread, updated timestamp. Methods roughly: add_user(), begin_assistant(), append_token(), finish_assistant(), stop_assistant() (keeps the partial text as a message with stopped=True), add_error(), model_messages(), apply_compaction(), to_dict(), from_dict(). Tokens arriving for a session that is no longer generating are ignored.
2. ui/session_controller.py: class SessionController(QObject), one per open conversation. It owns that conversation's workers (chat, plan, research, classify, route, title, and the code build state that currently lives in _build_running and _building_*). It connects worker signals to its OWN @Slot methods, as bound methods, never lambdas. Why: in PySide6 a lambda connected to a QThread signal can run on the worker thread, and touching widgets from there causes random glitches. A bound slot on a QObject living in the main thread is queued onto the main thread correctly, and because the controller holds its session, every signal is automatically routed to the right chat. Convert the existing UI-touching lambdas (for example in _dispatch_task, _on_plan_approved, _on_plan_ready) the same way. The one-build-at-a-time rule stays.
3. ui/chat_view.py: class ChatView(QWidget), one per conversation, with its own scroll area, history layout, stream flush timer (keep 100ms) and the existing scroll-stability logic (_user_scrolled_away and the debounced scroll). Views live inside a QStackedWidget in ChatPage and are created lazily on first open. Switching chats is only setCurrentWidget(): nothing is destroyed and nothing stops. Keep at most 6 views alive (least recently used are dropped first). Never drop the view of a session that is generating. A dropped view is rebuilt from session.messages plus the streaming buffer. Keep the "load earlier messages" lazy loading (40 at a time).
4. ChatPage becomes a thin shell holding the sidebar, the stack of views, the composer, the top bar and the inspector. It routes user actions to the ACTIVE session's controller. No conversation data lives in ChatPage attributes any more.
5. Switching chats or creating a new chat must NEVER call worker.stop(). Only the Stop button (or Esc) stops the active session's work.
6. Stopped generation: keep the partial text as an assistant message with stopped=True and show a quiet "Stopped" note under it. Errors are stored as an assistant message with error=True and offer Retry.
7. Compaction must never modify or delete session.messages. Store summary_text and summary_upto instead. model_messages() returns [system summary message] + messages[summary_upto:]. In the handler for ChatWorker.compaction_performed, derive these two values from the list the worker sends (find the system message that starts with "[Summary of earlier conversation:" and count the non-system messages after it). Always send model_messages() to ChatWorker. The token meter also uses model_messages(). Persist summary_text and summary_upto in the chat JSON. The UI never renders role "system".
8. Allowed worker change: in ChatWorker.run and ChatPage._strip_attachments_from_messages, send only {"role", "content"} to Ollama (a whitelist instead of removing just "attachments"), so keys like stopped or error never reach Ollama.
9. Crash safety: while a reply streams, save the partial text into the chat JSON under the key "draft_assistant" at most every 3 seconds (atomic save_chat). On load, if "draft_assistant" exists and no worker runs for that session, convert it into a stopped assistant message and clear the key.
10. Sidebar: each row shows state from session.status and session.unread: a small spinner while generating, planning, researching or building, and a small dot when a background chat finished and has not been viewed. Replace the hammer emoji. Make core/storage.list_chats() also return "updated" (additive) so rows can be grouped Today / Yesterday / Previous 7 days / Older, and make upsert_chat move the row to the top when a chat gets new activity.
11. MainWindow.closeEvent: stop all controllers cleanly and flush every session to disk.

Related bugs to fix in this phase, one small commit each:
- core/web_research.extract_urls treats file names as websites: "main.py", "app.py", "README.md", "index.js" match the bare-domain regex, so a normal coding prompt in Auto or Chat mode can trigger a web fetch or the false "Web access is disabled" message. For bare domains (no http:// or https:// prefix) reject names whose last part is a common file extension (py js ts tsx jsx md json txt html css yml yaml toml ini cfg csv sh bat ps1 java c cpp h go rs rb php sql log lock xml zip). tests/test_url_helpers.py must still pass.
- _stop_generation and similar code call isRunning() on workers that deleteLater() may already have destroyed, which raises "Internal C++ object already deleted". Clear the reference in the finished handler and guard every call.
- Retry removes "the last assistant widget" and can delete a notice row instead of the reply. With the new design, retry works on session.messages and rebuilds the tail of the view.
- ChatTopBar.set_low_power_mode() is never called, so low-power mode never locks research depth. Also, after Settings > Save the keep-alive duration is not re-applied to the countdown. Wire both. Make the "3m" text in the connection badge and the Preload tooltip use the real configured duration (the Preload tooltip currently says 5m).

Acceptance checks for Phase 1 (do all of them):
A. Start a long reply in chat 1, switch to chat 2 mid-stream, send a message in chat 2, return to chat 1. The reply kept streaming and is complete, and chat 2 is untouched.
B. Press Stop in chat 1, switch away and back. The partial text is still there, marked Stopped, and Retry works.
C. Push a conversation past the compaction threshold. Every earlier message is still visible and still there after restarting the app. No "system" bubble appears.
D. Kill the app mid-stream and restart. The partial text is recovered as a stopped message.
E. Switch chats during a Research run and during a code build. Both keep running and show their progress when I return.
F. Delete the active chat, and delete a chat that is generating in the background (its worker is stopped first). Create a new chat while another one generates.
G. New tests/test_chat_session.py (pure, no Qt) covering: token append, finish, stop keeps partial, late tokens ignored, model_messages after compaction, to_dict/from_dict round trip, error storage.
STOP and report.

PHASE 2: DESIGN SYSTEM (one place for every color, font and style)

Create ui/theme/__init__.py, ui/theme/tokens.py, ui/theme/qss.py.

tokens.py: a frozen dataclass Theme with colors, fonts, spacing, radii and sizes, plus one DARK instance. Structure it so a light theme could be added later, but do not build one now. These hex values are the only hex colors allowed anywhere in the repo:
- window background #1C1B1A, sidebar #171615, surface (cards, composer, inputs) #242322, raised (hover, menus) #2D2B2A
- border subtle #302E2C, border default #3B3836
- text primary #ECE9E4, secondary #B4AFA8, tertiary #8A857E
- accent #CC7E5B, accent hover #D98F6E, text on accent #1C1B1A
- success #7FB069, warning #E0B44C, danger #E5737B, info #6FA8DC (used sparingly, only for tiny status dots and error text)
- code background #161514, code text #D8D4CD
Fonts: UI stack "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue", Arial. Mono stack "Cascadia Code", "JetBrains Mono", "SF Mono", "Consolas", monospace. No bundled font files. Type scale in px: caption 12, body 14, chat text 15, title 18, hero 24.
Sizes: control height 32, sidebar 264, content max width 760, inspector 420, icon sizes 16 / 18 / 20.

qss.py: build_stylesheet(theme) returns ONE application-wide stylesheet covering QWidget base, QFrame#Card, QPushButton (default is a quiet secondary button, plus [variant="primary"], [variant="ghost"], [variant="danger"], [iconOnly="true"]), QToolButton, QLineEdit, QTextEdit, QPlainTextEdit, QComboBox (drop-down and popup list), QListWidget, QTreeWidget, QTableWidget and QHeaderView, QScrollBar (slim 8px, transparent track, no arrows), QSplitter handle, QMenu, QToolTip, QDialog, QMessageBox, QInputDialog, QProgressBar (4px high), QCheckBox, QSpinBox, and QLabel roles through a property (role = muted, title, caption, code).
apply_theme(app): sets Fusion style, builds a QPalette from the tokens (Window, Base, Text, Button, ButtonText, Highlight, HighlightedText, ToolTipBase, PlaceholderText and the Disabled group) so native dialogs (QFileDialog, QMessageBox, QInputDialog) match, sets the application font, applies the stylesheet, and calls core.markdown_formatter.set_palette(...). Add that set_palette function to markdown_formatter (additive, with defaults equal to the current look), so core never imports ui. Call apply_theme once in main.py right after QApplication is created.
Helper set_variant(widget, "primary") that sets the dynamic property and re-polishes the widget (style().unpolish then polish).

QSS is not CSS. Do not use: box-shadow, transition, var(), rem, flex, grid, gap, line-height, backdrop-filter. Gradients are qlineargradient only (and we are not using them). Use px sizes, :hover, :pressed, :focus, :disabled pseudo-states and rgba(r,g,b,a) with alpha 0 to 255. In rich text (QLabel, QTextBrowser) only a small CSS 2.1 subset works, and border-radius on spans is ignored. Check every style visually.

Then remove inline color styling: grep "setStyleSheet(" in chat_page.py, chat_sidebar.py, chat_top_bar.py, message_widget.py, plan_panel.py, research_plan_panel.py, research_panel.py, artifacts_panel.py, setup_wizard.py, terminal_widget.py, report_viewer.py and replace hardcoded colors with objectName, role or variant properties styled in qss.py. Make the report viewer CSS build from the same tokens.
Check: the app looks coherent and dark everywhere including every dialog. Only ui/theme/tokens.py contains hex colors (grep "#[0-9a-fA-F]{6}" outside it).
STOP and report.

PHASE 3: ICONS (replace emojis)

Create ui/icons.py. Draw icons as inline SVG strings, line style, 24px grid, 1.75px stroke, round caps and joins (the Lucide style). Render with QSvgRenderer into a QIcon, tinted with a theme color, devicePixelRatio aware, cached per (name, color, size). Use icon(name, color=None, size=18). Icons needed: plus, arrow-up (send), square (stop), settings, trash, pencil, copy, check, chevron-down, chevron-right, search, folder, file, file-code, refresh, bolt (preload), globe, message, list-checks (plan), package (files), flask (research), more-horizontal, x, download, external-link, alert-triangle, cpu, sidebar-left, panel-right, loader. Also a Spinner widget (QTimer at about 16 fps that rotates the loader icon, paused when hidden or when low_power_mode is on). Add "PySide6.QtSvg" to the hiddenimports in ollambo.spec.
Create ui/text_utils.py with strip_emoji_prefix(text) that returns the text without a leading emoji, plus a small category guess (search, fetch, read, check, warn) so progress lines can show the right icon.
Replace every emoji in UI code (tab titles, buttons, sidebar, status and progress text, notice rows, research panel labels, setup wizard status icons, settings labels, walkthrough footer in core/walkthrough.py, whose tests do not assert emojis). Do not touch the strings listed in the protected names section above.
STOP and report.

PHASE 4: APP SHELL AND NAVIGATION

- MainWindow: default 1200x780, minimum 1040x680, remember geometry and splitter sizes between launches with QSettings. Keep the native title bar.
- Sidebar (264px, collapsible to 0 with a 160ms QPropertyAnimation; no animation in low-power mode): small logo mark and wordmark, a primary "New chat" row with the plus icon, a search field that filters chat titles locally, grouped chat list (Today, Yesterday, Previous 7 days, Older) with elided titles, per-row state indicator (Phase 1), a hover "more" button with Rename (inline editing, set ai_title=True) and Delete (styled confirm dialog), then at the bottom a "Library" row (opens the research library page) and a "Settings" row. Remove the separate Delete and Settings buttons and the gold "Work In Progress" banner. The sidebar WIP behaviour (click to jump to the active build) becomes the row state indicator plus a tooltip.
- Replace the QTabWidget (Chat, Plan, Artifacts, Research) with: the chat in the center, a collapsible Inspector panel on the right (segmented control: Plan, Files), and the Research Library as its own full page reachable from the sidebar (a QStackedWidget in ChatPage or MainWindow).
- Create a small Navigator facade (ui/navigation.py) with show_chat(), show_plan(), show_files(), show_library(). Replace every self.tabs.setCurrentIndex / setCurrentWidget call (the Phase 0 grep list) with it, so no code knows about tab indexes. The Inspector opens automatically when a plan is ready or a build starts, and never steals focus from the composer.
- Keyboard shortcuts: Ctrl+N new chat, Ctrl+K focus chat search, Ctrl+B toggle sidebar, Ctrl+Shift+B toggle inspector, Ctrl+comma settings, Esc stop the active generation.
Check: switching pages and chats never loses state; the app still builds and resumes plans.
STOP and report.

PHASE 5: CHAT EXPERIENCE

Rebuild message rendering without changing the ChatMessageWidget API listed above.
- Centered column, max width 760, 24px between turns. User message: right-aligned rounded block (radius 16, surface color, max 80% width). Assistant message: no frame, no repeated name header, just a 20px logo mark on the left and text on the background. Remove the colored role names.
- Text segments use a read-only QTextBrowser subclass (ui/widgets/auto_text_view.py): transparent, no frame, no scrollbars, height follows document size and is recomputed on width changes, wheel events ignored so the outer scroll area scrolls, open external links with the default browser. Use real line-height through the rich-text paragraph style. This replaces QLabel word-wrap, which clips and jumps in scroll areas.
- Streaming must not jump at the end: render through the SAME renderer while streaming (no plain-text phase followed by a rich-text phase). Cache the HTML of closed blocks and re-render only the last open block on each 100ms flush. Keep the existing closed-segment cache in _parse_segments.
- Code blocks (CodeBlockWidget): header with language label and copy icon (the icon turns into a check for 1.5s). Size to content up to 28 lines, then horizontal scroll only; never trap the mouse wheel (forward wheel events to the outer scroll area). Add an "Expand" toggle for long blocks. Basic token coloring using a small QSyntaxHighlighter for Python, JS/TS, JSON, HTML, CSS, shell, using theme colors; disable it for blocks over 400 lines.
- "Thinking" state: a small animated three-dot indicator, not italic text. The Generating counter in the status area counts chunks, not tokens, so label it honestly (use eval_count when known).
- Hover action row under each assistant message (icon-only ghost buttons with tooltips): Copy, and Retry on the last message only. This replaces the separate retry row. Stopped and error states show a quiet note with a Retry button.
- App notices (for example "Continuing in project X") become a slim centered caption line with a small icon, not a fake assistant message.
- Empty state for a new chat: centered logo mark, heading "What are we building?", the composer in the middle of the screen, and four plain-text suggestion chips with icons (Plan a Python project, Explain a concept, Research a topic, Fix a traceback) that fill the composer. Once the first message is sent, the composer animates (or simply moves) to the bottom.
- Smart scroll behaviour stays: do not auto-scroll if the user scrolled up. Add a small floating "Jump to latest" icon button when scrolled away.
Check: very long replies scroll smoothly, no flicker, no clipped text, selection and copy work, resizing the window reflows text correctly.
STOP and report.

PHASE 6: COMPOSER, TOP BAR AND STATUS

Composer (ui/widgets/composer.py), a rounded card (radius 16) at the bottom of the column:
- Auto-growing multi-line input, 1 to 8 lines, Enter sends, Shift+Enter makes a newline (keep the current ChatInputEdit behaviour), placeholder in plain words.
- Bottom row: plus button on the left (keeps the Artifacts attach menu, the submenu content and chips behaviour), attachment chips above the input, and on the right ONE round button: arrow-up icon (accent) to send; it becomes a square stop icon while generating. Expose set_generating(bool). Replace every btn_send and btn_stop reference (Phase 0 list) with this API.
- If the active session is busy, allow typing but show "Waiting for the current reply" instead of silently dropping the message.
ChatTopBar stays the single source of truth for model, mode and depth state and keeps its whole public API (current_model, current_mode, research_depth, set_models, set_mode, set_internet_access, set_low_power_mode, update_token_meter, show_status, hide_status, set_working, set_idle, set_connected, set_disconnected, notify_activity and its signals). Only the visuals change: a slim 44px bar with the sidebar toggle, then quiet borderless dropdown chips for model and mode (the depth chip only in Research mode), then on the right: a globe icon when internet access is on, a connection pill (small colored dot plus text like "Ready 02:41", keeping the protected words Standby, Active and 03:00), a thin circular token meter (percentage in the tooltip, warning color only above 70%), a Preload icon button, and the inspector toggle.
Status area: the current status row appears and disappears and shifts the whole layout. Replace it with a fixed 2px progress line under the top bar and put the status text in a reserved line in the top bar, so nothing moves. Replace the string-splitting trick in _on_heartbeat_tick with a proper separate elapsed-time label.
Check: nothing in the window shifts when status text appears; model switch, mode switch, preload, token meter and the keep-alive countdown all still work.
STOP and report.

PHASE 7: INSPECTOR PANELS (PLAN AND FILES)

Plan panel:
- Header with the goal; steps as rows: step number, description, file path in a small monospace chip, status icon (pending circle, spinner while running, check when done). Remove the literal backticks in the text.
- Assumptions as a read-only collapsible section (the editable notes box does nothing, remove it).
- Primary "Approve and start coding" button; quiet "Reject" button. Reject must NOT call setVisible(False) on the panel (this currently leaves a blank tab). Show an empty state ("No plan yet. Ask for a project in Code mode.") instead.
- Keep PlanPanel's signals (plan_approved, plan_rejected) and methods (set_plan, mark_step_completed). Do the same restyle for ResearchPlanPanel (editable sub-questions stay).
Files panel (ArtifactsPanel, keep the protected API):
- Never call setVisible(False) on the whole panel when empty. Show an empty state.
- Project header with name, folder path (elided), icon buttons: Open folder, Export zip, Save all.
- File tree on the left with a filter field, code viewer on the right with a breadcrumb path header, line-number gutter and the same syntax highlighter (off above 2000 lines). Remove the duplicate file combo box. Selecting a tree item must not jump when files are added during a build, so only auto-select when the user hasn't picked a file.
- Replace modal "Saved" popups with toasts (Phase 9).
- When loading a workspace, avoid adding every file twice (_load_conversation currently adds files and then load_workspace clears and adds them again).
- Build progress: inside the chat, a BuildProgressCard (ui/widgets/progress_card.py) lists the plan's files with status icons and the current file, replacing text-only status.
STOP and report.

PHASE 8: RESEARCH LIBRARY AND LIVE PROGRESS

- Library page: clean table with a status chip (small dot plus text, not recolored text), toolbar icon buttons (Open, Resume, Rename, Export, Folder) and a row menu. Fix the selection logic: when nothing is selected, disable the actions; Resume must reset its label after a completed project is deselected.
- Stage tracker: a slim 5-step stepper (Planning, Searching, Reading, Checking facts, Writing) drawn with dots and a line, driven by the existing update_active_step API.
- Live research progress: today every progress event rewrites the whole assistant message with markdown. Replace it with a ResearchProgressCard widget in the chat: stepper, current action line, elapsed time and ETA, and a collapsible activity log (a capped list of 200 rows, newest at the bottom, icons chosen with the text_utils category guess). Update only the changed widgets. When research completes, replace the card with the summary and the "Open report" button.
- Report viewer: keep behaviour and security; restyle toolbar with icons; non-blocking toast on copy and export.
STOP and report.

PHASE 9: SETTINGS, SETUP, DIALOGS, TOASTS

- Settings dialog: a left list of sections (General, Performance, Agents and testing, Privacy and internet, Storage) with a stacked page each. Under each control add one plain sentence explaining what it does in everyday words. Validate the keep-alive field with parse_keep_alive_seconds and show an inline error. Save applies changes live (keep-alive countdown, low-power depth lock, internet badge, model lists).
- Setup wizard: when setup_done is true and checks pass, show a minimal splash (logo and "Starting Ollambo") instead of the full wizard flashing, then continue automatically (keep the logic and signals). When something fails, show a centered card with a three-step list (Ollama installed, Server running, Model available) using spinner, check or x icons, the manual-steps box, the terminal widget and the model picker, all themed. Keep every existing signal and the hardware recommendation logic.
- Toasts: ui/widgets/toast.py, a non-blocking message at the bottom center that fades after 2 seconds (no animation in low-power mode). Use it instead of QMessageBox.information for copy, save and export success. Keep QMessageBox only for questions and errors, styled by the theme.
STOP and report.

PHASE 10: POLISH AND HARDENING

- Motion: only the sidebar collapse, toast fade and spinner. Nothing animates while text streams. Disable all animation when low_power_mode is on.
- Focus order and focus rings on every control; tooltips on every icon-only button.
- Confirm HiDPI crispness of icons at 100%, 150%, 200% scaling.
- Error and offline states: Ollama not reachable, no models installed, internet disabled in Research mode, each with a clear message and a button to fix it.
- Update README.md (screenshots section placeholder, shortcuts table) and PROJECT_OVERVIEW.md (new UI module layout). Update ollambo.spec hiddenimports for every new module (ui.theme, ui.icons, ui.navigation, ui.text_utils, ui.chat_view, ui.session_controller, ui.widgets.*, core.chat_session) and "PySide6.QtSvg".
- Remove dead code left behind by earlier phases (grep proof).

PERFORMANCE BUDGET
- No widget creation per streamed token. Only the last open block re-renders, on the existing 100ms flush.
- At most 6 live ChatViews. Bulk-load messages with setUpdatesEnabled(False) then True.
- Research activity log capped at 200 rows.
- No QGraphicsDropShadowEffect, no blur, no opacity effects on large widgets.
- Icons cached; syntax highlighting off for big files; QtWebEngine stays lazy and is destroyed on close.
- Startup shows the window quickly; Ollama checks stay on the background thread.

DEFINITION OF DONE
1. All Phase 1 acceptance checks pass and tests/test_chat_session.py exists.
2. "python -m unittest discover tests" passes (same or fewer failures than the Phase 0 baseline, and I know about any difference).
3. No emojis in UI chrome. No hex colors outside ui/theme/tokens.py. No inline color setStyleSheet left.
4. python install.py still builds and the packaged app launches (icons visible, themed, no missing-module errors).
5. A short CHANGELOG entry listing every phase.

OUTPUT FORMAT AFTER EACH PHASE
Files created, files changed, files deleted; what you verified and how; what is still risky; the next phase's first three steps. Then wait for me.

Now begin with Phase 0 only.