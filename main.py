#!/usr/bin/env python3
# Run: python main.py [folder]

import sys
import re
import json
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from PIL.ImageQt import ImageQt

from PySide6.QtCore import Qt, QTimer, QPoint, QSize, QRect
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QScrollArea,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QSizePolicy,
    QMessageBox,
    QFileDialog,
    QMenu,
    QLayout,
    QDialog,
    QSlider,
)


ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SLICE_HEIGHT = 4000
ASPECT_RATIO = 19.5 / 9.0  # height / width
PREVIEW_MARGIN = 8
PREVIEW_SPACING = 8
MENU_OPEN_LABEL = "\u30d5\u30a9\u30eb\u30c0\u3092\u958b\u304f"
MENU_PREVIEW_LEFT_LABEL = "\u30d7\u30ec\u30d3\u30e5\u30fc\u3092\u5de6\u306b\u8868\u793a"
MENU_PREVIEW_RIGHT_LABEL = "\u30d7\u30ec\u30d3\u30e5\u30fc\u3092\u53f3\u306b\u8868\u793a"
MENU_INERTIAL_ON_LABEL = "\u6163\u6027\u30b9\u30af\u30ed\u30fc\u30eb ON"
MENU_INERTIAL_OFF_LABEL = "\u6163\u6027\u30b9\u30af\u30ed\u30fc\u30eb OFF"
MENU_SCROLL_SETTINGS_LABEL = "\u30b9\u30af\u30ed\u30fc\u30eb\u8a2d\u5b9a"
MENU_RELOAD_FOLDER_LABEL = "\u30d5\u30a9\u30eb\u30c0\u3092\u518d\u8aad\u307f\u8fbc\u307f"
MENU_RECENT_FOLDERS_LABEL = "\u6700\u8fd1\u958b\u3044\u305f\u30d5\u30a9\u30eb\u30c0"
MENU_RECENT_EMPTY_LABEL = "\uff08\u5c65\u6b74\u306a\u3057\uff09"
RECENT_FOLDERS_LIMIT = 10

DEFAULT_SCROLL_SETTINGS = {
    "wheel_speed": 0.10,
    "wheel_inertia": 1.13,
    "friction": 0.86,
    "drag_sensitivity": 1.00,
}
SCROLL_RANGES = {
    "wheel_speed": (0.1, 3.0),
    "wheel_inertia": (0.5, 3.0),
    "friction": (0.80, 0.99),
    "drag_sensitivity": (0.1, 3.0),
}


_num_re = re.compile(r"(\d+)")


def natural_key(path: Path):
    parts = _num_re.split(path.name)
    key = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


def collect_images(folder: Path):
    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTS:
            files.append(p)
    files.sort(key=natural_key)
    return files


def _clamp(value, min_v, max_v):
    return max(min_v, min(max_v, value))


def _event_global_y(event):
    try:
        return event.globalPosition().y()
    except AttributeError:
        return float(event.globalPos().y())


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def settings_path():
    return get_base_dir() / "settings.json"


def history_path():
    return get_base_dir() / "folder_history.json"


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_legacy_config():
    legacy_path = get_base_dir() / "config.json"
    data = _load_json(legacy_path)
    if isinstance(data, dict):
        return data
    return {}


def load_scroll_settings():
    settings = dict(DEFAULT_SCROLL_SETTINGS)
    path = settings_path()
    data = _load_json(path)
    used_legacy = False
    if data is None:
        legacy = _load_legacy_config()
        if legacy:
            data = legacy
            used_legacy = True
    if not isinstance(data, dict):
        data = {}
    legacy_map = {
        "wheel_sensitivity": "wheel_speed",
        "inertia_strength": "wheel_inertia",
    }
    for old_key, new_key in legacy_map.items():
        if old_key in data and new_key not in data:
            data[new_key] = data[old_key]
    for key, (min_v, max_v) in SCROLL_RANGES.items():
        value = data.get(key, settings[key])
        try:
            value = float(value)
        except Exception:
            value = settings[key]
        settings[key] = _clamp(value, min_v, max_v)
    if not path.exists() or used_legacy:
        save_scroll_settings(settings)
    return settings


