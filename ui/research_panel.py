"""
ui/research_panel.py - Research Library tab for Ollambo.

Features:
- Searchable project list with metadata: Title, Date, Status, Sources, Depth
- Project actions: Open Report, Resume, Rename, Delete, Export .md, Open Folder
- Step progress tracker widget (Planning, Searching, Reading, Checking facts, Writing)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.research_store import (
    delete_project,
    export_report,
    list_projects,
    load_report,
    open_project_folder,
    rename_project,
)
from ui.icons import icon
from ui.theme import set_property, set_variant
from ui.text_utils import strip_emoji_prefix
from ui.report_viewer import ReportViewerDialog
from ui.widgets.toast import show_toast


class ResearchPanel(QWidget):
    """Research Library tab widget."""

    resume_requested = Signal(str)  # project_id
    open_report_requested = Signal(str)  # project_id

    _STEPS = ["Planning", "Searching", "Reading", "Checking facts", "Writing"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._projects: list[dict] = []
        self._active_step: str = ""
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Top row: title, search bar, refresh button
        top_row = QHBoxLayout()
        title = QLabel("Research library")
        title.setProperty("role", "title")
        title_icon = QLabel()
        title_icon.setPixmap(icon("flask", size=18).pixmap(18, 18))
        top_row.addWidget(title_icon)
        top_row.addWidget(title)

        top_row.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter research projects…")
        self.search_input.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_input.setFixedWidth(240)
        self.search_input.textChanged.connect(self._apply_filter)
        top_row.addWidget(self.search_input)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setIcon(icon("refresh"))
        btn_refresh.setProperty("variant", "ghost")
        btn_refresh.clicked.connect(self.refresh)
        top_row.addWidget(btn_refresh)

        layout.addLayout(top_row)

        # Projects Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Title", "Date", "Status", "Sources", "Depth"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table)

        # Action Buttons Row
        actions_row = QHBoxLayout()
        self.btn_open = QPushButton("Open report")
        self.btn_open.setIcon(icon("file"))
        set_variant(self.btn_open, "primary")
        self.btn_open.clicked.connect(self._on_open_selected)
        actions_row.addWidget(self.btn_open)

        self.btn_resume = QPushButton("Resume")
        self.btn_resume.setIcon(icon("arrow-up"))
        set_variant(self.btn_resume, "primary")
        self.btn_resume.clicked.connect(self._on_resume_selected)
        actions_row.addWidget(self.btn_resume)

        self.btn_rename = QPushButton("Rename")
        self.btn_rename.setIcon(icon("pencil"))
        self.btn_rename.setProperty("variant", "ghost")
        self.btn_rename.clicked.connect(self._on_rename_selected)
        actions_row.addWidget(self.btn_rename)

        self.btn_export = QPushButton("Export .md")
        self.btn_export.setIcon(icon("download"))
        self.btn_export.setProperty("variant", "ghost")
        self.btn_export.clicked.connect(self._on_export_selected)
        actions_row.addWidget(self.btn_export)

        self.btn_folder = QPushButton("Open folder")
        self.btn_folder.setIcon(icon("folder"))
        self.btn_folder.setProperty("variant", "ghost")
        self.btn_folder.clicked.connect(self._on_folder_selected)
        actions_row.addWidget(self.btn_folder)

        actions_row.addStretch()

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setIcon(icon("trash"))
        set_variant(self.btn_delete, "danger")
        self.btn_delete.clicked.connect(self._on_delete_selected)
        actions_row.addWidget(self.btn_delete)

        layout.addLayout(actions_row)

        # Progress Steps Box
        self.progress_frame = QFrame()
        self.progress_frame.setProperty("role", "card")
        step_layout = QHBoxLayout(self.progress_frame)
        step_layout.setContentsMargins(10, 6, 10, 6)
        step_layout.setSpacing(8)

        step_lbl = QLabel("Research Stage:")
        step_lbl.setProperty("role", "section")
        step_layout.addWidget(step_lbl)

        self.step_labels: dict[str, QLabel] = {}
        for s in self._STEPS:
            lbl = QLabel(s)
            lbl.setProperty("role", "tertiary")
            step_layout.addWidget(lbl)
            self.step_labels[s.lower()] = lbl

        step_layout.addSpacing(8)

        self.stage_status_icon = QLabel()
        step_layout.addWidget(self.stage_status_icon)
        self.stage_status_lbl = QLabel("Ready")
        self.stage_status_lbl.setProperty("role", "accent")
        step_layout.addWidget(self.stage_status_lbl)

        self.stage_progress_bar = QProgressBar()
        self.stage_progress_bar.setFixedHeight(4)
        self.stage_progress_bar.setFixedWidth(100)
        self.stage_progress_bar.setTextVisible(False)
        self.stage_progress_bar.setVisible(False)
        step_layout.addWidget(self.stage_progress_bar)

        step_layout.addStretch()
        layout.addWidget(self.progress_frame)

    def refresh(self) -> None:
        """Reload project list from research store."""
        self._projects = list_projects()
        self._apply_filter(self.search_input.text())

    def _apply_filter(self, query: str = "") -> None:
        q = query.lower().strip()
        filtered = [
            p for p in self._projects
            if not q or q in p.get("title", "").lower() or q in p.get("topic", "").lower()
        ]
        self.table.setRowCount(len(filtered))

        for row, p in enumerate(filtered):
            # Title
            item_title = QTableWidgetItem(p.get("title", "Untitled"))
            item_title.setData(Qt.ItemDataRole.UserRole, p.get("id"))
            self.table.setItem(row, 0, item_title)

            # Date
            dt_raw = p.get("date", "")
            try:
                dt_fmt = datetime.fromisoformat(dt_raw).strftime("%Y-%m-%d %H:%M")
            except Exception:
                dt_fmt = dt_raw[:16]
            item_date = QTableWidgetItem(dt_fmt)
            self.table.setItem(row, 1, item_date)

            # Status
            status = p.get("status", "planning")
            item_status = QTableWidgetItem(status.capitalize())
            if status == "completed":
                item_status.setForeground(Qt.GlobalColor.green)
            elif status == "researching":
                item_status.setForeground(Qt.GlobalColor.cyan)
            self.table.setItem(row, 2, item_status)

            # Sources
            sc = str(p.get("source_count", 0))
            self.table.setItem(row, 3, QTableWidgetItem(sc))

            # Depth
            depth = p.get("depth", "standard")
            self.table.setItem(row, 4, QTableWidgetItem(depth.capitalize()))

    def _selected_project_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        pid = self._selected_project_id()
        if pid:
            self.open_report(pid)

    def _on_open_selected(self) -> None:
        pid = self._selected_project_id()
        if pid:
            self.open_report(pid)

    def open_report(self, project_id: str) -> None:
        """Open ReportViewerDialog for the selected project."""
        md = load_report(project_id)
        if not md:
            show_toast(self, "Report not ready yet", duration=2000)
            return

        # Find project title
        title = "Research Report"
        for p in self._projects:
            if p.get("id") == project_id:
                title = p.get("title", title)
                break

        dlg = ReportViewerDialog(title, md, parent=self)
        dlg.exec()

    def _on_resume_selected(self) -> None:
        pid = self._selected_project_id()
        if pid:
            self.resume_requested.emit(pid)

    def _on_rename_selected(self) -> None:
        pid = self._selected_project_id()
        if not pid:
            return
        curr_title = ""
        for p in self._projects:
            if p.get("id") == pid:
                curr_title = p.get("title", "")
                break
        new_title, ok = QInputDialog.getText(self, "Rename Project", "New title:", text=curr_title)
        if ok and new_title.strip():
            rename_project(pid, new_title.strip())
            self.refresh()

    def _on_export_selected(self) -> None:
        pid = self._selected_project_id()
        if not pid:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export Report", "report.md", "Markdown (*.md)")
        if dest:
            if export_report(pid, dest):
                show_toast(self, "Report exported")
            else:
                QMessageBox.warning(self, "Export Failed", "Could not export report.")

    def _on_folder_selected(self) -> None:
        pid = self._selected_project_id()
        if pid:
            open_project_folder(pid)

    def _on_delete_selected(self) -> None:
        pid = self._selected_project_id()
        if not pid:
            return
        ans = QMessageBox.question(
            self,
            "Delete Project",
            "Are you sure you want to delete this research project and all its files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            delete_project(pid)
            self.refresh()

    def _on_selection_changed(self) -> None:
        pid = self._selected_project_id()
        if not pid:
            return
        proj = next((p for p in self._projects if p.get("id") == pid), {})
        status = proj.get("status", "")
        if status == "completed":
            self.btn_resume.setEnabled(False)
            self.btn_resume.setText("Done")
            self.btn_resume.setIcon(icon("check"))
            self.btn_open.setEnabled(True)
        else:
            self.btn_resume.setEnabled(True)
            self.btn_resume.setText("Resume")
            self.btn_resume.setIcon(icon("arrow-up"))

    def _on_table_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        pid = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not pid:
            return

        proj = next((p for p in self._projects if p.get("id") == pid), {})
        status = proj.get("status", "")

        menu = QMenu(self)

        act_open = menu.addAction(icon("file"), "Open report")
        act_open.triggered.connect(lambda: self.open_report(pid))

        if status != "completed":
            act_resume = menu.addAction(icon("arrow-up"), "Resume research")
            act_resume.triggered.connect(lambda: self.resume_requested.emit(pid))

        menu.addSeparator()

        act_rename = menu.addAction(icon("pencil"), "Rename…")
        act_rename.triggered.connect(self._on_rename_selected)

        act_export = menu.addAction(icon("download"), "Export Markdown…")
        act_export.triggered.connect(self._on_export_selected)

        act_folder = menu.addAction(icon("folder"), "Open folder in Explorer")
        act_folder.triggered.connect(lambda: open_project_folder(pid))

        menu.addSeparator()

        act_del = menu.addAction(icon("trash"), "Delete project")
        act_del.triggered.connect(self._on_delete_selected)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def update_active_step(self, step_name: str, step: int = 0, total: int = 0, message: str = "") -> None:
        """Highlight active step in progress frame and update progress bar."""
        s_low = step_name.lower()
        for name, lbl in self.step_labels.items():
            set_property(lbl, "active", name in s_low or s_low in name)

        clean_message, category = strip_emoji_prefix(message)
        category_icons = {
            "search": "search",
            "fetch": "external-link",
            "read": "file",
            "check": "check",
            "warn": "alert-triangle",
        }
        if category:
            self.stage_status_icon.setPixmap(
                icon(category_icons[category], size=16).pixmap(16, 16)
            )
        elif step_name.lower() in ("idle", "ready", "completed"):
            self.stage_status_icon.clear()
        else:
            self.stage_status_icon.setPixmap(icon("loader", size=16).pixmap(16, 16))

        if clean_message:
            self.stage_status_lbl.setText(f"{step_name}: {clean_message[:40]}")
        else:
            self.stage_status_lbl.setText(step_name)

        if total > 0:
            self.stage_progress_bar.setVisible(True)
            self.stage_progress_bar.setRange(0, total)
            self.stage_progress_bar.setValue(step)
        elif step_name.lower() in ("idle", "ready", "completed"):
            self.stage_progress_bar.setVisible(False)
            self.stage_status_lbl.setText("Ready")
