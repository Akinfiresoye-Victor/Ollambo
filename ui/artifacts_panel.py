"""
Artifacts Panel for viewing, navigating folder structures, copying, and saving generated code.

Features:
  - Hierarchical folder-tree viewer (`QTreeWidget`) showing multi-level folders & files
  - Dropdown file selector synchronized with tree
  - Monospace code viewer with syntax styling
  - Copy button with temporary feedback
  - Save to Disk button for individual files
  - Save All to Folder button preserving full directory structures
"""

from __future__ import annotations

from pathlib import Path
from core.pathsafe import safe_relpath
from core import workspace
from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from ui.icons import icon
from ui.theme import set_variant
from ui.theme.tokens import DARK
from ui.text_utils import strip_emoji_prefix
from ui.widgets.toast import show_toast


class ArtifactsPanel(QFrame):
    """Viewer for generated code files with tree structure, export, and clipboard options."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._files: dict[str, str] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._current_workspace_id: str | None = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("Card")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Project Info Bar (Workspace title and folder path)
        proj_bar = QHBoxLayout()
        self.lbl_project_info = QLabel("")
        self.lbl_project_info.setProperty("role", "muted")
        self.lbl_project_info.setVisible(False)
        proj_bar.addWidget(self.lbl_project_info, 1)

        self.btn_open_folder = QToolButton()
        self.btn_open_folder.setIcon(icon("folder"))
        self.btn_open_folder.setToolTip("Open folder")
        self.btn_open_folder.setProperty("variant", "ghost")
        self.btn_open_folder.setProperty("iconOnly", "true")
        self.btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_folder.clicked.connect(self._open_project_folder)
        self.btn_open_folder.setVisible(False)
        proj_bar.addWidget(self.btn_open_folder)

        self.btn_export_zip = QToolButton()
        self.btn_export_zip.setIcon(icon("download"))
        self.btn_export_zip.setToolTip("Export zip")
        self.btn_export_zip.setProperty("variant", "ghost")
        self.btn_export_zip.setProperty("iconOnly", "true")
        self.btn_export_zip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export_zip.clicked.connect(self._export_project_zip)
        self.btn_export_zip.setVisible(False)
        proj_bar.addWidget(self.btn_export_zip)

        layout.addLayout(proj_bar)

        # Header: File selector, title & actions
        top_bar = QHBoxLayout()
        self.lbl_title = QLabel("Artifacts (0 files)")
        self.lbl_title.setProperty("role", "title")
        top_bar.addWidget(self.lbl_title)

        self.lbl_count = QLabel("0 files")
        self.lbl_count.setProperty("role", "caption")
        self.lbl_count.setVisible(False)
        top_bar.addWidget(self.lbl_count)
        top_bar.addSpacing(10)
        self.file_combo = QComboBox()
        self.file_combo.setMinimumWidth(180)
        self.file_combo.currentIndexChanged.connect(self._on_combo_selected)
        self.file_combo.hide()
        top_bar.addWidget(self.file_combo)
        top_bar.addStretch()

        self.btn_copy = QToolButton()
        self.btn_copy.setIcon(icon("copy"))
        self.btn_copy.setToolTip("Copy code")
        self.btn_copy.setProperty("variant", "ghost")
        self.btn_copy.setProperty("iconOnly", "true")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_current_code)
        top_bar.addWidget(self.btn_copy)

        self.btn_save = QToolButton()
        self.btn_save.setIcon(icon("download"))
        self.btn_save.setToolTip("Save file")
        self.btn_save.setProperty("variant", "ghost")
        self.btn_save.setProperty("iconOnly", "true")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_single_file)
        top_bar.addWidget(self.btn_save)

        self.btn_save_all = QPushButton("Save All to Folder")
        self.btn_save_all.setIcon(icon("download"))
        set_variant(self.btn_save_all, "primary")
        self.btn_save_all.clicked.connect(self._save_all_files)
        top_bar.addWidget(self.btn_save_all)

        layout.addLayout(top_bar)

        # Continuation / Testing Progress banner
        self.lbl_progress = QLabel("")
        self.lbl_progress.setProperty("role", "warning")
        self.lbl_progress.setVisible(False)
        layout.addWidget(self.lbl_progress)

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(0, 12, 0, 0)
        self.empty_label = QLabel("No files yet. Generate or add a project to see artifacts here.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setProperty("role", "muted")
        empty_layout.addWidget(self.empty_label)
        layout.addWidget(self.empty_state)

        # Splitter: Tree navigation on left, Code viewer on right
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(4)

        tree_title = QLabel("Project Structure")
        tree_title.setProperty("role", "section")
        tree_layout.addWidget(tree_title)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setAlternatingRowColors(False)
        self.tree.setHorizontalScrollMode(self.tree.ScrollMode.ScrollPerPixel)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        tree_layout.addWidget(self.tree)
        self.splitter.addWidget(tree_container)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        code_header = QHBoxLayout()
        code_header.addStretch()
        code_header.addWidget(self.btn_copy)
        code_header.addWidget(self.btn_save)
        right_layout.addLayout(code_header)

        self.code_viewer = QPlainTextEdit()
        self.code_viewer.setReadOnly(True)
        self.code_viewer.setProperty("role", "code")
        self.code_viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        right_layout.addWidget(self.code_viewer)
        self.splitter.addWidget(right_panel)

        self.splitter.setSizes([200, 600])
        self.splitter.setVisible(False)
        layout.addWidget(self.splitter)
        self.project_info_layout = proj_bar
        self.title_actions_layout = top_bar
        self.code_actions_layout = code_header

        self.summary_widget = QWidget()
        summary_layout = QVBoxLayout(self.summary_widget)
        summary_layout.setContentsMargins(0, 12, 0, 0)
        summary_layout.setSpacing(8)
        self.summary_title = QLabel("Artifacts associated with this chat")
        self.summary_title.setProperty("role", "section")
        summary_layout.addWidget(self.summary_title)
        self.summary_files = QLabel("No artifacts created in this chat yet.")
        self.summary_files.setWordWrap(True)
        self.summary_files.setProperty("role", "muted")
        summary_layout.addWidget(self.summary_files)
        self.btn_reopen_artifacts = QPushButton("Open artifacts")
        self.btn_reopen_artifacts.setProperty("variant", "ghost")
        self.btn_reopen_artifacts.clicked.connect(self._reopen_artifacts)
        summary_layout.addWidget(self.btn_reopen_artifacts)
        self.summary_widget.setVisible(False)
        layout.addWidget(self.summary_widget)

    def _reopen_artifacts(self) -> None:
        if self._files:
            self.summary_widget.setVisible(False)
            self.splitter.setVisible(True)
            self.empty_state.setVisible(False)
        else:
            self.empty_state.setVisible(True)
            self.summary_widget.setVisible(False)
            self.splitter.setVisible(False)

    def show_chat_summary(self, files: dict[str, str] | list[str] | None = None) -> None:
        names = []
        if isinstance(files, dict):
            names = list(files.keys())
        elif files:
            names = [str(item) for item in files]

        if names:
            preview = "<br>".join(f"• {name}" for name in names[:8])
            if len(names) > 8:
                preview += f"<br>• +{len(names) - 8} more"
            self.summary_files.setText(preview)
        else:
            self.summary_files.setText("No artifacts created in this chat yet.")

        self.splitter.setVisible(False)
        self.empty_state.setVisible(False)
        self.summary_widget.setVisible(True)

    def show_continuation_progress(self, filename: str, part: int, max_parts: int) -> None:
        """Show progress indicator when generating multi-part continuations."""
        self.lbl_progress.setText(f"Generating part {part} of `{filename}` (limit {max_parts})…")
        self.lbl_progress.setVisible(True)

    def show_test_status(self, filename: str, status_msg: str) -> None:
        """Show progress indicator when running automated testing or code repairs."""
        clean_status, _ = strip_emoji_prefix(status_msg)
        self.lbl_progress.setText(clean_status)
        self.lbl_progress.setVisible(True)

    def add_file(self, filename: str, content: str) -> None:
        """Add or update an artifact file in the panel and tree view."""
        self.lbl_progress.setVisible(False)
        self.empty_state.setVisible(False)
        self.splitter.setVisible(True)
        self._files[filename] = content

        # Update Tree
        self._add_or_update_tree(filename)

        # Update combo
        idx = self.file_combo.findText(filename)
        if idx == -1:
            self.file_combo.addItem(filename)
            self.file_combo.setCurrentIndex(self.file_combo.count() - 1)
        else:
            self.file_combo.setCurrentIndex(idx)
            self.code_viewer.setPlainText(content)

        count = len(self._files)
        self.lbl_title.setText(f"Artifacts ({count} {'file' if count == 1 else 'files'})")

    def _add_or_update_tree(self, filename: str) -> None:
        """Parse relative path and build/update tree nodes."""
        clean = filename.replace("\\", "/").strip("/")
        parts = clean.split("/")

        parent_item: QTreeWidgetItem | None = None
        # Walk directories
        for i in range(len(parts) - 1):
            dir_path = "/".join(parts[: i + 1])
            dir_name = parts[i]
            if dir_path in self._tree_items:
                parent_item = self._tree_items[dir_path]
            else:
                new_item = QTreeWidgetItem([f"{dir_name}/"])
                new_item.setIcon(0, icon("folder", DARK.colors.text_secondary, 16))
                new_item.setData(0, Qt.ItemDataRole.UserRole, "")  # Empty file data
                if parent_item is None:
                    self.tree.addTopLevelItem(new_item)
                else:
                    parent_item.addChild(new_item)
                new_item.setExpanded(True)
                self._tree_items[dir_path] = new_item
                parent_item = new_item

        # File leaf item
        file_name = parts[-1]
        if filename in self._tree_items:
            leaf_item = self._tree_items[filename]
            leaf_item.setText(0, file_name)
            leaf_item.setIcon(
                0,
                icon(
                    "file-code" if Path(file_name).suffix.lower() in (".py", ".js", ".ts", ".css", ".html", ".json") else "file",
                    DARK.colors.text_secondary,
                    16,
                ),
            )
        else:
            leaf_item = QTreeWidgetItem([file_name])
            leaf_item.setIcon(
                0,
                icon(
                    "file-code" if Path(file_name).suffix.lower() in (".py", ".js", ".ts", ".css", ".html", ".json") else "file",
                    DARK.colors.text_secondary,
                    16,
                ),
            )
            leaf_item.setData(0, Qt.ItemDataRole.UserRole, filename)
            if parent_item is None:
                self.tree.addTopLevelItem(leaf_item)
            else:
                parent_item.addChild(leaf_item)
            self._tree_items[filename] = leaf_item

        self.tree.setCurrentItem(leaf_item)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        rel_path = item.data(0, Qt.ItemDataRole.UserRole)
        if rel_path and rel_path in self._files:
            idx = self.file_combo.findText(rel_path)
            if idx >= 0:
                self.file_combo.setCurrentIndex(idx)
            self.code_viewer.setPlainText(self._files[rel_path])

    def _on_combo_selected(self, index: int) -> None:
        filename = self.file_combo.currentText()
        if filename in self._files:
            self.code_viewer.setPlainText(self._files[filename])
            if filename in self._tree_items:
                self.tree.setCurrentItem(self._tree_items[filename])

    def _copy_current_code(self) -> None:
        code = self.code_viewer.toPlainText()
        if code:
            clipboard = QGuiApplication.clipboard()
            if clipboard:
                clipboard.setText(code)
                self.btn_copy.setText("Copied!")
                show_toast(self, "Code copied to clipboard", duration=1800)
                QTimer.singleShot(1500, lambda: self.btn_copy.setText("Copy Code"))

    def _save_single_file(self) -> None:
        filename = self.file_combo.currentText()
        content = self._files.get(filename, "")
        if not filename or not content:
            return

        suggested_name = Path(filename.replace("\\", "/")).name
        path, _ = QFileDialog.getSaveFileName(self, "Save File", suggested_name, "All Files (*)")
        if path:
            try:
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                show_toast(self, f"Saved to {p.name}")
            except Exception as e:
                QMessageBox.critical(self, "Save Failed", f"Could not save file: {e}")

    def _save_all_files(self) -> None:
        """Write all files preserving multi-level directory hierarchy."""
        if not self._files:
            return

        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Save All Files")
        if folder:
            out_dir = Path(folder)
            saved_count = 0
            unsafe_count = 0
            for name, code in self._files.items():
                try:
                    clean_rel = safe_relpath(name)
                except ValueError:
                    unsafe_count += 1
                    continue
                try:
                    file_path = out_dir / clean_rel
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(code, encoding="utf-8")
                    saved_count += 1
                except Exception:
                    pass
            msg = f"Successfully exported {saved_count} files with directory structure to:\n{folder}"
            if unsafe_count:
                msg += f"\n\n(Skipped {unsafe_count} unsafe file names)"
            show_toast(self, f"Saved {saved_count} files")
            if unsafe_count:
                QMessageBox.warning(self, "Partial export", msg)

    def _open_project_folder(self) -> None:
        if not getattr(self, "_current_workspace_id", None):
            return
        p = workspace.workspace_path(self._current_workspace_id)
        if p and p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _export_project_zip(self) -> None:
        if not getattr(self, "_current_workspace_id", None):
            return
        ws = workspace.get_workspace(self._current_workspace_id)
        slug = ws.get("slug", "project") if ws else "project"
        dest, _ = QFileDialog.getSaveFileName(self, "Export Workspace as ZIP", f"{slug}.zip", "Zip Files (*.zip)")
        if dest:
            if workspace.export_zip(self._current_workspace_id, dest):
                show_toast(self, "Workspace exported")
            else:
                QMessageBox.critical(self, "Export Failed", "Failed to export zip file.")

    def load_workspace(self, workspace_id: str) -> None:
        """Load all workspace files from disk into the artifacts panel."""
        self.clear()
        self._current_workspace_id = workspace_id
        ws = workspace.get_workspace(workspace_id)
        if not ws:
            return
        p = workspace.workspace_path(workspace_id)
        folder_str = str(p) if p else ws.get("slug", "")
        self.lbl_project_info.setText(
            f"<b>{ws.get('name', 'Project')}</b> &nbsp;"
            f"<span style='color: {DARK.colors.text_tertiary};'>({folder_str})</span>"
        )
        self.lbl_project_info.setVisible(True)
        self.btn_open_folder.setVisible(bool(p and p.exists()))
        self.btn_export_zip.setVisible(True)

        files = workspace.list_files(workspace_id)
        if not files:
            self.empty_state.setVisible(True)
            self.splitter.setVisible(False)
            return

        for f in files:
            content = workspace.read_file(workspace_id, f)
            if content is not None:
                self.add_file(f, content)

        preferred = next(
            (name for name in ("main.py", "app.py", "index.html") if name in self._files),
            files[0] if files else "",
        )
        if preferred:
            self.select_file(preferred)
        self.lbl_title.setText(str(ws.get("name", "Project")))
        self.empty_state.setVisible(False)
        self.splitter.setVisible(True)

    def select_file(self, filename: str) -> None:
        """Select a file by its complete relative path."""
        if filename not in self._files:
            return
        index = self.file_combo.findText(filename)
        if index >= 0:
            self.file_combo.setCurrentIndex(index)
        self.code_viewer.setPlainText(self._files[filename])
        item = self._tree_items.get(filename)
        if item is not None:
            self.tree.setCurrentItem(item)

    def prepare_for_viewer(self, project_name: str) -> None:
        """Hide the legacy panel chrome while retaining its file/tree API."""
        self.lbl_title.setText(project_name)
        self.lbl_count.hide()
        self.file_combo.hide()
        self.lbl_project_info.hide()
        self.btn_open_folder.hide()
        self.btn_export_zip.hide()
        self.btn_copy.hide()
        self.btn_save.hide()
        self.btn_save_all.hide()
        self.lbl_progress.hide()
        self.empty_label.setText("No project files yet.")
        self.empty_state.setVisible(not self._files)
        self.summary_widget.hide()
        self.splitter.setVisible(bool(self._files))
        self.splitter.setSizes([260, 700])
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        code_font = self.code_viewer.font()
        code_font.setFamilies(list(DARK.fonts.mono))
        code_font.setPointSize(13)
        self.code_viewer.setFont(code_font)
        self.code_viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def clear(self) -> None:
        self._current_workspace_id = None
        self._files.clear()
        self._tree_items.clear()
        self.tree.clear()
        self.file_combo.clear()
        self.code_viewer.clear()
        self.lbl_title.setText("Artifacts (0 files)")
        if hasattr(self, "lbl_project_info"):
            self.lbl_project_info.clear()
            self.lbl_project_info.setVisible(False)
        if hasattr(self, "btn_open_folder"):
            self.btn_open_folder.setVisible(False)
        if hasattr(self, "btn_export_zip"):
            self.btn_export_zip.setVisible(False)
        self.empty_state.setVisible(True)
        self.splitter.setVisible(False)
        self.summary_widget.setVisible(False)