def save_scroll_settings(settings):
    path = settings_path()
    data = {k: float(v) for k, v in settings.items()}
    _save_json(path, data)


def load_recent_folders():
    history_file = history_path()
    items = _load_json(history_file)
    used_legacy = False
    if not isinstance(items, list):
        legacy = _load_legacy_config()
        if isinstance(legacy, dict):
            items = legacy.get("recent_folders", [])
            used_legacy = True
    if not isinstance(items, list):
        items = []
    cleaned = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            continue
        path_str = item.strip()
        if not path_str or path_str in seen:
            continue
        cleaned.append(path_str)
        seen.add(path_str)
        if len(cleaned) >= RECENT_FOLDERS_LIMIT:
            break
    if not history_file.exists() or used_legacy:
        save_recent_folders(cleaned)
    return cleaned


def save_recent_folders(recent_folders):
    path = history_path()
    data = list(recent_folders)[:RECENT_FOLDERS_LIMIT]
    _save_json(path, data)


def process_image(path: Path):
    slices = []
    orig_w = None
    orig_h = None
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            width, height = im.size
            orig_w = width
            orig_h = height
            y = 0
            while y < height:
                y_end = min(y + SLICE_HEIGHT, height)
                crop = im.crop((0, y, width, y_end))
                qimg = ImageQt(crop)
                slices.append(qimg)
                y = y_end
    except Exception as exc:
        print(f"Failed to load {path}: {exc}", file=sys.stderr)
    return slices, orig_w, orig_h


