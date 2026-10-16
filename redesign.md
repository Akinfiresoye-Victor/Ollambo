OLLAMBO UI FIX PASS: BUGS, LAYOUT, AND POLISH

ROLE AND RULES

You are a senior PySide6 / Qt6 desktop engineer (Python 3.9 to 3.14). The redesign phases in phases.md are "done", but I tested the app and found many bugs. This is a fix pass. Same working rules as phases.md still apply:

1. Do not break behaviour. core/ and workers/ keep their public signatures and Signal names. Allowed: new files, new optional parameters with defaults, additive keys.
2. Work in the three phases below, in order. After each phase run "python -m unittest discover tests", launch with "python main.py" (from SOURCE, not an installed exe), run that phase's checks, then STOP and report: files changed, what you verified, what you are unsure about. Do not start the next phase until I reply "continue".
3. No new dependencies. No gradients, glow, shadows or emojis in UI chrome. Hex colors only in ui/theme/tokens.py.
4. No big-bang rewrites. Small edits, and explain every non-obvious decision in one or two plain sentences. I am a Python developer who wants to understand each change.
5. Keep "from **future** import annotations" and nothing newer than Python 3.9.
6. Tests that assert protected behaviour (tests/test_chat_sidebar.py, test_icons.py, test_folder_and_keepalive.py, test_theme.py, test_markdown_formatter.py, test_navigation.py) must keep passing. If a test must change because the design intentionally changed, update it in the same commit and tell me exactly why.
7. If something is ambiguous, ask me. Do not guess.

STEP ZERO (before any edit)
Open phases.md and compare it with the code. List every item from Phases 5 to 9 that is NOT actually implemented (examples I could not see in the app: centered 760px column, hero empty state with suggestion chips, composer card with bottom button row, BuildProgressCard, ResearchProgressCard, stage tracker stepper, settings sections, splash screen). Send me that list at the end of Phase A's report. Do not build those items until Phase C.

========================================
PHASE A: FUNCTIONAL BUGS
========================================

A1. Spinners never spin.
Root causes:

- ui/setup_wizard.py \_set_check_status, ui/chat_top_bar.py (\_set_connection_icon("loader", ...)), ui/research_panel.py (stage_status_icon) and ui/chat_page.py use icon("loader") as a static pixmap. A static pixmap cannot rotate.
- ui/icons.py Spinner stops its timer completely when low_power_mode is True. The setup wizard turns low-power mode ON automatically for machines with 8 GB RAM or less, so every spinner on my machine is frozen by design, and a frozen spinner looks like a hang.
  Fix:
- Create a small reusable widget (in ui/icons.py or ui/widgets/status_icon.py) called StatusIcon: a QLabel-sized widget that shows either a static icon (check, x, alert-triangle, message, etc.) or an animated Spinner, with set_state("loading" | "ok" | "failed" | "idle"). Use it in the setup wizard check rows, the top bar connection badge, and the research panel stage line. Remove every place that passes "loader" to icon() as a stand-in for a spinner.
- Change Spinner low-power behaviour: do NOT stop it. In low-power mode rotate in 90 degree steps every 400 ms (about 2.5 steps per second, almost zero CPU). Normal mode stays smooth (about 16 fps). It must still pause when hidden. Update tests/test_icons.py to assert "timer is active but interval >= 400 in low-power mode, stopped when hidden", and tell me you changed it.
- Make sure every Spinner is actually parented, shown, and has a visible size where it is used.
  Check: setup wizard rows spin while checking; top bar shows a spinner while working; sidebar row spins during a background reply; all of this with low-power mode ON and OFF.

A2. Sidebar cannot be reopened.
Root cause: the only collapse button is inside the sidebar, so when the sidebar is 0px wide the button is gone. The collapsed state is also saved in QSettings, so it can start collapsed with no way out except Ctrl+B.
Fix:

- Add an always-visible sidebar toggle icon button at the far left of ChatTopBar (icon "sidebar-left", tooltip "Toggle sidebar (Ctrl+B)") and an inspector toggle icon button at the far right (icon "panel-right", tooltip "Toggle inspector (Ctrl+Shift+B)"). Add NEW signals sidebar_toggle_requested and inspector_toggle_requested to ChatTopBar (additive). Connect them in ChatPage to \_toggle_sidebar and \_toggle_inspector. Keep the existing button inside the sidebar too.
- Make the Ctrl+B / Ctrl+Shift+B / Ctrl+K / Ctrl+N / Ctrl+, shortcuts use Qt.ShortcutContext.WindowShortcut so they work no matter which widget has focus.
- Set shell_splitter.setHandleWidth(1). The current splitter handle is a thick grey bar between the sidebar and the chat.
- On startup, if the sidebar was saved as collapsed, still show the top bar toggle (it now always exists).
  Check: collapse the sidebar with the button, with Ctrl+B, restart the app, then reopen it with the top bar button. Same for the inspector.

