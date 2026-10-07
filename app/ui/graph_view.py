# SPDX-License-Identifier: GPL-3.0-or-later
"""Obsidian-style graph of the notes in the open folder.

Every note is a dot, every ``[[link]]`` a line.  The layout is a small
force-directed simulation (springs along links, repulsion between all dots,
a weak pull to the centre) stepped on a timer until it settles, then left
alone so an idle graph costs nothing.

Painting is plain ``QPainter`` rather than a web view: the graph needs no
HTML, picks its colours straight from the palette tokens, and redraws only
the frames in which something moves.

Interaction: click a dot to open the note, click a dashed dot (a link to a
note that does not exist yet) to create it, drag dots around, drag the
background to pan, wheel to zoom, double-click the background to fit.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.vault import Vault
from ..themes.palettes import Palette

__all__ = ["GraphView"]

NodeKey = Path | str

#: Rest length of a link, in world units.
_SPRING_LENGTH = 70.0
_SPRING_STRENGTH = 0.06
_REPULSION = 2600.0
_GRAVITY = 0.012
_DAMPING = 0.55
_ALPHA_DECAY = 0.975
_ALPHA_MIN = 0.004
_CLICK_SLOP = 4.0


@dataclass
class _Node:
    key: NodeKey
    label: str
    exists: bool
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    degree: int = 0
    pinned: bool = False

    @property
    def radius(self) -> float:
        return 4.0 + 1.6 * math.sqrt(self.degree)


class _GraphCanvas(QWidget):
    """The drawing surface plus the simulation."""

    node_clicked = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self._nodes: dict[NodeKey, _Node] = {}
        self._edges: list[tuple[NodeKey, NodeKey]] = []
        self._adjacent: dict[NodeKey, set[NodeKey]] = {}
        self._current: NodeKey | None = None
        self._hover: NodeKey | None = None

        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self._auto_fit = True

        self._drag_node: _Node | None = None
        self._pan_from: QPointF | None = None
        self._press_pos: QPointF | None = None
        self._moved = False

        self._alpha = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._colors = {
            "bg": QColor("#282a36"),
            "edge": QColor("#44475a"),
            "edge_hi": QColor("#bd93f9"),
            "node": QColor("#6272a4"),
            "current": QColor("#bd93f9"),
            "hover": QColor("#ff79c6"),
            "text": QColor("#f8f8f2"),
            "muted": QColor("#6272a4"),
        }
        self._font = QFont(self.font())
        self._font.setPointSizeF(max(7.0, self._font.pointSizeF() * 0.85))

    # ---------------------------------------------------------------- data
    def set_graph(
        self,
        labels: dict[NodeKey, tuple[str, bool]],
        edges: list[tuple[NodeKey, NodeKey]],
        current: NodeKey | None,
    ) -> None:
        """Replace the graph, keeping the position of nodes that survive."""
        old = self._nodes
        structure_changed = set(old) != set(labels) or edges != self._edges
        self._nodes = {}
        self._edges = edges
        self._adjacent = {}
        for a, b in edges:
            self._adjacent.setdefault(a, set()).add(b)
            self._adjacent.setdefault(b, set()).add(a)

        rng = random.Random(len(labels))
        for key, (label, exists) in labels.items():
            node = old.get(key)
            if node is None:
                node = _Node(key=key, label=label, exists=exists)
                anchor = next((old[n] for n in self._adjacent.get(key, ()) if n in old), None)
                angle = rng.uniform(0, math.tau)
                spread = 30.0 if anchor else 40.0 + 12.0 * math.sqrt(len(labels))
                cx, cy = (anchor.x, anchor.y) if anchor else (0.0, 0.0)
                node.x = cx + math.cos(angle) * spread * rng.uniform(0.3, 1.0)
                node.y = cy + math.sin(angle) * spread * rng.uniform(0.3, 1.0)
            node.label, node.exists = label, exists
            node.degree = len(self._adjacent.get(key, ()))
            self._nodes[key] = node

        self._current = current if current in self._nodes else None
        if self._hover not in self._nodes:
            self._hover = None
        if structure_changed:
            self.reheat(1.0 if not old else 0.6)
        self.update()

    def set_current(self, key: NodeKey | None) -> None:
        self._current = key if key in self._nodes else None
        self.update()

    def set_colors(self, colors: dict[str, QColor]) -> None:
        self._colors.update(colors)
        self.update()

    # ---------------------------------------------------------- simulation
    def reheat(self, alpha: float = 0.5) -> None:
        self._alpha = max(self._alpha, alpha)
        if not self._timer.isActive() and self._nodes:
            # Repulsion is O(n²): give big graphs a longer frame.
            n = len(self._nodes)
            self._timer.start(max(16, int(n * n / 3000)))

    def _tick(self) -> None:
        nodes = list(self._nodes.values())
        alpha = self._alpha
        if not nodes or alpha < _ALPHA_MIN:
            self._timer.stop()
            self._alpha = 0.0
            return

        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                dx, dy = a.x - b.x, a.y - b.y
                dist2 = dx * dx + dy * dy
                if dist2 < 0.01:
                    dx, dy, dist2 = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
                if dist2 > 250_000:
                    continue
                force = _REPULSION * alpha / dist2
                dist = math.sqrt(dist2)
                fx, fy = dx / dist * force, dy / dist * force
                a.vx += fx
                a.vy += fy
                b.vx -= fx
                b.vy -= fy

        for ka, kb in self._edges:
            a, b = self._nodes.get(ka), self._nodes.get(kb)
            if a is None or b is None:
                continue
            dx, dy = b.x - a.x, b.y - a.y
            dist = math.hypot(dx, dy) or 0.01
            force = (dist - _SPRING_LENGTH) * _SPRING_STRENGTH * alpha
            fx, fy = dx / dist * force, dy / dist * force
            a.vx += fx
            a.vy += fy
            b.vx -= fx
            b.vy -= fy

        for node in nodes:
            node.vx -= node.x * _GRAVITY * alpha
            node.vy -= node.y * _GRAVITY * alpha
            if node.pinned:
                node.vx = node.vy = 0.0
                continue
            node.vx *= _DAMPING
            node.vy *= _DAMPING
            node.x += max(-40.0, min(40.0, node.vx))
            node.y += max(-40.0, min(40.0, node.vy))

        if self._drag_node is None:
            self._alpha *= _ALPHA_DECAY
        if self._auto_fit:
            self._fit(animate=True)
        self.update()

    # ------------------------------------------------------------- viewport
    def _bounds(self) -> QRectF:
        xs = [n.x for n in self._nodes.values()]
        ys = [n.y for n in self._nodes.values()]
        if not xs:
            return QRectF(-50, -50, 100, 100)
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)).adjusted(
            -40, -30, 40, 30
        )

    def _fit(self, animate: bool = False) -> None:
        bounds = self._bounds()
        if self.width() < 10 or self.height() < 10:
            return
        scale = min(self.width() / max(bounds.width(), 1), self.height() / max(bounds.height(), 1))
        scale = max(0.15, min(2.2, scale))
        centre = bounds.center()
        offset = QPointF(-centre.x() * scale, -centre.y() * scale)
        if animate:
            self._scale += (scale - self._scale) * 0.15
            self._offset += (offset - self._offset) * 0.15
        else:
            self._scale, self._offset = scale, offset

    def fit(self) -> None:
        self._auto_fit = True
        self._fit()
        self.update()

    def _to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(
            x * self._scale + self.width() / 2 + self._offset.x(),
            y * self._scale + self.height() / 2 + self._offset.y(),
        )

    def _to_world(self, p: QPointF) -> QPointF:
        return QPointF(
            (p.x() - self.width() / 2 - self._offset.x()) / self._scale,
            (p.y() - self.height() / 2 - self._offset.y()) / self._scale,
        )

    def _node_at(self, pos: QPointF) -> _Node | None:
        best, best_dist = None, 1e9
        for node in self._nodes.values():
            centre = self._to_screen(node.x, node.y)
            dist = math.hypot(centre.x() - pos.x(), centre.y() - pos.y())
            reach = max(8.0, node.radius * self._scale + 4)
            if dist <= reach and dist < best_dist:
                best, best_dist = node, dist
        return best

    # -------------------------------------------------------------- painting
    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self._colors["bg"])
        if not self._nodes:
            painter.setPen(self._colors["muted"])
            painter.drawText(
                self.rect().adjusted(16, 16, -16, -16),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "No linked notes yet.\n\nOpen a folder and connect notes with [[Note name]].",
            )
            return

        focus = self._hover if self._hover is not None else self._current
        lit = {focus} | self._adjacent.get(focus, set()) if focus is not None else set()

        edge_pen = QPen(self._colors["edge"], max(0.6, 1.0 * min(self._scale, 1.4)))
        hi_pen = QPen(self._colors["edge_hi"], max(1.0, 1.6 * min(self._scale, 1.4)))
        for ka, kb in self._edges:
            a, b = self._nodes.get(ka), self._nodes.get(kb)
            if a is None or b is None:
                continue
            highlighted = focus is not None and focus in (ka, kb)
            painter.setPen(hi_pen if highlighted else edge_pen)
            painter.drawLine(self._to_screen(a.x, a.y), self._to_screen(b.x, b.y))

        painter.setFont(self._font)
        metrics = QFontMetricsF(self._font)
        show_all_labels = self._scale >= 0.75 or len(self._nodes) <= 25
        dimmed = focus is not None
        for node in self._nodes.values():
            centre = self._to_screen(node.x, node.y)
            radius = max(2.5, node.radius * min(self._scale, 1.6))
            if node.key == self._hover:
                color = self._colors["hover"]
            elif node.key == self._current:
                color = self._colors["current"]
            else:
                color = QColor(self._colors["node"])
                if dimmed and node.key not in lit:
                    color.setAlphaF(0.35)
            if node.exists:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
            else:
                pen = QPen(color, 1.4, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius, radius)

            if show_all_labels or node.key in lit or node.key == self._current:
                text_color = QColor(self._colors["text"] if node.exists else self._colors["muted"])
                if dimmed and node.key not in lit:
                    text_color.setAlphaF(0.35)
                painter.setPen(text_color)
                label = metrics.elidedText(node.label, Qt.TextElideMode.ElideRight, 160)
                width = metrics.horizontalAdvance(label)
                painter.drawText(
                    QPointF(centre.x() - width / 2, centre.y() + radius + metrics.ascent() + 2),
                    label,
                )
        painter.end()

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        self._press_pos = pos
        self._moved = False
        node = self._node_at(pos)
        if node is not None:
            self._drag_node = node
            node.pinned = True
        else:
            self._pan_from = pos

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        pos = event.position()
        if self._press_pos is not None and (
            abs(pos.x() - self._press_pos.x()) + abs(pos.y() - self._press_pos.y()) > _CLICK_SLOP
        ):
            self._moved = True
        if self._drag_node is not None and self._moved:
            world = self._to_world(pos)
            self._drag_node.x, self._drag_node.y = world.x(), world.y()
            self._auto_fit = False
            self.reheat(0.3)
            self.update()
            return
        if self._pan_from is not None and self._moved:
            self._offset += pos - self._pan_from
            self._pan_from = pos
            self._auto_fit = False
            self.update()
            return
        node = self._node_at(pos)
        key = node.key if node is not None else None
        if key != self._hover:
            self._hover = key
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if node else Qt.CursorShape.ArrowCursor
            )
            self.setToolTip(
                (str(key) if isinstance(key, str) else node.label) if node else ""
            )
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        node = self._drag_node
        if node is not None:
            node.pinned = False
            if not self._moved:
                self.node_clicked.emit(node.key)
        self._drag_node = None
        self._pan_from = None
        self._press_pos = None

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and self._node_at(event.position()) is None:
            self.fit()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        factor = 1.0015 ** event.angleDelta().y()
        new_scale = max(0.1, min(5.0, self._scale * factor))
        anchor = event.position()
        world = self._to_world(anchor)
        self._scale = new_scale
        after = self._to_screen(world.x(), world.y())
        self._offset += anchor - after
        self._auto_fit = False
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._auto_fit:
            self._fit()
        super().resizeEvent(event)


class GraphView(QWidget):
    """Graph pane: header, canvas and the backlinks of the current note."""

    #: An existing note was chosen (graph dot or backlink).
    note_activated = pyqtSignal(Path)
    #: A link to a note that does not exist yet was clicked.
    missing_activated = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("graphPane")
        self.setMinimumWidth(220)
        self._vault: Vault | None = None
        self._current: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("sideMenuHeader")
        header.setFixedHeight(38)
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 0, 8, 0)
        row.setSpacing(4)
        title = QLabel("Graph", header)
        title.setObjectName("sectionLabel")
        self._count = QLabel("", header)
        self._count.setObjectName("statusLabel")
        row.addWidget(title)
        row.addWidget(self._count)
        row.addStretch(1)

        self._local = QToolButton(header)
        self._local.setText("Local")
        self._local.setCheckable(True)
        self._local.setToolTip("Only the current note and the notes it touches")
        self._local.setCursor(Qt.CursorShape.PointingHandCursor)
        self._local.toggled.connect(lambda _on: self.rebuild())
        row.addWidget(self._local)

        self._fit = QToolButton(header)
        self._fit.setText("Fit")
        self._fit.setToolTip("Fit the whole graph in view (or double-click the background)")
        self._fit.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self._fit)
        layout.addWidget(header)

        split = QSplitter(Qt.Orientation.Vertical, self)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        self._canvas = _GraphCanvas(split)
        self._canvas.node_clicked.connect(self._on_node_clicked)
        self._fit.clicked.connect(self._canvas.fit)

        backlinks = QWidget(split)
        back_layout = QVBoxLayout(backlinks)
        back_layout.setContentsMargins(0, 0, 0, 0)
        back_layout.setSpacing(0)
        self._backlinks_label = QLabel("Backlinks", backlinks)
        self._backlinks_label.setObjectName("sectionLabel")
        self._backlinks_label.setContentsMargins(14, 8, 8, 4)
        self._backlinks = QListWidget(backlinks)
        self._backlinks.setObjectName("backlinks")
        self._backlinks.itemActivated.connect(self._on_backlink)
        self._backlinks.itemClicked.connect(self._on_backlink)
        back_layout.addWidget(self._backlinks_label)
        back_layout.addWidget(self._backlinks, 1)

        split.addWidget(self._canvas)
        split.addWidget(backlinks)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

    # ---------------------------------------------------------------- state
    def set_vault(self, vault: Vault | None) -> None:
        self._vault = vault
        self.rebuild()

    def set_current(self, path: Path | None) -> None:
        path = path.resolve() if path is not None else None
        if path == self._current:
            return
        self._current = path
        if self._local.isChecked():
            self.rebuild()
        else:
            self._canvas.set_current(path)
            self._refresh_backlinks()

    def rebuild(self) -> None:
        """Re-read nodes and edges from the vault."""
        vault = self._vault
        if vault is None:
            self._canvas.set_graph({}, [], None)
            self._count.setText("")
            self._refresh_backlinks()
            return

        labels: dict[NodeKey, tuple[str, bool]] = {
            path: (note.title, True) for path, note in vault.notes.items()
        }
        edges: list[tuple[NodeKey, NodeKey]] = []
        seen: set[frozenset[NodeKey]] = set()
        for source, target in vault.edges():
            pair = frozenset((source, target))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append((source, target))
            if isinstance(target, str):
                labels[target] = (target, False)

        if self._local.isChecked() and self._current in labels:
            keep = vault.neighbourhood(self._current, depth=1)
            labels = {k: v for k, v in labels.items() if k in keep}
            edges = [(a, b) for a, b in edges if a in keep and b in keep]

        notes = sum(1 for _label, exists in labels.values() if exists)
        self._count.setText(f"· {notes} notes · {len(edges)} links")
        self._canvas.set_graph(labels, edges, self._current)
        self._refresh_backlinks()

    def _refresh_backlinks(self) -> None:
        self._backlinks.clear()
        vault, current = self._vault, self._current
        if vault is None or current is None or current not in vault.notes:
            self._backlinks_label.setText("Backlinks")
            return
        sources: dict[Path, int] = {}
        for source, _link in vault.backlinks(current):
            sources[source] = sources.get(source, 0) + 1
        self._backlinks_label.setText(f"Backlinks · {len(sources)}")
        for source in sorted(sources, key=lambda p: vault.notes[p].title.casefold()):
            item = QListWidgetItem(vault.notes[source].title)
            item.setToolTip(vault.rel(source))
            item.setData(Qt.ItemDataRole.UserRole, str(source))
            self._backlinks.addItem(item)

    # -------------------------------------------------------------- palette
    def apply_palette(self, palette: Palette) -> None:
        t = palette.tokens()
        edge = QColor(t["border_strong"])
        self._canvas.set_colors(
            {
                "bg": QColor(t["bg"]),
                "edge": edge,
                "edge_hi": QColor(t["accent_on_bg"]),
                "node": QColor(t["muted_on_bg"]),
                "current": QColor(t["accent_on_bg"]),
                "hover": QColor(t["accent2_on_bg"]),
                "text": QColor(t["text"]),
                "muted": QColor(t["muted_on_bg"]),
            }
        )

    # --------------------------------------------------------------- events
    def _on_node_clicked(self, key: object) -> None:
        if isinstance(key, Path):
            self.note_activated.emit(key)
        elif isinstance(key, str):
            self.missing_activated.emit(key)

    def _on_backlink(self, item: QListWidgetItem) -> None:
        self.note_activated.emit(Path(item.data(Qt.ItemDataRole.UserRole)))