class ImageSliceLabel(QLabel):
    def __init__(self, qimage, parent=None, context_menu_cb=None):
        super().__init__(parent)
        self._base_image = qimage
        self._base_w = qimage.width()
        self._base_h = qimage.height()
        self._last_target_w = -1
        self._context_menu_cb = context_menu_cb
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def update_scale(self, target_w: int):
        if target_w <= 0 or target_w == self._last_target_w:
            return
        self._last_target_w = target_w
        target_h = max(1, int(target_w * self._base_h / self._base_w))
        pix = QPixmap.fromImage(self._base_image).scaled(
            target_w,
            target_h,
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        self.setPixmap(pix)
        self.setFixedHeight(target_h)

    def contextMenuEvent(self, event):
        if self._context_menu_cb is not None:
            self._context_menu_cb(event.globalPos())
        else:
            super().contextMenuEvent(event)


class ImageViewer(QWidget):
    def __init__(self, parent=None, context_menu_cb=None, scroll_area=None):
        super().__init__(parent)
        self._labels = []
        self._context_menu_cb = context_menu_cb
        self._scroll_area = scroll_area
        self._drag_active = False
        self._dragging = False
        self._drag_start_y = 0.0
        self._last_mouse_y = 0.0
        self.setMouseTracking(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignTop)
        self.setLayout(layout)

    def add_slice(self, qimage):
        label = ImageSliceLabel(qimage, self, context_menu_cb=self._context_menu_cb)
        self.layout().addWidget(label)
        self._labels.append(label)
        return label

    def set_target_width(self, width: int):
        for label in self._labels:
            label.update_scale(width)

    def clear(self):
        for label in self._labels:
            self.layout().removeWidget(label)
            label.setParent(None)
            label.deleteLater()
        self._labels.clear()

    def contextMenuEvent(self, event):
        if self._context_menu_cb is not None:
            self._context_menu_cb(event.globalPos())
        else:
            super().contextMenuEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._dragging = False
            current_y = _event_global_y(event)
            self._drag_start_y = current_y
            self._last_mouse_y = current_y
            if self._scroll_area is not None:
                self._scroll_area.stop_inertia()
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._drag_active:
            super().mouseMoveEvent(event)
            return
        current_y = _event_global_y(event)
        if not self._dragging:
            if abs(current_y - self._drag_start_y) >= 5:
                self._dragging = True
                self._last_mouse_y = current_y
                event.accept()
                return
        if self._dragging and self._scroll_area is not None:
            mouse_delta = current_y - self._last_mouse_y
            bar = self._scroll_area.verticalScrollBar()
            new_value = bar.value() - mouse_delta * self._scroll_area.drag_sensitivity()
            new_value = _clamp(new_value, bar.minimum(), bar.maximum())
            bar.setValue(int(new_value))
            self._last_mouse_y = current_y
            event.accept()
            return
        self._last_mouse_y = current_y
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_active:
            self._drag_active = False
            self._dragging = False
            self.releaseMouse()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PreviewLabel(QLabel):
    def __init__(self, qimage, parent=None, context_menu_cb=None):
        super().__init__(parent)
        self._context_menu_cb = context_menu_cb
        self._on_left_click = None
        self.setPixmap(QPixmap.fromImage(qimage))
        self.setFixedSize(self.pixmap().size())
        self.setCursor(Qt.PointingHandCursor)

    def set_left_click(self, callback):
        self._on_left_click = callback

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._on_left_click is not None:
            self._on_left_click(event)
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        if self._context_menu_cb is not None:
            self._context_menu_cb(event.globalPos())
        else:
            super().contextMenuEvent(event)


class PreviewContainer(QWidget):
    def __init__(self, context_menu_cb=None, parent=None):
        super().__init__(parent)
        self._context_menu_cb = context_menu_cb

    def contextMenuEvent(self, event):
        if self._context_menu_cb is not None:
            self._context_menu_cb(event.globalPos())
        else:
            super().contextMenuEvent(event)


class InertialScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._velocity = 0.0
        self._inertial_enabled = True
        self._wheel_speed = DEFAULT_SCROLL_SETTINGS["wheel_speed"]
        self._wheel_inertia = DEFAULT_SCROLL_SETTINGS["wheel_inertia"]
        self._drag_sensitivity = DEFAULT_SCROLL_SETTINGS["drag_sensitivity"]
        self._friction = DEFAULT_SCROLL_SETTINGS["friction"]
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_inertial_tick)

    def set_inertial_enabled(self, enabled: bool):
        self._inertial_enabled = enabled
        if not enabled:
            self.stop_inertia()

    def inertial_enabled(self):
        return self._inertial_enabled

    def stop_inertia(self):
        self._velocity = 0.0
        self._timer.stop()

    def start_inertia(self, velocity):
        self._velocity = float(velocity)
        if abs(self._velocity) < 0.1:
            self.stop_inertia()
            return
        if not self._timer.isActive():
            self._timer.start()

    def set_scroll_params(self, wheel_speed, wheel_inertia, friction, drag_sensitivity):
        self._wheel_speed = float(wheel_speed)
        self._wheel_inertia = float(wheel_inertia)
        self._friction = float(friction)
        self._drag_sensitivity = float(drag_sensitivity)

    def wheel_speed(self):
        return self._wheel_speed

    def drag_sensitivity(self):
        return self._drag_sensitivity

    def wheel_inertia(self):
        return self._wheel_inertia

    def friction(self):
        return self._friction

    def wheelEvent(self, event):
        if not self._inertial_enabled:
            super().wheelEvent(event)
            return
        self.stop_inertia()
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            event.ignore()
            return
        self._velocity += delta * self._wheel_speed
        if not self._timer.isActive():
            self._timer.start()
        event.accept()

    def _on_inertial_tick(self):
        bar = self.verticalScrollBar()
        if bar is None:
            self.stop_inertia()
            return
        new_value = bar.value() - self._velocity
        if new_value < bar.minimum():
            new_value = bar.minimum()
            self._velocity = 0.0
        elif new_value > bar.maximum():
            new_value = bar.maximum()
            self._velocity = 0.0
        bar.setValue(int(new_value))
        self._velocity *= self._wheel_inertia
        self._velocity *= self._friction
        if abs(self._velocity) < 0.1:
            self.stop_inertia()


