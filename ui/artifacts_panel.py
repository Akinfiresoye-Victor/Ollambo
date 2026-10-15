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
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ArtifactsPanel(QFrame):
    """Viewer for generated code files with tree structure, export, and clipboard options."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._files: dict[str, str] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "ArtifactsPanel { background-color: #1e2227; border: 1px solid #282c34; border-radius: 6px; padding: 6px; }"
        )
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Project Info Bar (Workspace title and folder path)
        proj_bar = QHBoxLayout()
        self.lbl_project_info = QLabel("")
        self.lbl_project_info.setStyleSheet("font-size: 12px; color: #98c379;")
        self.lbl_project_info.setVisible(False)
        proj_bar.addWidget(self.lbl_project_info)
        proj_bar.addStretch()

        self.btn_open_folder = QPushButton("📂 Open Folder")
        self.btn_open_folder.clicked.connect(self._open_project_folder)
        self.btn_open_folder.setVisible(False)
        proj_bar.addWidget(self.btn_open_folder)

        self.btn_export_zip = QPushButton("📦 Export .zip")
        self.btn_export_zip.clicked.connect(self._export_project_zip)
        self.btn_export_zip.setVisible(False)
        proj_bar.addWidget(self.btn_export_zip)

        layout.addLayout(proj_bar)

        # Header: File selector, title & actions
        top_bar = QHBoxLayout()
        self.lbl_title = QLabel("📁 Artifacts (0 files)")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #61afef;")
        top_bar.addWidget(self.lbl_title)

        top_bar.addSpacing(10)
        self.file_combo = QComboBox()
        self.file_combo.setMinimumWidth(180)
        self.file_combo.currentIndexChanged.connect(self._on_combo_selected)
        top_bar.addWidget(self.file_combo)
        top_bar.addStretch()

        # Action Buttons
        self.btn_copy = QPushButton("Copy Code")
        self.btn_copy.clicked.connect(self._copy_current_code)
        top_bar.addWidget(self.btn_copy)

        self.btn_save = QPushButton("Save File")
        self.btn_save.clicked.connect(self._save_single_file)
        top_bar.addWidget(self.btn_save)

        self.btn_save_all = QPushButton("💾 Save All to Folder")
        self.btn_save_all.setStyleSheet("font-weight: bold; background-color: #3e4451; color: white;")
        self.btn_save_all.clicked.connect(self._save_all_files)
        top_bar.addWidget(self.btn_save_all)

        layout.addLayout(top_bar)

        # Continuation / Testing Progress banner
        self.lbl_progress = QLabel("")
        self.lbl_progress.setStyleSheet("color: #e5c07b; font-size: 11px; font-style: italic; padding: 2px;")
        self.lbl_progress.setVisible(False)
        layout.addWidget(self.lbl_progress)

        # Splitter: Tree navigation on left, Code viewer on right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background-color: #282c34; width: 4px; }")

        # Left: Folder Structure Tree
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(4)

        tree_title = QLabel("Project Structure")
        tree_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #abb2bf; padding: 2px;")
        tree_layout.addWidget(tree_title)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setStyleSheet(
            "QTreeWidget { background-color: #181a1f; color: #abb2bf; border: 1px solid #282c34; "
            "border-radius: 4px; font-size: 12px; } "
            "QTreeWidget::item:selected { background-color: #2c313a; color: #61afef; }"
        )
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        tree_layout.addWidget(self.tree)
        splitter.addWidget(tree_container)

        # Right: Code viewer
        self.code_viewer = QPlainTextEdit()
        self.code_viewer.setReadOnly(True)
        self.code_viewer.setFont(QFont("Consolas", 10))
        self.code_viewer.setStyleSheet(
            "QPlainTextEdit { background-color: #14161a; color: #abb2bf; border: 1px solid #21252b; "
            "border-radius: 4px; font-family: Consolas, monospace; line-height: 1.4; }"
        )
        splitter.addWidget(self.code_viewer)

        splitter.setSizes([200, 600])
        layout.addWidget(splitter)

    def show_continuation_progress(self, filename: str, part: int, max_parts: int) -> None:
        """Show progress indicator when generating multi-part continuations."""
        self.lbl_progress.setText(f"⏳ Generating Part {part} of `{filename}` (limit {max_parts})…")
        self.lbl_progress.setVisible(True)

    def show_test_status(self, filename: str, status_msg: str) -> None:
        """Show progress indicator when running automated testing or code repairs."""
        self.lbl_progress.setText(status_msg)
        self.lbl_progress.setVisible(True)

    def add_file(self, filename: str, content: str) -> None:
        """Add or update an artifact file in the panel and tree view."""
        self.lbl_progress.setVisible(False)
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
        self.lbl_title.setText(f"📁 Artifacts ({count} {'file' if count == 1 else 'files'})")
        self.setVisible(True)

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
                new_item = QTreeWidgetItem([f"📁 {dir_name}/"])
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
            leaf_item.setText(0, f"📄 {file_name}")
        else:
            leaf_item = QTreeWidgetItem([f"📄 {file_name}"])
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
                QMessageBox.information(self, "Saved", f"Saved to {path}")
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
            QMessageBox.information(
                self,
                "Saved All",
                msg,
            )

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
                QMessageBox.information(self, "Exported", f"Successfully exported project to:\n{dest}")
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
            f"🏷️ <b>{ws.get('name', 'Project')}</b> &nbsp;<span style='color: #7f848e;'>({folder_str})</span>"
        )
        self.lbl_project_info.setVisible(True)
        self.btn_open_folder.setVisible(bool(p and p.exists()))
        self.btn_export_zip.setVisible(True)

        files = workspace.list_files(workspace_id)
        for f in files:
            content = workspace.read_file(workspace_id, f)
            if content is not None:
                self.add_file(f, content)

        if files:
            self.setVisible(True)

    def clear(self) -> None:
        self._current_workspace_id = None
        self._files.clear()
        self._tree_items.clear()
        self.tree.clear()
        self.file_combo.clear()
        self.code_viewer.clear()
        self.lbl_title.setText("📁 Artifacts (0 files)")
        if hasattr(self, "lbl_project_info"):
            self.lbl_project_info.clear()
            self.lbl_project_info.setVisible(False)
        if hasattr(self, "btn_open_folder"):
            self.btn_open_folder.setVisible(False)
        if hasattr(self, "btn_export_zip"):
            self.btn_export_zip.setVisible(False)
        self.setVisible(False)