A3. Sidebar chat titles are drawn twice (overlapping ghost text).
Root cause: ui/chat_sidebar.py creates QListWidgetItem(display_title) and ALSO calls setItemWidget with a \_ChatRow. Qt draws the item's own text underneath the row widget.
Fix: create items with EMPTY text and keep the title only in item.data(Qt.UserRole + 3) (the tests already read that role) and in the row's title_label. Remove every item.setText(...) call (refresh_list, upsert_chat, set_chat_state, \_commit_rename). Update \_apply_filter to rely on the data role only. Also give list rows a minimum height of 36px (item.setSizeHint with max(row.sizeHint().height(), 36)).

A4. Send button is nearly invisible.
Root cause: ui/theme/qss.py only styles QPushButton[variant="primary"]. The composer send button is a QToolButton, so it never gets the accent style.
Fix: add QToolButton[variant="primary"] (+ :hover, :disabled) rules matching the QPushButton ones, and QToolButton[variant="ghost"] already exists so keep it. Add "QToolButton::menu-indicator { image: none; width: 0; }" so the attach (+) button does not show a stray little dropdown arrow. For every primary-variant button, tint its icon with DARK.colors.text_on_accent (the "New chat" plus icon is currently grey on orange, which is why it looks faint).

A5. Custom widgets ignore stylesheet backgrounds and borders.
Root cause: in Qt, a plain QWidget subclass does not paint a QSS background/border unless Qt.WidgetAttribute.WA_StyledBackground is set. That is why the Composer has no card look.
Fix: set WA_StyledBackground on Composer, ChatSidebar (#Sidebar), the attachment chips, the inspector container, and any other custom QWidget that relies on a QSS card/background rule. Grep for objectName / role="card" usage on plain QWidgets and cover them all.

A6. Setup wizard seems stuck on "Ollama server: waiting".
Screenshots show "Installed: Found at ..." then "Ollama server: waiting" and "Models available: waiting" with Continue disabled. Investigate whether this is a real hang or just a slow check. Look at workers/setup_worker.py, core/setup_checker.is_server_running and OllamaClient.is_server_running (5 s timeout) and the 10 s wait loop after start_ollama_server. Add temporary logging, reproduce with Ollama stopped and with Ollama running, and tell me what you find. Make sure the UI always reaches a final state (ok or failed with a clear message and a Re-check button) within about 20 seconds. Also implement the splash from Phase 9 when setup_done is true: show only the logo and "Starting Ollambo" with a spinner, and switch to the chat automatically if checks pass; show the full wizard only if something fails.

PHASE A CHECKS: spinners, sidebar reopen (also after restart), no ghost text in sidebar rows, send button clearly visible in accent color, composer shows as a rounded bordered card, wizard reaches a final state. STOP and report.

========================================
PHASE B: LAYOUT AND SPACING (nothing may look squeezed)
========================================

B1. Chat text sits inside boxes and is cramped.
Root cause: the global QSS rule "QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox" gives every QTextBrowser (including AutoTextView) a border, surface background and padding.
Fix: add a more specific rule: QTextBrowser[role="body"] { background: transparent; border: none; padding: 0; } and make sure AutoTextView sets that role. Assistant text must have no frame at all.
Typography: body chat text 15px, paragraph line-height about 1.55, paragraph spacing 10px. In core/markdown_formatter.py reduce the empty-line spacer (<div style='height: 6px'>) so blank lines do not double the gap, change paragraph font-size from 13px to 15px, and keep all substrings the tests assert. Headings: h1 20px, h2 17px, h3 15px bold, in primary text color (no colorful headings, one accent only for links).
Layout: put each ChatView's content in a centered column with max width 760 (a wrapper widget with a horizontal layout: stretch, content with setMaximumWidth(760), stretch). The user message bubble is right-aligned inside that column, max 80% width. The assistant avatar (18px logo) must be aligned to the TOP of the message (Qt.AlignTop), not vertically centered next to the text.

B2. Composer.
Rebuild layout inside the same Composer class and keep its public API (text, clear, set_text, set_generating, set_send_enabled, send_requested, stop_requested, btn_attach, input_edit): a vertical layout inside the card. Top: auto-growing input (1 to 8 lines, scroll after that). Bottom row: attach (+) button on the left, spacer, circular 36px send/stop button on the right (accent color, arrow-up icon, becomes square icon while generating). Card margins 16, spacing 8. Composer width follows the same 760px centered column. Placeholder: "Message Ollambo, or describe what you want to build".

B3. Sidebar layout.

- The chat list must fill all remaining height: add it with stretch factor 1 and delete the layout.addStretch() after it. Make QListWidget in the sidebar borderless with transparent background.
- Search field: the leading icon overlaps the placeholder. Give that QLineEdit padding-left of 32px (or use setTextMargins) so text starts after the icon.
- Buttons with icons ("Library", "Settings", "New chat") need visible space between icon and text. Qt has no icon-gap property in QSS, so create a small helper that left-aligns, sets padding-left 12px and iconSize 16, and add two leading spaces to the label if needed. Apply to every button that has both icon and text across the app.
- Group headers (Today, Yesterday...) use the caption style, tertiary color, 12px, with 12px top spacing, sentence case.

B4. Inspector and Files panel (currently badly clipped: "Artifacts (9", "Copy C", "Save I", "e All tc", file names shown as "c...", "g...").

- Put the inspector inside a QSplitter with the chat so I can drag its width. Default 520px, minimum 400px, saved in QSettings with the other splitter state.
- Files panel: rebuild the header as TWO clean rows: row 1 = project name elided with "..." (full name in tooltip) + icon-only buttons with tooltips (Open folder, Export zip, Save all). Row 2 = filter field. Remove the file combo box and the duplicate "Artifacts (N files)" title; show the file count as a small caption.
- Use a VERTICAL splitter inside the panel (file tree on top about 35%, code viewer below) so both get the full panel width. The tree must show full file names with horizontal scrolling instead of eliding to "c...".
- Code viewer: setLineWrapMode(NoWrap), monospace from tokens, 13px, with a one-line header above it showing the file path and icon-only Copy and Save buttons.
- Plan panel gets the same treatment: step rows must wrap text instead of clipping, file path in a small monospace chip.
  Keep ArtifactsPanel protected API (add_file, \_files, \_tree_items) and PlanPanel signals and methods.

B5. Top bar.

- Replace the text "Refresh" button with an icon-only refresh button (tooltip "Refresh models").
- Shorten the connection badge to a dot plus short text ("Ready 02:41", "Working", "Standby"), keeping the protected words and "03:00" after set_idle with a 180 second window, and keep the long explanation in the tooltip.
- Model and mode dropdowns become quiet chips (transparent background, no border until hover). Give Auto detect, Code mode, Chat only and Research four different icons.
- Fixed 44px height, 12px side margins, 8px spacing, nothing may wrap or shift when status text appears.

B6. Verify at the minimum window size (1040x680) and at 125% and 150% Windows display scaling. Nothing may be clipped, overlapped or truncated without an ellipsis plus tooltip. Send me screenshots of: empty chat, chat with a long reply, chat with plan + inspector open on Files, settings dialog, setup screen.

PHASE B CHECKS: all of the above, plus resize the window and drag the inspector; text reflows, nothing jumps. STOP and report.

========================================
PHASE C: MAKE IT LOOK DESIGNED, NOT GENERIC
========================================

The current screens feel plain and unfinished. Goal: calm and professional like Claude's desktop app, with a bit of personality. Still ONE accent color, no gradients, no emojis.

C1. New-chat empty state: centered logo mark (48px), heading "What are we building?" (hero size), one quiet subtitle line, the composer centered in the middle of the screen, and four suggestion chips under it (Plan a Python project, Explain a concept, Research a topic, Fix a traceback) each with a line icon; clicking a chip fills the composer. After the first message the composer sits at the bottom.
C2. Setup wizard: a centered card (max width 520) with the logo, the three checks as rows using StatusIcon (spinner, check, x), a one-line human explanation under each failed check, and a single primary action button per failure state. No big empty dark page.
C3. Build progress and research progress: if BuildProgressCard and ResearchProgressCard from phases.md Phases 7 and 8 are missing, build them now as described there (file list with status icons; stepper with Planning, Searching, Reading, Checking facts, Writing; elapsed time; collapsible activity log capped at 200 rows). Update only changed widgets, never rebuild the whole message per event.
C4. Icons and visuals: review every icon in ui/icons.py; use different icons for different meanings (no reusing "message" for three things). Add a small set of simple line illustrations built from SVG paths, in the same icon style, for empty states (empty library, no files yet, no plan yet, Ollama not reachable). Keep them 64 to 96px, single stroke color (text_tertiary) with one accent detail.
C5. Research report diagrams: they are always generic left-to-right flowcharts. In core/prompts.py and the section-5 instructions in core/research_writer.py, tell the model to choose the diagram type that fits the content (flowchart for processes, sequenceDiagram for interactions, timeline for history, mindmap for concepts, pie for proportions), keep labels short, and avoid HTML. In ui/report_viewer.py initialize Mermaid with theme "base" and themeVariables built from the DARK tokens (background, primaryColor = surface, primaryBorderColor = border, lineColor = text_tertiary, fontFamily = ui font, accent only for the key node) so diagrams match the app. Keep validate_mermaid_code, the repair flow and the security behaviour unchanged and keep tests/test_deep_research.py passing.
C6. Final pass: consistent spacing (4, 8, 12, 16, 24, 32), every icon-only button has a tooltip and a visible focus ring, no text smaller than 12px, contrast at least 4.5:1, and every screen has designed empty, loading and error states.

PHASE C CHECKS: screenshots of every screen again, tests green, python install.py builds, and the packaged app launches with all icons visible. STOP and report.

Now begin with STEP ZERO and Phase A only.