class VerticalFlowLayout(QLayout):
    def __init__(self, parent=None, spacing=0, margins=(0, 0, 0, 0)):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(*margins)
        self._last_height = None

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return False

    def sizeHint(self):
        if self._last_height is None:
            return self.minimumSize()
        return self.sizeForHeight(self._last_height)

    def minimumSize(self):
        left, top, right, bottom = self.getContentsMargins()
        width = left + right
        height = top + bottom
        for item in self._items:
            size = item.sizeHint()
            width = max(width, size.width() + left + right)
            height += size.height() + self.spacing()
        return QSize(width, height)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._last_height = rect.height()
        self._do_layout(rect, False)

    def sizeForHeight(self, height):
        rect = QRect(0, 0, 0, height)
        return self._do_layout(rect, True)

    def _do_layout(self, rect, test_only):
        left, top, right, bottom = self.getContentsMargins()
        x = rect.x() + left
        y = rect.y() + top
        max_height = rect.height() - top - bottom
        if max_height <= 0:
            max_height = 10**9
        column_width = 0
        total_width = left + right
        for item in self._items:
            size = item.sizeHint()
            if y > rect.y() + top and y + size.height() > rect.y() + top + max_height:
                x += column_width + self.spacing()
                y = rect.y() + top
                column_width = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), size))
            y += size.height() + self.spacing()
            column_width = max(column_width, size.width())
            total_width = max(total_width, x - rect.x() + column_width + right)
        total_height = max_height + top + bottom
        return QSize(total_width, total_height)


class PreviewPanel(QWidget):
    def __init__(self, parent=None, context_menu_cb=None):
        super().__init__(parent)
        self._context_menu_cb = context_menu_cb
        self._widgets = []
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = PreviewContainer(context_menu_cb=context_menu_cb)
        self.flow = VerticalFlowLayout(
            self.container, spacing=PREVIEW_SPACING, margins=(PREVIEW_MARGIN, PREVIEW_MARGIN, PREVIEW_MARGIN, PREVIEW_MARGIN)
        )
        self.container.setLayout(self.flow)
        self.scroll.setWidget(self.container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll)
        self.setLayout(layout)

    def clear(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            if item is not None and item.widget() is not None:
                w = item.widget()
                w.setParent(None)
                w.deleteLater()
        self._widgets.clear()
        self._update_layout()

    def add_preview(self, qimage):
        label = PreviewLabel(qimage, self.container, context_menu_cb=self._context_menu_cb)
        self.flow.addWidget(label)
        self._widgets.append(label)
        self._update_layout()
        return label

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_layout)

    def _update_layout(self):
        viewport_h = self.scroll.viewport().height()
        if viewport_h <= 0:
            return
        size = self.flow.sizeForHeight(viewport_h)
        self.container.setMinimumHeight(viewport_h)
        self.container.setMinimumWidth(size.width())
        self.container.resize(size.width(), viewport_h)


