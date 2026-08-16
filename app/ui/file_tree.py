# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar file browser, filtered down to Markdown documents.

``QFileSystemModel`` with ``setNameFilterDisables(False)`` hides everything that
does not match, so the tree shows folders plus Markdown files and nothing else.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QDir, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QSizePolicy,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.file_service import MARKDOWN_GLOBS
from ..themes.palettes import Palette

log = logging.getLogger(__name__)

__all__ = ["FileTree"]


class _MarkdownFileSystemModel(QFileSystemModel):
    """File system model that appends an unsaved-changes marker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dirty_paths: set[Path] = set()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if (
            role == Qt.ItemDataRole.DisplayRole
            and index.isValid()
            and index.column() == 0
            and self.dirty_paths
        ):
            path = Path(self.filePath(index))
            if path in self.dirty_paths:
                return f"{super().data(index, role)} •"
        return super().data(index, role)


class FileTree(QWidget):
    """Folder tree that opens Markdown files on click."""

    file_activated = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("fileTreePanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._empty = QLabel(
            "No folder open.\n\nUse “Open Folder” to browse\nyour Markdown notes here."
        )
        self._empty.setObjectName("emptyBody")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setContentsMargins(16, 24, 16, 24)

        self._model = _MarkdownFileSystemModel(self)
        self._model.setNameFilters(list(MARKDOWN_GLOBS))
        self._model.setNameFilterDisables(False)
        self._model.setFilter(
            QDir.Filter.AllDirs
            | QDir.Filter.Files
            | QDir.Filter.NoDotAndDotDot
            | QDir.Filter.Hidden
        )
        self._model.setReadOnly(True)

        self._view = QTreeView(self)
        self._view.setModel(self._model)
        self._view.setHeaderHidden(True)
        self._view.setAnimated(True)
        self._view.setIndentation(14)
        self._view.setSortingEnabled(False)
        self._view.setUniformRowHeights(True)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setExpandsOnDoubleClick(True)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        for column in range(1, self._model.columnCount()):
            self._view.hideColumn(column)
        self._view.clicked.connect(self._on_clicked)
        self._view.activated.connect(self._on_clicked)
        self._view.hide()

        layout.addWidget(self._empty)
        layout.addWidget(self._view)

        self._root: Path | None = None

    # ---------------------------------------------------------------- state
    @property
    def root(self) -> Path | None:
        return self._root

    def set_root(self, directory: Path | None) -> None:
        """Show ``directory``; passing ``None`` returns to the empty state."""
        if directory is None or not directory.is_dir():
            self._root = None
            self._view.hide()
            self._empty.show()
            return
        self._root = directory
        self._model.setRootPath(str(directory))
        self._view.setRootIndex(self._model.index(str(directory)))
        self._empty.hide()
        self._view.show()

    def select_path(self, path: Path | None) -> None:
        """Highlight the row for ``path`` if it is inside the current root."""
        if path is None or self._root is None:
            self._view.clearSelection()
            return
        index = self._model.index(str(path))
        if index.isValid():
            self._view.setCurrentIndex(index)
            self._view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def mark_dirty(self, path: Path | None, dirty: bool) -> None:
        """Show or clear the unsaved-changes marker for a file in the tree."""
        if path is None:
            return
        if dirty:
            self._model.dirty_paths.add(path)
        else:
            self._model.dirty_paths.discard(path)
        index = self._model.index(str(path))
        if index.isValid():
            self._model.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])

    # ------------------------------------------------------------ appearance
    def apply_palette(self, palette: Palette) -> None:
        """Nothing to do beyond QSS today; kept for symmetry with siblings."""
        del palette

    # ---------------------------------------------------------------- events
    def _on_clicked(self, index: QModelIndex) -> None:
        if not index.isValid() or self._model.isDir(index):
            return
        path = Path(self._model.filePath(index))
        if path.is_file():
            self.file_activated.emit(path)