class ScrollSettingsDialog(QDialog):
    def __init__(self, parent, settings, on_change=None, on_save=None):
        super().__init__(parent)
        self.setWindowTitle(MENU_SCROLL_SETTINGS_LABEL)
        self._settings = settings
        self._on_change = on_change
        self._on_save = on_save

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self._add_slider(
            layout,
            "wheel_speed",
            "\u30db\u30a4\u30fc\u30eb\u901f\u5ea6",
            0.1,
            3.0,
            100,
            2,
        )
        self._add_slider(
            layout,
            "wheel_inertia",
            "\u30db\u30a4\u30fc\u30eb\u6163\u6027",
            0.5,
            3.0,
            100,
            2,
        )
        self._add_slider(
            layout,
            "friction",
            "\u30db\u30a4\u30fc\u30eb\u6e1b\u901f",
            0.80,
            0.99,
            100,
            2,
        )
        self._add_slider(
            layout,
            "drag_sensitivity",
            "\u30c9\u30e9\u30c3\u30b0\u901f\u5ea6",
            0.1,
            3.0,
            100,
            2,
        )

    def _add_slider(self, layout, key, title, min_v, max_v, scale, decimals):
        label = QLabel(self)
        slider = QSlider(Qt.Horizontal, self)
        slider.setRange(int(min_v * scale), int(max_v * scale))
        slider.setValue(int(self._settings.get(key, min_v) * scale))
        label.setText(f"{title}: {self._settings.get(key, min_v):.{decimals}f}")

        def on_value_changed(value):
            float_value = value / scale
            self._settings[key] = float_value
            label.setText(f"{title}: {float_value:.{decimals}f}")
            if self._on_change is not None:
                self._on_change(self._settings)

        slider.valueChanged.connect(on_value_changed)
        layout.addWidget(label)
        layout.addWidget(slider)

    def closeEvent(self, event):
        if self._on_save is not None:
            self._on_save(self._settings)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, folder: Optional[Path]):
        super().__init__()
        self._pages = []
        self._max_ratio = 0.0
        self._current_thumb_w = None
        self._preview_on_left = False
        self._scroll_settings = load_scroll_settings()
        self._recent_folders = load_recent_folders()
        self._settings_dialog = None
        self._current_folder = folder
        self._pending_scroll_ratio = None
        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.timeout.connect(self._refresh_previews)

        self.setWindowTitle("Webtoon Viewer")

        central = QWidget(self)
        self._main_layout = QHBoxLayout(central)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        self.scroll_area = InertialScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._apply_scroll_settings()

        self.viewer = ImageViewer(
            self, context_menu_cb=self._show_context_menu, scroll_area=self.scroll_area
        )
        self.scroll_area.setWidget(self.viewer)

        self.preview_panel = PreviewPanel(self, context_menu_cb=self._show_context_menu)
        self.preview_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._apply_preview_position()
        central.setLayout(self._main_layout)
        self.setCentralWidget(central)

        if folder is not None:
            self._open_folder(folder, show_warning=True, update_history=True)

        initial_viewer_w = 540
        initial_h = int(initial_viewer_w * ASPECT_RATIO)
        initial_total_w = initial_viewer_w + 320
        self.resize(initial_total_w, initial_h)
        QTimer.singleShot(0, self._rescale_to_viewport)

    def _load_images(self, folder: Path, show_warning: bool, keep_scroll_ratio: Optional[float] = None):
        images = collect_images(folder)
        self._current_folder = folder
        self._pending_scroll_ratio = keep_scroll_ratio
        self.viewer.setUpdatesEnabled(False)
        self.viewer.clear()
        self.preview_panel.clear()
        self._pages = []
        self._max_ratio = 0.0
        self._current_thumb_w = None
        self.scroll_area.stop_inertia()
        if not images:
            self.viewer.setUpdatesEnabled(True)
            self._pending_scroll_ratio = None
            if show_warning:
                QMessageBox.warning(self, "No Images", "No supported images found.")
            return
        for img_path in images:
            slices, orig_w, orig_h = process_image(img_path)
            first_label = None
            for idx, qimg in enumerate(slices):
                label = self.viewer.add_slice(qimg)
                if idx == 0:
                    first_label = label
            if first_label is not None and orig_w and orig_h:
                self._pages.append((img_path, first_label, orig_w, orig_h))
                ratio = orig_h / orig_w
                if ratio > self._max_ratio:
                    self._max_ratio = ratio
        self.viewer.setUpdatesEnabled(True)
        if keep_scroll_ratio is None:
            self.scroll_area.verticalScrollBar().setValue(0)
        self._schedule_preview_refresh()
        QTimer.singleShot(0, self._rescale_to_viewport)

    def _rescale_to_viewport(self):
        viewport_w = self.scroll_area.viewport().width()
        self.viewer.set_target_width(viewport_w)
        self._apply_pending_scroll()

    def _apply_pending_scroll(self):
        if self._pending_scroll_ratio is None:
            return
        ratio = _clamp(self._pending_scroll_ratio, 0.0, 1.0)
        self._pending_scroll_ratio = None

        def apply():
            bar = self.scroll_area.verticalScrollBar()
            max_v = bar.maximum()
            if max_v <= 0:
                bar.setValue(0)
                return
            bar.setValue(int(max_v * ratio))

        QTimer.singleShot(0, apply)

    def showEvent(self, event):
        super().showEvent(event)
        available_h = self.centralWidget().height()
        self.scroll_area.setFixedWidth(max(1, int(available_h / ASPECT_RATIO)))
        self.preview_panel._update_layout()
        self._schedule_preview_refresh()
        QTimer.singleShot(0, self._rescale_to_viewport)

    def resizeEvent(self, event):
        available_h = self.centralWidget().height()
        viewer_w = max(1, int(available_h / ASPECT_RATIO))
        self.scroll_area.setFixedWidth(viewer_w)
        self._rescale_to_viewport()
        self.preview_panel._update_layout()
        self._schedule_preview_refresh()
        super().resizeEvent(event)

    def _open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder")
        if not folder:
            return
        self._open_folder(Path(folder), show_warning=True, update_history=True)

    def _normalize_folder_path(self, folder_path: Path):
        try:
            return folder_path.expanduser().resolve()
        except Exception:
            return folder_path

    def _open_folder(self, folder_path: Path, show_warning: bool, update_history: bool = True):
        normalized = self._normalize_folder_path(folder_path)
        if not normalized.exists() or not normalized.is_dir():
            if show_warning:
                QMessageBox.warning(self, "Invalid Folder", "Folder not found.")
            return False
        self._load_images(normalized, show_warning=show_warning)
        if update_history:
            self._push_recent_folder(normalized)
        return True

    def _push_recent_folder(self, folder_path: Path):
        path_str = str(folder_path)
        if path_str in self._recent_folders:
            self._recent_folders.remove(path_str)
        self._recent_folders.insert(0, path_str)
        if len(self._recent_folders) > RECENT_FOLDERS_LIMIT:
            self._recent_folders = self._recent_folders[:RECENT_FOLDERS_LIMIT]
        save_recent_folders(self._recent_folders)

    def _remove_recent_folder(self, path_str: str):
        if path_str in self._recent_folders:
            self._recent_folders.remove(path_str)
            save_recent_folders(self._recent_folders)

    def _open_recent_folder(self, path_str: str):
        folder_path = self._normalize_folder_path(Path(path_str))
        if not folder_path.exists() or not folder_path.is_dir():
            self._remove_recent_folder(path_str)
            QMessageBox.warning(self, "Invalid Folder", "Folder not found.")
            return
        self._open_folder(folder_path, show_warning=True, update_history=True)

    def _apply_scroll_settings(self):
        self.scroll_area.set_scroll_params(
            self._scroll_settings["wheel_speed"],
            self._scroll_settings["wheel_inertia"],
            self._scroll_settings["friction"],
            self._scroll_settings["drag_sensitivity"],
        )

    def _open_scroll_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        self._settings_dialog = ScrollSettingsDialog(
            self,
            self._scroll_settings,
            on_change=self._on_settings_change,
            on_save=self._on_settings_save,
        )
        self._settings_dialog.finished.connect(self._on_settings_closed)
        self._settings_dialog.show()

    def _on_settings_change(self, settings):
        self._scroll_settings = settings
        self._apply_scroll_settings()

    def _on_settings_save(self, settings):
        self._scroll_settings = settings
        self._apply_scroll_settings()
        save_scroll_settings(self._scroll_settings)

    def _on_settings_closed(self, _result):
        self._settings_dialog = None

    def _apply_preview_position(self):
        self._main_layout.removeWidget(self.preview_panel)
        self._main_layout.removeWidget(self.scroll_area)
        if self._preview_on_left:
            self._main_layout.addWidget(self.preview_panel)
            self._main_layout.addWidget(self.scroll_area)
        else:
            self._main_layout.addWidget(self.scroll_area)
            self._main_layout.addWidget(self.preview_panel)

    def _set_preview_on_left(self, value: bool):
        if self._preview_on_left == value:
            return
        self._preview_on_left = value
        self._apply_preview_position()
        self.preview_panel._update_layout()

    def _reload_current_folder(self):
        if self._current_folder is None:
            return
        bar = self.scroll_area.verticalScrollBar()
        ratio = 0.0
        if bar.maximum() > 0:
            ratio = bar.value() / bar.maximum()
        self._load_images(self._current_folder, show_warning=True, keep_scroll_ratio=ratio)

    def _show_context_menu(self, global_pos):
        menu = QMenu(self)
        open_action = menu.addAction(MENU_OPEN_LABEL)
        reload_action = menu.addAction(MENU_RELOAD_FOLDER_LABEL)
        recent_menu = menu.addMenu(MENU_RECENT_FOLDERS_LABEL)
        settings_action = menu.addAction(MENU_SCROLL_SETTINGS_LABEL)
        left_action = menu.addAction(MENU_PREVIEW_LEFT_LABEL)
        right_action = menu.addAction(MENU_PREVIEW_RIGHT_LABEL)
        inertial_label = (
            MENU_INERTIAL_OFF_LABEL
            if self.scroll_area.inertial_enabled()
            else MENU_INERTIAL_ON_LABEL
        )
        inertial_action = menu.addAction(inertial_label)
        recent_actions = {}
        for path_str in self._recent_folders:
            path_obj = Path(path_str)
            label = path_obj.name or path_str
            action = recent_menu.addAction(label)
            action.setToolTip(path_str)
            recent_actions[action] = path_str
        if not recent_actions:
            empty_action = recent_menu.addAction(MENU_RECENT_EMPTY_LABEL)
            empty_action.setEnabled(False)
        reload_action.setEnabled(self._current_folder is not None)
        left_action.setEnabled(not self._preview_on_left)
        right_action.setEnabled(self._preview_on_left)
        chosen = menu.exec(global_pos)
        if chosen == open_action:
            self._open_folder_dialog()
        elif chosen == reload_action:
            self._reload_current_folder()
        elif chosen in recent_actions:
            self._open_recent_folder(recent_actions[chosen])
        elif chosen == settings_action:
            self._open_scroll_settings()
        elif chosen == left_action:
            self._set_preview_on_left(True)
        elif chosen == right_action:
            self._set_preview_on_left(False)
        elif chosen == inertial_action:
            self.scroll_area.set_inertial_enabled(
                not self.scroll_area.inertial_enabled()
            )

    def contextMenuEvent(self, event):
        self._show_context_menu(event.globalPos())
        event.accept()

    def _calc_thumbnail_width(self):
        if self._max_ratio <= 0:
            return None
        window_h = self.preview_panel.scroll.viewport().height()
        if window_h <= 0:
            window_h = self.centralWidget().height()
        if window_h <= 0:
            window_h = self.height()
        if window_h <= 0:
            return None
        effective_h = max(1, window_h - (PREVIEW_MARGIN * 2))
        width = int(effective_h / self._max_ratio)
        return max(1, width)

    def _schedule_preview_refresh(self):
        if not self._pages or self._max_ratio <= 0:
            return
        self._preview_refresh_timer.start(0)

    def _refresh_previews(self):
        if not self._pages or self._max_ratio <= 0:
            return
        thumb_w = self._calc_thumbnail_width()
        if thumb_w is None:
            return
        if self._current_thumb_w == thumb_w and self.preview_panel._widgets:
            return
        self._current_thumb_w = thumb_w
        self.preview_panel.clear()
        for idx, (img_path, _label, orig_w, orig_h) in enumerate(self._pages):
            thumb = self._create_thumbnail(img_path, thumb_w, orig_w, orig_h)
            if thumb is None:
                continue
            page_label = self.preview_panel.add_preview(ImageQt(thumb))
            page_label.set_left_click(
                lambda evt, i=idx, lbl=page_label: self._jump_to_page(i, evt, lbl)
            )
        self.preview_panel._update_layout()

    def _create_thumbnail(self, path, thumb_w, orig_w, orig_h):
        if not orig_w or not orig_h:
            return None
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA")
                thumb_h = max(1, int(thumb_w * orig_h / orig_w))
                return im.resize((thumb_w, thumb_h), Image.LANCZOS)
        except Exception as exc:
            print(f"Failed to create preview {path}: {exc}", file=sys.stderr)
            return None

    def _jump_to_page(self, page_index, event, label):
        if page_index < 0 or page_index >= len(self._pages):
            return
        pix = label.pixmap()
        if pix is None or pix.height() == 0:
            return
        ratio = max(0.0, min(1.0, event.position().y() / pix.height()))
        _path, start_label, orig_w, orig_h = self._pages[page_index]
        if not orig_w or not orig_h:
            return
        self.scroll_area.stop_inertia()
        viewport_w = self.scroll_area.viewport().width()
        scale = viewport_w / orig_w
        offset = int(orig_h * scale * ratio)
        y_start = start_label.mapTo(self.viewer, QPoint(0, 0)).y()
        self.scroll_area.verticalScrollBar().setValue(y_start + offset)


def main():
    if len(sys.argv) > 2:
        print("Usage: python main.py [folder]")
        return 1

    folder = None
    if len(sys.argv) == 2:
        folder = Path(sys.argv[1]).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            print(f"Folder not found: {folder}", file=sys.stderr)
            return 1

    app = QApplication(sys.argv)
    win = MainWindow(folder)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
