#!/usr/bin/env python3
"""
yt_settings_gui7.5.py

A settings-only (pre-)editor for yt_settings7.json, built on PySide6.

The JSON file is the preset; this window is the editor for it. Nothing
here touches a URL, downloads anything, or shows progress. The one
deliberate peek at "during" is the Preview button, which runs the
engine's own process_one_url() with DEBUG_DRY_RUN forced on for a
throwaway copy of the settings dict - the person's saved value on disk
is never touched.

Requires: yt_settings7.json and yt_schema7.5.py next to this file, and
yt_video_downloader7.5.py next to it (for the preview feature only).

Install:  pip install PySide6
Run:      python yt_settings_gui7.5.py
"""

import re
import subprocess
import sys
import contextlib
import io
import importlib.util
import threading
from pathlib import Path

import json

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QPointF, QRectF, QRect, QPoint, QSize
from PySide6.QtGui import (
    QFont, QFontMetrics, QPainter, QColor, QPen, QBrush,
    QKeySequence, QAction,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLayout,
    QLabel, QLineEdit, QComboBox, QSpinBox, QCheckBox, QRadioButton,
    QPushButton, QToolButton, QScrollArea, QFrame, QFileDialog,
    QMessageBox, QSizePolicy, QTextEdit, QDialog, QButtonGroup,
    QAbstractSpinBox, QMenu,
)


SCRIPT_DIR = Path(__file__).resolve().parent


def _import_sibling(fname):
    """
    Load a sibling module from this file's own folder, by path. A plain
    `import` can never load a file whose version position holds a dot
    (yt_schema7.5.py - Python reads that dot as a package separator), so
    the loader goes through importlib's file-location machinery instead,
    under the module name the filename spells. Each call re-executes the
    file, so the Preview button picks up engine edits without restarting
    this window. This small helper is deliberately duplicated in the
    engine, the GUI and the wiring test: it has to run before the schema
    exists to be imported from.
    """
    p = Path(__file__).parent / fname
    if not p.is_file():
        raise ImportError(f"{fname} not found next to {Path(__file__).name} "
                          f"- it must sit in the same folder.")
    spec = importlib.util.spec_from_file_location(fname[:-3], p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fname[:-3]] = mod
    spec.loader.exec_module(mod)
    return mod


S = _import_sibling("yt_schema7.5.py")   # the one schema (see the derived-registries block below)
SETTINGS_FILE = SCRIPT_DIR / "yt_settings7.json"

FULL_COMMAND_RE = re.compile(
    r'Full command:\s*\n\s*={3,}\s*\n(.+?)\n\s*={3,}',
    re.DOTALL,
)
PREVIEW_URL = "https://www.youtube.com/watch?v=l18A5BOTlzE&list=PL5-WT4DlkvgrAZsd69LPZOARdHLuE9Xo3"


URL_RE = re.compile(r'^https?://\S+$')

def _blend(hex_a: str, hex_b: str, t: float) -> str:
    """Linear blend between two '#rrggbb' colors. t=0 -> hex_a, t=1 -> hex_b.
    Used to derive dimmed variants of the theme colors, since QSS itself
    has no color-math operators - the derivation has to happen in Python,
    where the stylesheet is being assembled."""
    a = hex_a.lstrip("#")
    b = hex_b.lstrip("#")
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = round(ar + (br - ar) * t)
    g = round(ag + (bg - ag) * t)
    bl = round(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"
    
# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
TH = {
    "bg":          "#1e1e1e",
    "panel":       "#252526",
    "field":       "#2d2d2d",
    "field_ro":    "#252526",
    "border":      "#3c3c3c",
    "border_hi":   "#505050",
    "fg":          "#d4d4d4",
    "fg_bright":   "#ffffff",
    "fg_dim":      "#8c8c8c",
    "fg_disabled": "#5a5a5a",
    # One accent identity, used everywhere something is "on/selected/active":
    # checkbox and radio fills, chip fills, focus borders, and button-press
    # feedback. Previously these were two unrelated hues - grey for
    # checkboxes/chips, blue for button-press and text selection - which
    # is a plain Nielsen-heuristic-4 (consistency) miss: the same concept
    # (this is the active/selected thing) had two different visual
    # identities depending which widget you were looking at. This is a
    # single desaturated slate blue instead of pure grey, per the dark-
    # theme guidance to desaturate accents rather than remove color
    # entirely - a fully monochrome UI reads as flat/lifeless, and losing
    # color removes a legitimate "this is interactive" cue.
    # Contrast (WCAG): accent-on-bg 3.34:1 (passes the 3:1 minimum for
    # non-text UI components); white-on-accent 4.99:1 (passes the 4.5:1
    # minimum for normal text, e.g. chip labels).
    "accent":      "#527296",
    "accent_hi":   "#6f8fb3",   # hover on checked states - lighter, same hue
    "select":      "#264f78",   # already this family; left untouched (8.5:1 white-on-select)
    "btn_bg":      "#3a3a3a",
    "btn_hover":   "#4a4a4a",
    "btn_press":   "#40556c",   # accent blended 35% toward bg - a "pressed/sunken"
                                # version of the SAME hue, not a different color family
    "chip_off_bg": "#333333",
    "chip_off_fg": "#b8b8b8",
    "chip_on_bg":  "#527296",   # same accent family as everything else "on"
    "chip_on_fg":  "#ffffff",
    "track":       "#505050",
    "track_off":   "#2a2a2a",
    "tick":        "#909090",
    "tick_off":    "#404040",
    "thumb_off":   "#606060",
    "warn":        "#e8a05a",
    "ok":          "#7ec87e",
}

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


def _qss(th):
    
    # Derived colors: the *same* hues as the active states, pulled toward
    # the background. Changing chip_on_bg / accent_hi above therefore
    # automatically changes their disabled variants too.
    chip_on_bg_disabled = _blend(th["chip_on_bg"], th["bg"], 0.45)
    chip_on_border_dis = _blend(th["accent_hi"],  th["bg"], 0.55)
    check_on_bg_disabled = _blend(th["accent"],   th["bg"], 0.45)
    check_on_border_dis  = _blend(th["accent_hi"], th["bg"], 0.55)
    """
    Global stylesheet. The checkbox and radio indicators get explicit
    border + background so an unchecked box is clearly visible against
    the dark panel - the default Fusion rendering makes the unchecked
    indicator nearly invisible here. Checked state is a solid accent
    fill; the color change alone is unambiguous, no checkmark glyph
    needed (QSS ::indicator styling suppresses the native one anyway).
    """
    return f"""
    QWidget {{
        background-color: {th['bg']};
        color: {th['fg']};
        font-family: '{FONT_FAMILY}';
        font-size: 9pt;
    }}
    QLabel {{ background-color: transparent; }}

    QPushButton {{
        background-color: {th['btn_bg']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        padding: 4px 10px;
        border-radius: 2px;
    }}
    QPushButton:hover    {{ background-color: {th['btn_hover']}; color: {th['fg_bright']}; }}
    QPushButton:pressed  {{ background-color: {th['btn_press']}; }}
    QPushButton:disabled {{ background-color: {th['panel']}; color: {th['fg_disabled']}; }}
    QPushButton:checked  {{
        background-color: {th['chip_on_bg']};
        color: {th['chip_on_fg']};
        border: 1px solid {th['accent_hi']};
    }}
    QPushButton:checked:hover {{ background-color: {th['accent_hi']}; }}
    QPushButton:checked:disabled {{
        background-color: {chip_on_bg_disabled};
        color: {th['fg_disabled']};
        border: 1px solid {chip_on_border_dis};
    }}
    QToolButton {{
        background-color: transparent;
        color: {th['fg']};
        border: none;
        padding: 2px 4px;
    }}
    QToolButton:hover {{ color: {th['fg_bright']}; }}
    QToolButton::menu-indicator {{ image: none; }}

    QLineEdit {{
        background-color: {th['field']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        padding: 3px 4px;
        border-radius: 2px;
        selection-background-color: {th['select']};
        selection-color: {th['fg_bright']};
    }}
    QLineEdit:focus    {{ border: 1px solid {th['accent']}; }}
    QLineEdit:disabled {{ background-color: {th['field_ro']}; color: {th['fg_disabled']}; }}

    QComboBox {{
        background-color: {th['field']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        padding: 3px 4px;
        border-radius: 2px;
    }}
    QComboBox:focus    {{ border: 1px solid {th['accent']}; }}
    QComboBox:disabled {{ background-color: {th['field_ro']}; color: {th['fg_disabled']}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background-color: {th['field']};
        color: {th['fg']};
        selection-background-color: {th['accent']};
        selection-color: {th['fg_bright']};
        border: 1px solid {th['border']};
    }}

    QSpinBox {{
        background-color: {th['field']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        padding: 3px 4px;
        border-radius: 2px;
        selection-background-color: {th['select']};
        selection-color: {th['fg_bright']};
    }}
    QSpinBox:focus    {{ border: 1px solid {th['accent']}; }}
    QSpinBox:disabled {{ background-color: {th['field_ro']}; color: {th['fg_disabled']}; }}

    /* -------- Checkboxes: readable when off -------- */
    QCheckBox, QRadioButton {{
        background-color: transparent;
        color: {th['fg']};
        spacing: 8px;
        padding: 2px 0;
    }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {th['fg_disabled']}; }}

    QCheckBox::indicator, QRadioButton::indicator {{
        width: 15px;
        height: 15px;
        border: 2px solid #6a6a6a;
        background-color: #1a1a1a;
    }}
    QCheckBox::indicator    {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border: 2px solid #909090;
        background-color: #262626;
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background-color: {th['accent']};
        border: 2px solid {th['accent_hi']};
    }}
    QCheckBox::indicator:checked:hover, QRadioButton::indicator:checked:hover {{
        background-color: {th['accent_hi']};
        border: 2px solid #a0a0a0;
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        border: 2px solid #3a3a3a;
        background-color: #1e1e1e;
    }}
    QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
        background-color: {check_on_bg_disabled};
        border: 2px solid {check_on_border_dis};
    }}

    QScrollArea {{ border: none; background-color: {th['bg']}; }}
    QScrollBar:vertical {{
        background-color: {th['panel']};
        width: 12px; border: none; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background-color: {th['btn_bg']};
        min-height: 20px; border-radius: 2px;
    }}
    QScrollBar::handle:vertical:hover {{ background-color: {th['btn_hover']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{
        background-color: {th['panel']};
        height: 12px; border: none; margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background-color: {th['btn_bg']};
        min-width: 20px; border-radius: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{ background-color: {th['btn_hover']}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

    QTextEdit {{
        background-color: {th['field']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        selection-background-color: {th['select']};
        selection-color: {th['fg_bright']};
    }}

    QMenu {{
        background-color: {th['field']};
        color: {th['fg']};
        border: 1px solid {th['border']};
        padding: 4px;
    }}
    QMenu::item {{ padding: 4px 18px 4px 12px; }}
    QMenu::item:selected {{
        background-color: {th['accent']};
        color: {th['fg_bright']};
    }}
    """


# ---------------------------------------------------------------------------
# Field status model
# ---------------------------------------------------------------------------
#
# Every field carries exactly one of:
#
#   NORMAL    - visible, interactive
#   HIDDEN    - not rendered. A fact about the current mode. Raw value
#               persists; change the mode back and it returns untouched.
#   DISABLED  - rendered but greyed. Raw value still echoed in the widget.
#
# A DISABLED field carries a reason + reason kind. The kind does not
# change the rendering or the cascade - it only tells the UI what the
# reason string *means*, so it can be phrased correctly:
#
#   REASON_SETTINGS - an ancestor toggle in this file is off. Following
#                     the chain up always terminates at a switch the user
#                     can flip. The reason names that switch. This is the
#                     only reason kind the pre-GUI produces.
#
#   REASON_EXTERNAL - a fact outside the settings file. Currently unused
#                     in the pre-GUI; reserved for a future during-GUI,
#                     where a scanned video lacking chapters or subtitles
#                     would disable the corresponding fields. Nothing
#                     about the state model changes when that arrives -
#                     same grey, same cascade, one more reason kind.
#
# Both reasons cascade identically: a disabled field makes its children
# disabled, and each child reports its own nearest cause.

ST_NORMAL   = "normal"
ST_HIDDEN   = "hidden"
ST_DISABLED = "disabled"

REASON_NONE     = ""
REASON_SETTINGS = "settings"
REASON_EXTERNAL = "external"

# ---------------------------------------------------------------------------
# Everything below is DERIVED from yt_schema7.5.py - the one schema. Nothing
# here is edited by hand any more: add/retire a setting, change a hint, a
# default, which mode hides it, its widget or its chips -> edit ONE F(...)
# line in the schema and this window follows.
#   SCHEMA          key -> (widget kind, options, parent switch)
#   HIDDEN_IN_MODE  key -> DOWNLOAD_TYPE values that hide it (raw persists)
# ---------------------------------------------------------------------------
HIDDEN_IN_MODE = S.HIDDEN_IN_MODE
SCHEMA = S.GUI_SCHEMA
LANG_CHIPS = S.LANG_CHIPS
MORE_LANGS = S.MORE_LANGS
CHIP_KEYS = S.CHIP_KEYS
LANG_PICKER_KEYS = S.LANG_PICKER_KEYS
ADVANCED_OVERRIDE_KEYS = S.ADVANCED_OVERRIDE_KEYS
MODE_HINTS = S.MODE_HINTS
TEXT_WIDTHS = S.TEXT_WIDTHS
BROWSE_KEYS = S.BROWSE_KEYS
HINTS = S.HINTS
ARRAY_INT_KEYS = S.ARRAY_INT_KEYS
ARRAY_STR_KEYS = S.ARRAY_STR_KEYS
INT_KEYS = S.INT_KEYS


CHIP_ORDER_HINT = "Chips add or remove entries. Edit the field directly to reorder."

ADVANCED_OVERRIDE_TITLE = "Advanced: per-purpose overrides (embed vs. keep different languages)"


DEFAULT_TEXT_WIDTH = 30

DYN_MIN_PX = 90
DYN_MAX_PX = 320
DYN_PAD_PX = 4

RULER_WIDTH_PX = 600  # was 460 - at 460 the 9 AUDIO_BITRATE_CAP/AUDIO_QUALITY
                      # ticks were only 54px apart while each label's own
                      # draw rect was 52px wide, leaving ~2px between
                      # "112k"/"128k"/"160k" - close enough that anti-
                      # aliased glyphs visibly ran together. 600px gives
                      # ~73px per tick.

# Fixed pixel width of the key-label column. Constant across modes so
# switching audio <-> video doesn't shift every control left or right
# when a long key disappears or reappears.
KEY_COLUMN_WIDTH_PX = 170  # was 200, then 220 - after the QUALITY_FALLBACK /
                           # SUB_ASK_MISSING / AUDIO_CODEC renames, the
                           # longest real key is PLAYLIST_URL_HANDLING
                           # (~152px in Consolas 9pt); this is that plus a
                           # small safety margin for font-metric drift on
                           # a real Windows/Consolas render vs. here.


AUTOSUB_WARNING = (
    "\u26a0 Wildcard patterns aren't supported any more (this warning fires "
    "so you know what will happen): the engine reduces an entry like 'en.*' "
    "to the plain language 'en' with a run-time warning. Patterns once "
    "matched dozens of auto-translated tracks and got the whole run "
    "rate-limited (HTTP 429) before the video downloaded at all, which is "
    "why they're gone. A plain code + ENABLE_AUTO_SUBS is the replacement: "
    "it resolves to the real, non-translated transcript for this video, "
    "including the '-orig' auto-original. (Known exception, not this "
    "script's doing: on a video that ALSO uses YouTube's auto-dubbed-audio "
    "feature, even '-orig' can become ambiguous \u2014 current upstream "
    "limitation, unresolved as of writing.)"
)

_UNDO_LIMIT = 50

BANNER_TITLE = "YouTube Downloader Settings (v7.5)"


# ---------------------------------------------------------------------------
# Parsing / writing
#
# The file on disk is plain, properly-typed JSON - real true/false, real
# arrays, real numbers where a number is actually all a value can ever be.
# No comments live in it; every hint shown in this editor comes from the
# HINTS dict instead; section/subsection grouping comes from the JSON's
# own nested-object structure.
#
# There is no intermediate text representation anywhere in this file.
# read_settings() returns the parsed dict as-is; _render() walks it
# directly (see _render_node) to build the UI, and every widget is
# initialized from its real native value - a checkbox from a real bool, a
# spinbox from a real int, a chip field's backing QLineEdit from a real
# list (joined for display, since a text field can only ever show text -
# that join is a UI-display concern, not a silent type downgrade, because
# the value handed to everything else in this file, engine included, is
# read back through _read_value() as the native type again, never as the
# display string). write_settings() edits only the keys that actually
# changed, compared as real values, not string reserializations.
# ---------------------------------------------------------------------------


def _find_json_container(node: dict, key: str):
    """Depth-first search for `key` as a direct leaf of some nested object,
    returning that object so the caller can assign into it. Mirrors the
    engine's own assumption (see _flatten_json_tables) that every setting
    name is unique across the whole file."""
    for k, v in node.items():
        if k == key and not isinstance(v, dict):
            return node
    for k, v in node.items():
        if isinstance(v, dict):
            found = _find_json_container(v, key)
            if found is not None:
                return found
    return None


def read_settings(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_settings(path: Path, new_values: dict):
    """new_values holds REAL native types already (whatever _value_of()
    returns) - no string round-trip on the way in. A key whose current
    JSON value already equals the new one is left completely alone, so an
    untouched key can never have its type or formatting perturbed."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    missing = []
    for key, value in new_values.items():
        container = _find_json_container(doc, key)
        if container is None:
            missing.append(key)
            continue
        if container[key] == value:
            continue
        container[key] = value
    if missing:
        raise KeyError(
            "These keys don't exist anywhere in the JSON file, so nothing "
            "was written for them: " + ", ".join(sorted(missing)))
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _expand_settings(raw: dict, script_dir: Path) -> dict:
    """Path templating ({ROOT}, {SOME_KEY}) - the same schema-module function
    the engine's load_settings calls, so the two can no longer drift."""
    return S.expand_templates(raw, script_dir)


# ---------------------------------------------------------------------------
# Custom widgets
# ---------------------------------------------------------------------------

class FlowLayout(QLayout):
    """
    Packs its children left-to-right, wrapping to a new line when a widget
    doesn't fit - the standard Qt "flow layout" recipe. This is what makes
    the block/card layout possible: a compact field (a checkbox, a short
    combo) sits next to its neighbors; a wide one (chips, the bitrate
    ruler, a radio row with a long explanation) naturally gets a line to
    itself because nothing else fits beside it at its own width. No row/
    column bookkeeping anywhere upstream - callers just addWidget() a card
    in file order and this figures out the wrapping at whatever width the
    window currently is.

    Hidden widgets (setVisible(False), the whole gating mechanism this
    editor runs on) are skipped entirely - both their space and their
    geometry - exactly like Qt's built-in layouts already do, which a
    naive custom QLayout does NOT do for free.
    """

    def __init__(self, parent=None, margin=0, h_spacing=12, v_spacing=10):
        super().__init__(parent)
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._items = []
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self):
        return self._h_spacing

    def verticalSpacing(self):
        return self._v_spacing

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisible():
                continue
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        effective = QRect(rect.x() + m.left(), rect.y() + m.top(),
                          rect.width() - m.left() - m.right(),
                          rect.height() - m.top() - m.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0

        for item in self._items:
            w = item.widget()
            if w is not None and not w.isVisible():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            if next_x - self._h_spacing > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class RulerSlider(QWidget):
    """A slider drawn on a QWidget: track, per-value ticks, labels beneath,
    draggable indicator snapping to the nearest tick."""

    valueChanged = Signal(str)

    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.values = list(values)
        self.index = 0
        self._enabled = True
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        # QWidget's default sizeHint() is invalid (-1,-1) for a bare
        # painted widget like this one with no child layout - left
        # unset, that invalid hint was propagating up through the
        # container's QVBoxLayout and undercutting setMinimumHeight()
        # by a few px, which is what was clipping the bottom of the
        # tick labels (the actual, visible cause of "smooshed").
        return QSize(RULER_WIDTH_PX, 46)

    def minimumSizeHint(self):
        return QSize(120, 46)

    def current_value(self):
        return self.values[self.index] if 0 <= self.index < len(self.values) else ""

    def set_value(self, value):
        try:
            self.index = self.values.index(value)
        except ValueError:
            return
        self.update()

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self._enabled = enabled
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.update()

    def _x_for(self, i):
        margin = 14.0
        n = len(self.values)
        if n <= 1:
            return self.width() / 2.0
        span = max(1.0, self.width() - 2 * margin)
        return margin + i * span / (n - 1)

    def _index_for(self, x):
        margin = 14.0
        n = len(self.values)
        if n <= 1:
            return 0
        span = max(1.0, self.width() - 2 * margin)
        rel = (x - margin) / span
        i = round(rel * (n - 1))
        return max(0, min(n - 1, i))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track_y = 12.0
        label_y = self.height() - 12.0

        p.setPen(QPen(QColor(TH["track"] if self._enabled else TH["track_off"]), 2))
        p.drawLine(QPointF(self._x_for(0), track_y),
                   QPointF(self._x_for(len(self.values) - 1), track_y))

        p.setFont(QFont(FONT_FAMILY, 8))
        for i, val in enumerate(self.values):
            x = self._x_for(i)
            p.setPen(QPen(QColor(TH["tick"] if self._enabled else TH["tick_off"])))
            p.drawLine(QPointF(x, track_y - 3), QPointF(x, track_y + 3))
            p.setPen(QPen(QColor(TH["fg_dim"] if self._enabled else TH["fg_disabled"])))
            p.drawText(QRectF(x - 30, label_y - 9, 60, 16),
                       Qt.AlignCenter, val)

        tx = self._x_for(self.index)
        color = QColor(TH["accent"] if self._enabled else TH["thumb_off"])
        p.setBrush(QBrush(color))
        p.setPen(Qt.NoPen)
        r = 6.0
        p.drawEllipse(QPointF(tx, track_y), r, r)

    def mousePressEvent(self, event):
        if not self._enabled:
            return
        new = self._index_for(event.position().x())
        if new != self.index:
            self.index = new
            self.update()
            self.valueChanged.emit(self.values[self.index])

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.mousePressEvent(event)


class CollapsibleSection(QWidget):
    """
    Progressive-disclosure wrapper: a clickable header that shows/hides a
    content area. Used for the advanced subtitle overrides so the common
    case (SUB_LANGUAGES alone) isn't cluttered by two rarely-touched fields.
    The content area exposes its own grid so setting rows can be added to
    it with the same layout rules the main grid uses.
    """

    def __init__(self, title: str, parent=None, start_collapsed: bool = True):
        super().__init__(parent)
        self._title = title
        self._collapsed = start_collapsed

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 6)
        v.setSpacing(0)

        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(not start_collapsed)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                background: transparent;
                border: none;
                color: {TH['fg_dim']};
                font-weight: 600;
                font-family: '{FONT_FAMILY}';
                font-size: 9pt;
                padding: 4px 0;
            }}
            QPushButton:hover {{ color: {TH['fg_bright']}; }}
            QPushButton:checked {{ color: {TH['fg']}; }}
        """)
        self._refresh_header()
        v.addWidget(self.header)

        self.content = QWidget()
        self.content_flow = FlowLayout(self.content, margin=0, h_spacing=12, v_spacing=10)
        self.content_flow.setContentsMargins(16, 4, 0, 0)
        self.content.setVisible(not start_collapsed)
        v.addWidget(self.content)

        self.header.toggled.connect(self._on_toggle)

    def _refresh_header(self):
        arrow = "\u25be" if not self._collapsed else "\u25b8"
        self.header.setText(f"{arrow}  {self._title}")

    def _on_toggle(self, checked):
        self._collapsed = not checked
        self.content.setVisible(checked)
        self._refresh_header()
        self._relayout()

    def _relayout(self):
        """A FlowLayout's height depends on the width it is given, and Qt
        doesn't re-ask it when the content area is shown - the area stayed
        at just its top margin (a few px), so the cards inside were laid
        out but clipped away and only the header text was visible. Pin the
        content's height to what the flow needs at the current width, then
        make the parents pick that up."""
        if self._collapsed:
            self.content.setMinimumHeight(0)
            return
        w = self.content.width() or self.width() or 600
        self.content_flow.invalidate()
        self.content.setMinimumHeight(self.content_flow.heightForWidth(w))
        self.content.updateGeometry()
        self.updateGeometry()
        self.content_flow.activate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Width changes alter how many cards wrap per line -> height changes.
        if not self._collapsed:
            self._relayout()

    def is_collapsed(self):
        return self._collapsed


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class SettingsEditor(QWidget):

    def __init__(self, path: Path):
        super().__init__()

        self.path = path
        self.doc = read_settings(path)
        self.rows = {}
        self.dirty = False
        self._baseline = {}

        self.sections = {}
        self.row_section = {}
        self.row_subsection = {}      # key -> subsection heading it sits under (or None)
        self._subsection_hosts = {}   # heading widget -> (section, subsection)
        self._current_subsection = None
        self._collapsibles = []
        self._current_section = None

        self._search_term = ""

        self._autosub_warning = None
        self._autosub_warning_should_show = False

        # Undo/redo
        self._undo_stack = []
        self._redo_stack = []
        self._last_committed_state = {}
        self._applying_undo = False
        self._suppress_on_change = False

        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(350)
        self._undo_timer.timeout.connect(self._commit_undo_snapshot)

        self.setWindowTitle(f"Settings \u2014 {path.name}")
        self.resize(1050, 800)
        self.setMinimumSize(820, 500)
        self.setStyleSheet(_qss(TH))

        self._build_ui()
        self._render()
        self._baseline = {k: self._value_of(k) for k in self.rows}
        self._refresh_states()
        self._sync_auxiliary()
        self._apply_visibility()

        self._last_committed_state = {k: self._value_of(k) for k in self.rows}

        for seq, slot in (("Ctrl+S", self._save), ("Ctrl+F", self._focus_search)):
            a = QAction(self)
            a.setShortcut(QKeySequence(seq))
            a.triggered.connect(slot)
            self.addAction(a)

        self._start_update_check()

        QApplication.instance().installEventFilter(self)

    # ---- chrome ----------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.inner = QWidget()
        self.inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.grid = QVBoxLayout(self.inner)
        self.grid.setContentsMargins(14, 12, 20, 20)
        self.grid.setSpacing(4)
        self.grid.setAlignment(Qt.AlignTop)

        scroll.setWidget(self.inner)
        root.addWidget(scroll, 1)

        self.scroll = scroll

        root.addWidget(self._build_status())

        self._row = 0
        self._advanced_section = None

    def _build_toolbar(self):
        host = QWidget()
        bar = QHBoxLayout(host)
        bar.setContentsMargins(14, 12, 14, 6)
        bar.setSpacing(6)

        for label, slot in (("Save", self._save),
                            ("Reload", self._reload),
                            ("Notepad++", self._open_raw),
                            ("Preview command", self._preview_command)):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            bar.addWidget(btn)

        bar.addSpacing(12)

        self._undo_btn = QPushButton("\u2190")
        self._undo_btn.setFixedWidth(34)
        self._undo_btn.clicked.connect(self._undo)
        self._undo_btn.setEnabled(False)
        self._undo_btn.setToolTip("Undo (Ctrl+Z)")
        bar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("\u2192")
        self._redo_btn.setFixedWidth(34)
        self._redo_btn.clicked.connect(self._redo)
        self._redo_btn.setEnabled(False)
        self._redo_btn.setToolTip("Redo (Ctrl+Y)")
        bar.addWidget(self._redo_btn)

        bar.addStretch(1)

        # Passive "update available" notice - hidden unless the startup
        # check (see _start_update_check) finds installed != latest.
        self._update_lbl = QLabel("")
        self._update_lbl.setTextFormat(Qt.RichText)
        self._update_lbl.setOpenExternalLinks(True)
        self._update_lbl.setStyleSheet(f"color:{TH['warn']};background:transparent;")
        self._update_lbl.setVisible(False)
        bar.addWidget(self._update_lbl)

        bar.addSpacing(12)

        lbl = QLabel("Search:")
        lbl.setStyleSheet(f"color:{TH['fg_dim']};")
        bar.addWidget(lbl)

        self.search_edit = QLineEdit()
        self.search_edit.setFixedWidth(200)
        self.search_edit.setPlaceholderText("key, label, or value")
        self.search_edit.textChanged.connect(self._on_search_change)
        bar.addWidget(self.search_edit)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_search)
        bar.addWidget(clear_btn)

        return host

    def _build_status(self):
        host = QWidget()
        outer = QHBoxLayout(host)
        outer.setContentsMargins(14, 0, 14, 6)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{TH['fg_dim']};")
        outer.addWidget(self.status)

        outer.addStretch(1)

        path_lbl = QLabel(str(self.path))
        path_lbl.setStyleSheet(f"color:{TH['fg_disabled']};")
        outer.addWidget(path_lbl)

        return host

    # ---- widget construction helpers -------------------------------------

    def _make_label(self, text, *, family=FONT_FAMILY, size=9, bold=False,
                    italic=False, color=None, wrap=False):
        lbl = QLabel(text)
        f = QFont(family, size)
        f.setBold(bold)
        f.setItalic(italic)
        lbl.setFont(f)
        if color:
            lbl.setStyleSheet(f"color:{color};background:transparent;")
        else:
            lbl.setStyleSheet("background:transparent;")
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if wrap:
            lbl.setWordWrap(True)
        return lbl

    def _make_separator(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet(f"color:{TH['border']};background:{TH['border']};")
        line.setFixedHeight(1)
        return line

    # ---- rendering -------------------------------------------------------

    def _render(self):
        """Walks self.doc - the parsed JSON, nothing else - directly: a
        nested object is a subsection heading, a leaf is a field card.
        There is no text form of this anywhere; _render_setting gets each
        leaf's real native value straight from the dict."""
        self._current_flow = None
        self._all_flows = []
        self._advanced_section = None
        self._current_section = None

        self._add_banner_title(BANNER_TITLE)
        for section_name, section_body in self.doc.items():
            self._advanced_section = None  # a new top-level section closes any open one
            self._current_subsection = None
            self._add_section(section_name)
            self._render_node(section_body)

    def _render_node(self, node: dict):
        for key, value in node.items():
            if isinstance(value, dict):
                self._add_subsection(key)
                self._render_node(value)
            elif key in ADVANCED_OVERRIDE_KEYS:
                self._ensure_advanced_section()
                self._render_setting(key, value, self._advanced_section.content_flow)
            else:
                self._render_setting(key, value, self._current_flow)

    def _new_flow_container(self):
        """A fresh card area, appended to the main column right now and
        made the target for every field card from here on - until the next
        section/subsection banner swaps in a new one. This is the whole
        block/card layout: cards addWidget() into a FlowLayout instead of
        landing at hand-picked (row, col) coordinates in one shared grid,
        so short fields pack side by side and wide ones simply don't fit
        next to anything and get a line to themselves."""
        box = QWidget()
        flow = FlowLayout(box, margin=0, h_spacing=12, v_spacing=10)
        self.grid.addWidget(box)
        self._current_flow = flow
        self._all_flows.append(flow)
        return box

    def _activate_flows(self):
        """Qt normally recomputes a QLayout's geometry automatically
        whenever its widget is resized or a child's visibility changes -
        but that automatic invalidation isn't reliably firing for these
        dynamically-populated FlowLayouts (confirmed: the wrapping math
        itself is correct, calling .activate() explicitly is what's
        missing). So this is called once after the initial build and again
        after every gating/search visibility pass, rather than trusting
        Qt to notice on its own."""
        for flow in self._all_flows:
            flow.invalidate()
            flow.activate()
        for cs in self._collapsibles:
            cs.content_flow.invalidate()
            cs.content_flow.activate()
            cs._relayout()   # cards may have been hidden/shown -> height changed

    def _ensure_advanced_section(self):
        if self._advanced_section is not None:
            return
        self._advanced_section = CollapsibleSection(
            ADVANCED_OVERRIDE_TITLE, start_collapsed=True)
        self.grid.addWidget(self._advanced_section)
        self._all_flows.append(self._advanced_section.content_flow)
        self._collapsibles.append(self._advanced_section)
        if self._current_section:
            self.sections[self._current_section]["widgets"].append(self._advanced_section)

    def _add_banner_title(self, title):
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(0, 4, 0, 12)
        h.setSpacing(12)

        lbl = self._make_label(title, size=10, bold=True, color=TH["fg_bright"])
        h.addWidget(lbl)
        h.addWidget(self._make_separator(), 1)

        self.grid.addWidget(host)

    def _add_section(self, text):
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(0, 26, 0, 6)
        h.setSpacing(6)

        arrow = ClickableLabel("\u25be")
        arrow.setFixedWidth(16)
        arrow.setCursor(Qt.PointingHandCursor)
        arrow.setStyleSheet(f"color:{TH['fg_dim']};background:transparent;")
        arrow.setFont(QFont(FONT_FAMILY, 10, QFont.Bold))
        arrow.clicked.connect(lambda s=text: self._toggle_section(s))
        h.addWidget(arrow)

        title = ClickableLabel(text)
        title.setCursor(Qt.PointingHandCursor)
        title.setFont(QFont(FONT_FAMILY, 10, QFont.Bold))
        title.setStyleSheet(f"color:{TH['fg_bright']};background:transparent;")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title.clicked.connect(lambda s=text: self._toggle_section(s))
        h.addWidget(title)
        h.addWidget(self._make_separator(), 1)

        self.grid.addWidget(host)

        self.sections[text] = {
            "header":     host,
            "toggle":     arrow,
            "title":      title,
            "widgets":    [],
            "collapsed":  False,
        }
        self._current_section = text
        self._new_flow_container()

    def _add_subsection(self, text):
        # A real heading, not a caption: bigger, bold, non-italic, and
        # given its own top margin so it visually separates from the
        # previous field's hint text above it - instead of column-1
        # alignment, which made it collide with that hint paragraph and
        # read as a continuation of it rather than a heading for what
        # follows. The width fix for "too wide" lives in
        # KEY_COLUMN_WIDTH_PX/grid margins now, not in this label's column.
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(0, 14, 0, 4)
        h.setSpacing(0)
        lbl = self._make_label(text, size=11, bold=True, color=TH["fg"])
        h.addWidget(lbl)
        h.addStretch(1)
        self.grid.addWidget(host)
        self._current_subsection = text
        if self._current_section:
            self.sections[self._current_section]["widgets"].append(host)
            self._subsection_hosts[host] = (self._current_section, text)
        self._new_flow_container()

    def _render_setting(self, key, value, target_flow):
        """Builds one setting as a single self-contained card - label,
        control, optional chips, and a one-line hint teaser (full text on
        hover) - and adds it to target_flow. FlowLayout then decides
        per-card whether it packs next to its neighbors or gets a line to
        itself, based on nothing but its own natural width.

        `value` is the REAL native type straight out of the parsed JSON -
        a bool, a list, an int, or a string. Nothing upstream of this
        stringified it first."""
        kind, options, _parent = SCHEMA.get(key, ("text", None, None))

        card = QFrame()
        card.setObjectName("fieldCard")
        card.setStyleSheet(f"""
            QFrame#fieldCard {{
                background: {TH['panel']};
                border: 1px solid {TH['border']};
                border-radius: 6px;
            }}
        """)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(10, 8, 10, 8)
        cv.setSpacing(4)

        key_label = self._make_label(key, family=MONO_FAMILY, size=9,
                                     color=TH["fg_dim"])
        cv.addWidget(key_label)

        primary = None
        targets = []
        chips = {}
        ruler = None
        dyn_container = None
        group = None
        container = None
        entry = None

        if kind == "bool":
            primary = QCheckBox()
            primary.setChecked(bool(value))
            targets = [primary]
            container = primary
            primary.toggled.connect(lambda *_: self._on_change())

        elif kind == "toggles":
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(14)
            group = QButtonGroup(container)
            group.setExclusive(True)
            for opt in options:
                rb = QRadioButton(opt)
                rb.setChecked(value == opt)
                group.addButton(rb)
                h.addWidget(rb)
                targets.append(rb)
            group.buttonToggled.connect(lambda *_: self._on_change())

        elif kind == "ruler":
            container = QWidget()
            container.setFixedWidth(RULER_WIDTH_PX)
            v = QVBoxLayout(container)
            v.setContentsMargins(0, 0, 0, 0)
            ruler = RulerSlider(options)
            ruler.set_value(str(value).strip())
            ruler.valueChanged.connect(lambda *_: self._on_change())
            v.addWidget(ruler)
            targets = [ruler]

        elif kind == "enum":
            primary = QComboBox()
            primary.setEditable(False)
            for o in options:
                primary.addItem(str(o))
            primary.setCurrentText(str(value))
            width = max(len(str(o)) for o in options) + 3
            fm = QFontMetrics(QFont(FONT_FAMILY, 9))
            primary.setFixedWidth(fm.horizontalAdvance("m") * width)
            primary.currentTextChanged.connect(lambda *_: self._on_change())
            targets = [primary]
            container = primary

        elif kind == "spinbox":
            primary = QSpinBox()
            primary.setRange(1 if key == "PLAYLIST_START_INDEX" else 0, 10 ** 9)
            try:
                primary.setValue(int(value))
            except (ValueError, TypeError):
                primary.setValue(0)
            primary.setFixedWidth(80)
            primary.valueChanged.connect(lambda *_: self._on_change())
            targets = [primary]
            container = primary

        else:  # text
            # A list-typed value (any ARRAY_STR_KEYS/ARRAY_INT_KEYS field,
            # not just the ones with a chip strip - SUB_LANGUAGES is array-
            # typed but has no chips) only ever gets *displayed* as comma-
            # joined text here; _read_value() is what turns the text back
            # into a real list for everything downstream, so this join
            # never leaks past this one widget's on-screen text.
            display = ",".join(str(v) for v in value) if isinstance(value, list) else str(value)

            if key in CHIP_KEYS:
                dyn_container = QWidget()
                dyn_container.setFixedHeight(26)
                v = QVBoxLayout(dyn_container)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(0)

                primary = QLineEdit(display)
                primary.setFixedWidth(
                    QFontMetrics(QFont(FONT_FAMILY, 9)).horizontalAdvance("m") * 26)
                primary.textChanged.connect(lambda *_: self._on_change())
                primary.textChanged.connect(lambda *_, k=key: self._autosize_chip(k))
                v.addWidget(primary)

                entry = primary
                targets = [primary]
                container = dyn_container
            elif key in BROWSE_KEYS:
                container = QWidget()
                h = QHBoxLayout(container)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(6)

                primary = QLineEdit(display)
                primary.setFixedWidth(
                    QFontMetrics(QFont(FONT_FAMILY, 9)).horizontalAdvance("m")
                    * TEXT_WIDTHS.get(key, DEFAULT_TEXT_WIDTH)
                )
                primary.textChanged.connect(lambda *_: self._on_change())
                h.addWidget(primary)

                browse = QPushButton("\u2026")
                browse.setFixedWidth(30)
                browse.clicked.connect(lambda *_, k=key: self._browse(k))
                h.addWidget(browse)

                targets = [primary, browse]
                entry = primary
            else:
                primary = QLineEdit(display)
                primary.setFixedWidth(
                    QFontMetrics(QFont(FONT_FAMILY, 9)).horizontalAdvance("m")
                    * TEXT_WIDTHS.get(key, DEFAULT_TEXT_WIDTH)
                )
                primary.textChanged.connect(lambda *_: self._on_change())
                targets = [primary]
                container = primary
                entry = primary

        cv.addWidget(container)

        # ---- chip strip ----
        chip_host = None
        if key in CHIP_KEYS:
            chip_host = QWidget()
            h = QHBoxLayout(chip_host)
            h.setContentsMargins(0, 2, 0, 0)
            h.setSpacing(5)
            for opt in CHIP_KEYS[key]:
                chip = QPushButton(opt)
                chip.setCheckable(True)
                chip.setFont(QFont(FONT_FAMILY, 8))
                chip.setFixedHeight(22)
                chip.clicked.connect(lambda *_, k=key, o=opt: self._toggle_chip(k, o))
                h.addWidget(chip)
                chips[opt] = chip
            cv.addWidget(chip_host)

            if key in LANG_PICKER_KEYS:
                more_btn = QToolButton()
                more_btn.setText("More \u25be")
                more_btn.setStyleSheet(f"""
                    QToolButton {{
                        background: transparent;
                        color: {TH['fg_dim']};
                        border: 1px dashed {TH['border_hi']};
                        border-radius: 11px;
                        padding: 2px 8px;
                        font-family: '{FONT_FAMILY}';
                        font-size: 8pt;
                    }}
                    QToolButton:hover {{ color: {TH['fg_bright']}; border-color: {TH['accent_hi']}; }}
                """)
                more_btn.setPopupMode(QToolButton.InstantPopup)
                menu = QMenu(more_btn)
                for lang in MORE_LANGS:
                    act = menu.addAction(lang)
                    act.triggered.connect(lambda _=False, k=key, l=lang: self._add_lang(k, l))
                more_btn.setMenu(menu)
                h.addWidget(more_btn)
                chips["__more__"] = more_btn

            h.addStretch(1)

        # A card's width should come from its CONTROL, never from its hint
        # text - the hint wraps to fit whatever width the control already
        # needs, rather than a one-line, non-wrapping label silently
        # stretching a checkbox's card out to ~400px just to fit a whole
        # sentence on one line (which is what made every card full-width
        # and defeated packing entirely).
        content_width = max(container.sizeHint().width(),
                             chip_host.sizeHint().width() if chip_host is not None else 0,
                             180)

        # ---- hint: wraps within the card's own width, full text on hover ----
        notes = []
        base = HINTS.get(key, "")
        if base:
            notes.append(base)
        if key in CHIP_KEYS:
            notes.append(CHIP_ORDER_HINT)

        base_hint = " ".join(notes) if notes else ""
        hint_label = None
        if base_hint or key in MODE_HINTS:
            hint_label = self._make_label("", size=8, color=TH["fg_dim"], wrap=True)
            hint_label.setMaximumWidth(content_width)
            cv.addWidget(hint_label)
            self._apply_hint_text(card, hint_label, base_hint)

        if key == "SUB_LANGUAGES":
            self._autosub_warning = self._make_label("", size=8, color=TH["warn"], wrap=True)
            self._autosub_warning.setMaximumWidth(content_width)
            cv.addWidget(self._autosub_warning)
            self._autosub_warning.setVisible(False)

        target_flow.addWidget(card)

        self.rows[key] = {
            "primary":       primary,
            "entry":         entry,
            "targets":       targets,
            "kind":          kind,
            "chips":         chips,
            "ruler":         ruler,
            "group":         group,
            "container":     container,
            "dyn_container": dyn_container,
            "card":          card,
            "all_widgets":   [card],
            "hint_label":    hint_label,
            "base_hint":     base_hint,
            "status":        ST_NORMAL,
            "reason":        "",
            "reason_kind":   REASON_NONE,
        }

        if self._current_section:
            self.sections[self._current_section]["widgets"].append(card)
            self.row_section[key] = self._current_section
            self.row_subsection[key] = self._current_subsection

        if key in CHIP_KEYS:
            self._autosize_chip(key)

    @staticmethod
    def _teaser_text(text: str, limit: int = 90) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        cut = text[:limit].rsplit(" ", 1)[0]
        return cut + "\u2026"

    def _apply_hint_text(self, card, hint_label, text: str):
        """One line visible (a teaser), the full paragraph on hover - so a
        card stays compact but nothing the file used to say out loud is
        actually lost, just one hover away instead of always taking up
        vertical space."""
        hint_label.setText(self._teaser_text(text) if text else "")
        hint_label.setVisible(bool(text))
        card.setToolTip(text)

    # ---- value access ----------------------------------------------------

    def _read_value(self, key):
        """The single boundary between Qt widgets (which only ever speak
        str/bool/int primitives) and everything else in this file - the
        engine, the JSON on disk, undo snapshots, dirty-checking. Returns
        the REAL native type every time: a real bool from a checkbox, a
        real int from a spinbox, a real list from a chip field's text.
        Every caller of _value_of()/_read_value() gets that for free -
        there is no second place that could forget to convert, which is
        exactly what let _gather_settings ship broken before."""
        r = self.rows[key]
        kind = r["kind"]
        if kind == "bool":
            return r["primary"].isChecked()
        if kind == "toggles":
            btn = r["group"].checkedButton()
            return btn.text() if btn else ""
        if kind == "ruler":
            return r["ruler"].current_value()
        if kind == "enum":
            return r["primary"].currentText()
        if kind == "spinbox":
            return r["primary"].value()
        text = r["primary"].text()
        if key in ARRAY_INT_KEYS:
            # Called on every keystroke, so it must never raise on
            # half-typed input ("480,7a"). Non-numeric entries are skipped
            # here and reported by _validation_problems() at save time,
            # so nothing is silently dropped from what actually gets saved.
            return [int(s.strip()) for s in text.split(",") if s.strip().isdigit()]
        if key in ARRAY_STR_KEYS:
            return [s.strip() for s in text.split(",") if s.strip()]
        if key == "MAX_COMMENTS":
            t = text.strip()
            return t.lower() if t.lower() == "all" else t
        return text

    def _write_value(self, key, value):
        """The reverse boundary - given a real native value (from an undo
        snapshot, say), pushes it into whatever widget this field uses."""
        r = self.rows[key]
        kind = r["kind"]
        if kind == "bool":
            r["primary"].setChecked(bool(value))
        elif kind == "toggles":
            for btn in r["group"].buttons():
                if btn.text() == value:
                    btn.setChecked(True)
                    return
        elif kind == "ruler":
            r["ruler"].set_value(str(value))
        elif kind == "enum":
            r["primary"].setCurrentText(str(value))
        elif kind == "spinbox":
            try:
                r["primary"].setValue(int(value))
            except (ValueError, TypeError):
                pass
        else:
            text = ",".join(str(v) for v in value) if isinstance(value, list) else str(value)
            r["primary"].setText(text)

    def _value_of(self, key):
        return self._read_value(key)

    # ---- sections / search ----------------------------------------------

    def _toggle_section(self, name):
        sec = self.sections[name]
        sec["collapsed"] = not sec["collapsed"]
        sec["toggle"].setText("\u25b8" if sec["collapsed"] else "\u25be")
        self._apply_visibility()

    def _on_search_change(self, _text=None):
        self._search_term = self.search_edit.text()
        self._apply_visibility()

    def _haystack(self, key):
        """Everything a search can hit for one row, lower-cased: the key
        (also with _ read as spaces), its section AND subsection heading,
        its current value and its hint text."""
        k = key.lower()
        v = self._read_value(key)
        v_text = ",".join(str(x) for x in v) if isinstance(v, list) else str(v)
        return " ".join([
            k, k.replace("_", " "),
            (self.row_section.get(key) or "").lower(),
            (self.row_subsection.get(key) or "").lower(),
            v_text.lower(),
            HINTS.get(key, "").lower(),
        ])

    def _row_matches(self, key, term):
        """Every whitespace-separated word must hit somewhere in the row's
        haystack (so 'thumbnail format' or 'embed subs' work, and the word
        order doesn't matter)."""
        tokens = (term or "").lower().split()
        if not tokens:
            return True
        hay = self._haystack(key)
        return all(t in hay for t in tokens)

    def _search_result_set(self, term):
        """(matched, context). matched = rows that hit the term. context =
        their gating ancestors (SUB_LANGUAGES -> ENABLE_SUBTITLES ->
        ENABLE_SIDECAR): shown alongside so a greyed-out hit can be
        switched on right where it is. Ancestors only - never a hit's
        descendants, so a search doesn't fan out to unrelated children."""
        matched = {k for k, r in self.rows.items()
                   if r.get("status") != ST_HIDDEN and self._row_matches(k, term)}
        context = set()
        for k in matched:
            parent = SCHEMA.get(k, ("text", None, None))[2]
            while parent:
                r = self.rows.get(parent)
                if r is not None and r.get("status") != ST_HIDDEN and parent not in matched:
                    context.add(parent)
                parent = SCHEMA.get(parent, ("text", None, None))[2]
        return matched, context

    # ---- status model ----------------------------------------------------

    def _structural_disables(self, values):
        """Keys DISABLED by a fact about the current mode alone. The rule is
        data now (`disabled_in` on a field in the schema). A future
        during-GUI adds scan-derived entries here too, tagged
        REASON_EXTERNAL. Same rendering, same cascade."""
        return S.structural_disables(values)

    def _status_of(self, key, values, structural_disables):
        """
        The single source of truth for one field's UI state. Returns
        (status, reason_text, reason_kind). Driven entirely by the current
        values and mode - never reads widget state.

        reason_kind (REASON_SETTINGS / REASON_EXTERNAL) does not change
        the rendering or the cascade. It only tells the UI how to phrase
        the reason: SETTINGS reads as an instruction ("turn X on"),
        EXTERNAL as a fact ("no X on this video"). The pre-GUI produces
        only REASON_SETTINGS today.
        """
        # The rule itself lives in the schema (hidden by mode -> structurally
        # disabled -> nearest unmet parent); this only maps it to UI terms.
        # `structural_disables` is kept in the signature for the callers; the
        # schema recomputes the identical dict from `values`.
        state, reason = S.effective_state(values, key)
        if state == "hidden":
            return ST_HIDDEN, "", REASON_NONE
        if state == "disabled":
            return ST_DISABLED, reason, REASON_SETTINGS
        return ST_NORMAL, "", REASON_NONE

    def _tooltip_for(self, status, reason, kind):
        if status == ST_HIDDEN:
            return "Hidden in this mode"
        if not reason:
            return ""
        if kind == REASON_EXTERNAL:
            return f"\u2716 {reason}"     # fact
        return f"\u26a0 {reason}"          # instruction

    # ---- state -----------------------------------------------------------

    def _on_change(self):
        if self._suppress_on_change:
            return
        self._sync_auxiliary()
        self._refresh_states()
        self._apply_visibility()
        now_dirty = any(self._value_of(k) != self._baseline[k]
                        for k in self.rows)
        if now_dirty != self.dirty:
            self.dirty = now_dirty
            self._update_title()

        if not self._applying_undo:
            self._undo_timer.start()

    def _refresh_states(self):
        values = {k: self._read_value(k) for k in self.rows}
        structural_disables = self._structural_disables(values)

        for key, r in self.rows.items():
            status, reason, kind = self._status_of(key, values, structural_disables)
            r["status"] = status
            r["reason"] = reason
            r["reason_kind"] = kind
            enabled = (status == ST_NORMAL)
            tip = self._tooltip_for(status, reason, kind)
            for t in r["targets"]:
                t.setEnabled(enabled)
                t.setToolTip(tip)
            # Mode-aware hints: HINTS' static prose stays as the base; a
            # mode-specific line is appended when the mode makes the
            # original wording misleading.
            if r.get("hint_label") is not None and key in MODE_HINTS:
                mode_line = MODE_HINTS[key].get(values.get("DOWNLOAD_TYPE", "video"), "")
                text = r["base_hint"]
                if mode_line:
                    text = (text + "  " if text else "") + mode_line
                self._apply_hint_text(r["card"], r["hint_label"], text)

    def _update_title(self):
        mark = " \u2022" if self.dirty else ""
        self.setWindowTitle(f"Settings \u2014 {self.path.name}{mark}")

    def _flash(self, msg):
        self.status.setText(msg)
        QTimer.singleShot(2500, lambda: self.status.setText(""))

    # ---- visibility ------------------------------------------------------

    def _apply_visibility(self):
        term = self._search_term.strip()
        searching = bool(term)

        if searching:
            matched, context = self._search_result_set(term)
            shown = matched | context
        else:
            shown = set()

        self.setUpdatesEnabled(False)
        try:
            section_match = {name: False for name in self.sections}
            sub_match = set()
            for key in self.rows:
                sec = self.row_section.get(key)
                if sec is None:
                    continue
                if self.rows[key].get("status") == ST_HIDDEN:
                    continue
                if not searching or key in shown:
                    section_match[sec] = True
                    if searching and self.row_subsection.get(key):
                        sub_match.add((sec, self.row_subsection[key]))

            for name, sec in self.sections.items():
                sec["header"].setVisible(bool(section_match.get(name)))

            for key, r in self.rows.items():
                if r.get("status") == ST_HIDDEN:
                    visible = False
                else:
                    sec = self.row_section.get(key)
                    if sec is None:
                        visible = (not searching) or key in shown
                    elif searching:
                        visible = key in shown
                    else:
                        visible = not self.sections[sec]["collapsed"]
                for w in r["all_widgets"]:
                    w.setVisible(visible)

            row_widget_set = {w for r in self.rows.values() for w in r["all_widgets"]}
            for name, sec in self.sections.items():
                for w in sec["widgets"]:
                    if w in row_widget_set:
                        continue
                    if isinstance(w, CollapsibleSection):
                        self._apply_collapsible_visibility(
                            w, name, sec, searching, shown)
                        continue
                    if w in self._subsection_hosts:
                        # a subsection heading follows its rows: during a
                        # search it shows iff one of its rows is showing
                        if searching:
                            w.setVisible(self._subsection_hosts[w] in sub_match)
                        else:
                            w.setVisible(not sec["collapsed"])
                        continue
                    w.setVisible(not (searching or sec["collapsed"]))

            if self._autosub_warning is not None:
                show = self._autosub_warning_should_show
                sec_name = self.row_section.get("SUB_LANGUAGES")
                if sec_name and not searching and self.sections[sec_name]["collapsed"]:
                    show = False
                if searching and "SUB_LANGUAGES" not in shown:
                    show = False
                self._autosub_warning.setVisible(show)
        finally:
            self.setUpdatesEnabled(True)
            self._activate_flows()
            self.inner.updateGeometry()

    def _apply_collapsible_visibility(self, w, sec_name, sec, searching, shown):
        """The 'Advanced' disclosure. Searching: it appears only if one of
        its own rows is showing (an empty header with nothing behind it was
        the old 'text only' look), and it is opened for the duration of the
        search, then put back the way the user had it."""
        if searching:
            has_hit = any(k in shown for k in ADVANCED_OVERRIDE_KEYS if k in self.rows)
            w.setVisible(has_hit)
            if has_hit and w.is_collapsed():
                w._auto_opened = True
                w.header.setChecked(True)
        else:
            if getattr(w, "_auto_opened", False):
                w._auto_opened = False
                if not w.is_collapsed():
                    w.header.setChecked(False)
            w.setVisible(not sec["collapsed"])

    # ---- chip / ruler / aux handlers ------------------------------------

    def _toggle_chip(self, key, value):
        r = self.rows[key]
        if not r["primary"].isEnabled():
            return
        current = [x.strip() for x in r["primary"].text().split(",") if x.strip()]
        if value in current:
            current.remove(value)
        else:
            current.append(value)
        r["primary"].setText(",".join(current))

    def _add_lang(self, key, lang):
        """Append a language picked from the 'More ▾' menu to the field's
        text. Idempotent - re-picking a language that's already present
        doesn't duplicate it."""
        r = self.rows.get(key)
        if r is None or not r["primary"].isEnabled():
            return
        current = [x.strip() for x in r["primary"].text().split(",") if x.strip()]
        if lang in current:
            self._flash(f"{lang} already in the list")
            return
        current.append(lang)
        r["primary"].setText(",".join(current))

    def _autosize_chip(self, key):
        r = self.rows.get(key)
        if r is None or r["dyn_container"] is None:
            return
        text = r["primary"].text()
        fm = QFontMetrics(QFont(FONT_FAMILY, 9))
        want = max(DYN_MIN_PX, min(DYN_MAX_PX, fm.horizontalAdvance(text) + DYN_PAD_PX + 12))
        r["dyn_container"].setFixedWidth(want)

    def _sync_auxiliary(self):
        for key, r in self.rows.items():
            current_raw = self._read_value(key)

            if r["chips"]:
                # current_raw is a real list for every chip-backed key
                # (_read_value's array branches already produced it)
                current = {str(x).strip().lower() for x in current_raw if str(x).strip()}
                for val, chip in r["chips"].items():
                    if val == "__more__":
                        continue
                    checked = val.lower() in current
                    blocked = chip.blockSignals(True)
                    chip.setChecked(checked)
                    chip.blockSignals(blocked)

            if r["dyn_container"] is not None:
                self._autosize_chip(key)

            if r["ruler"] is not None:
                r["ruler"].set_value(current_raw.strip())

        self._clamp_parsed_comments()
        self._update_autosub_warning()

    def _clamp_parsed_comments(self):
        """MAX_COMMENTS is a plain text field, not a spinbox - it has to
        accept yt-dlp's literal "all" sentinel as well as a number, and a
        spinbox can't represent that. So this only clamps when MAX_COMMENTS
        currently holds an actual number; "all" (or anything else
        non-numeric) just means "no cap to clamp against" and is left
        alone rather than crashing or silently mangling it to 0."""
        if "MAX_COMMENTS" not in self.rows or "MAX_PARSED_COMMENTS" not in self.rows:
            return
        try:
            cap = int(self.rows["MAX_COMMENTS"]["primary"].text().strip())
        except (ValueError, AttributeError):
            return
        spin = self.rows["MAX_PARSED_COMMENTS"]["primary"]
        spin.setMaximum(max(0, cap))
        if spin.value() > cap:
            spin.setValue(cap)

    def _update_autosub_warning(self):
        self._autosub_warning_should_show = False
        if self._autosub_warning is None:
            return
        if "SUB_LANGUAGES" not in self.rows:
            return

        def yn(k):
            r = self.rows.get(k)
            if r is None:
                return False
            return bool(self._read_value(k))

        sidecar = yn("ENABLE_SIDECAR")
        subs = sidecar and yn("ENABLE_SUBTITLES")
        autosubs = subs and yn("ENABLE_AUTO_SUBS")
        lang_val = self._read_value("SUB_LANGUAGES")  # real list now

        if autosubs and any(".*" in entry for entry in lang_val):
            self._autosub_warning.setText(AUTOSUB_WARNING)
            self._autosub_warning_should_show = True

    # ---- undo / redo -----------------------------------------------------

    def _commit_undo_snapshot(self):
        current = {k: self._value_of(k) for k in self.rows}
        if current == self._last_committed_state:
            return
        self._undo_stack.append(self._last_committed_state)
        if len(self._undo_stack) > _UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._last_committed_state = current
        self._update_undo_buttons()

    def _apply_snapshot(self, snap):
        self._applying_undo = True
        self._suppress_on_change = True
        try:
            for k, v in snap.items():
                if k not in self.rows:
                    continue
                self._write_value(k, v)
        finally:
            self._suppress_on_change = False

        try:
            self._sync_auxiliary()
            self._refresh_states()
            self._apply_visibility()
            now_dirty = any(self._value_of(k) != self._baseline[k]
                            for k in self.rows)
            if now_dirty != self.dirty:
                self.dirty = now_dirty
                self._update_title()
        finally:
            self._applying_undo = False

    def _undo(self):
        if self._undo_timer.isActive():
            self._undo_timer.stop()
            self._commit_undo_snapshot()
        if not self._undo_stack:
            return
        current = {k: self._value_of(k) for k in self.rows}
        self._redo_stack.append(current)
        snap = self._undo_stack.pop()
        self._apply_snapshot(snap)
        self._last_committed_state = snap
        self._update_undo_buttons()

    def _redo(self):
        if self._undo_timer.isActive():
            self._undo_timer.stop()
            self._commit_undo_snapshot()
        if not self._redo_stack:
            return
        current = {k: self._value_of(k) for k in self.rows}
        self._undo_stack.append(current)
        snap = self._redo_stack.pop()
        self._apply_snapshot(snap)
        self._last_committed_state = snap
        self._update_undo_buttons()

    def _update_undo_buttons(self):
        self._undo_btn.setEnabled(bool(self._undo_stack))
        self._redo_btn.setEnabled(bool(self._redo_stack))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and self.isActiveWindow():
            focused = self.focusWidget()
            in_text = isinstance(focused, (QLineEdit, QAbstractSpinBox))
            if not in_text:
                if event.matches(QKeySequence.StandardKey.Undo):
                    self._undo()
                    return True
                if event.matches(QKeySequence.StandardKey.Redo):
                    self._redo()
                    return True
        return super().eventFilter(obj, event)

    # ---- yt-dlp update notice --------------------------------------------

    def _start_update_check(self):
        """One passive 'yt-dlp update available' toolbar link when the
        installed version differs from the latest release. Deliberately
        nothing more: auto-update is pinned, not built (PINS.md). Silent
        when current, silent when the check can't run - there is no cache
        and no retry, the next launch simply checks again.

        yt_exe is read HERE, on the GUI thread - _gather_settings touches
        widgets and must never run on the worker thread."""
        yt_exe = self._gather_settings()["YT_DLP_EXE"]
        holder = {}

        def worker():
            try:
                eng = _import_sibling("yt_video_downloader7.5.py")
                holder["pair"] = eng.check_yt_dlp_update(yt_exe)
                holder["outdated"] = eng.yt_dlp_outdated(*holder["pair"])
                holder["url"] = eng.UPDATE_PAGE_URL
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            if not holder:
                QTimer.singleShot(100, poll)
                return
            installed, latest = holder.get("pair", (None, None))
            if installed and latest and holder.get("outdated"):
                url = holder.get("url", "https://github.com/yt-dlp/yt-dlp/releases/latest")
                self._update_lbl.setText(
                    f'yt-dlp update available: {installed} \u2192 '
                    f'<a href="{url}">{latest}</a>')
                self._update_lbl.setVisible(True)
                self._update_lbl.setToolTip(
                    "Passive notice only - nothing auto-updates. "
                    "Click the version to open the releases page.")

        poll()

    # ---- preview ---------------------------------------------------------

    def _gather_settings(self):
        raw = {k: self._value_of(k) for k in self.rows}
        # Same guard as the engine's load_settings (S.enforce, one copy): a
        # mistyped value in a text field is replaced by its schema default,
        # so Preview behaves exactly like the real run. The messages are
        # kept so the Preview dialog can show them as [WRONG] lines.
        raw, self._enforce_msgs = S.enforce(raw)
        return _expand_settings(S.with_defaults(raw), SCRIPT_DIR)

    def _resolve_preview_url(self, eng):
        try:
            clip = eng.get_clipboard()
        except Exception:
            clip = ""
        if clip and URL_RE.match(clip.strip()):
            return clip.strip()
        return (PREVIEW_URL or "").strip()

    def _preview_command(self):
        # The engine is re-executed on every preview (see _import_sibling),
        # so edits to it apply to the next preview without restarting this
        # window.
        try:
            eng = _import_sibling("yt_video_downloader7.5.py")
        except Exception as e:
            QMessageBox.critical(self, "Preview failed",
                                 f"Could not import yt_video_downloader7.5:\n\n{e}")
            return

        url = self._resolve_preview_url(eng)
        if not url:
            QMessageBox.information(
                self, "Preview",
                "No URL to preview. Copy a YouTube URL to the clipboard, "
                "or set PREVIEW_URL in this script to a fallback.")
            return

        settings = self._gather_settings()
        wrong = list(getattr(self, "_enforce_msgs", []))
        settings["DEBUG_DRY_RUN"] = True
        download_dir = Path(settings["DOWNLOAD_DIR"])
        log_dir = Path(settings["LOG_DIR"])
        yt_exe = settings["YT_DLP_EXE"]

        self._flash("Analyzing URL - this can take a few seconds")
        QApplication.setOverrideCursor(Qt.WaitCursor)

        holder = {}

        def worker():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    holder["result"] = eng.process_one_url(
                        url, settings, yt_exe, download_dir, log_dir)
            except Exception as e:
                holder["err"] = str(e)
            captured = buf.getvalue().rstrip()
            if wrong:
                captured = ("\n".join(f"[WRONG] {m}" for m in wrong)
                            + "\n\n" + captured)
            holder["captured"] = captured

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                QTimer.singleShot(80, poll)
                return
            QApplication.restoreOverrideCursor()
            self._flash("")
            self._show_preview_dialog(
                url,
                holder.get("captured", ""),
                holder.get("result"),
                holder.get("err"),
            )

        QTimer.singleShot(80, poll)

    def _show_preview_dialog(self, url, captured, result, err):
        win = QDialog(self)
        win.setWindowTitle("Command preview")
        win.setStyleSheet(_qss(TH))
        win.resize(1020, 780)
        win.setModal(True)

        v = QVBoxLayout(win)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        if err:
            badge_text, badge_color = f"\u26a0 Engine raised: {err}", TH["warn"]
        else:
            status = (result or {}).get("status", "?")
            if status == "DRY_RUN":
                badge_text, badge_color = "\u2713 Dry run complete", TH["ok"]
            else:
                badge_text, badge_color = f"\u26a0 Engine returned: {status}", TH["warn"]

        badge = QLabel(badge_text)
        badge.setFont(QFont(FONT_FAMILY, 10, QFont.Bold))
        badge.setStyleSheet(f"color:{badge_color};background:transparent;")
        v.addWidget(badge)

        url_lbl = QLabel(url)
        url_lbl.setFont(QFont(MONO_FAMILY, 9))
        url_lbl.setWordWrap(True)
        url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_lbl.setStyleSheet("background:transparent;")
        v.addWidget(url_lbl)

        hint = QLabel("Live analysis - real probe, real playlist scan, real "
                      "command build. Nothing was downloaded.")
        hint.setStyleSheet(f"color:{TH['fg_dim']};background:transparent;")
        v.addWidget(hint)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont(MONO_FAMILY, 9))
        txt.setLineWrapMode(QTextEdit.WidgetWidth)
        txt.setPlainText("PIPELINE OUTPUT\n" + "=" * 78 + "\n" +
                         ((captured or "<no output>").rstrip()))
        v.addWidget(txt, 1)

        m = FULL_COMMAND_RE.search(captured or "")
        cmd_str = m.group(1).strip() if m else None

        btns = QHBoxLayout()
        btns.setSpacing(6)
        if cmd_str:
            copy_btn = QPushButton("Copy command")
            copy_btn.clicked.connect(lambda: self._copy_cmd(cmd_str))
            btns.addWidget(copy_btn)
        btns.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(win.accept)
        btns.addWidget(close_btn)
        v.addLayout(btns)

        win.exec()

    def _copy_cmd(self, cmd_str):
        QApplication.clipboard().setText(cmd_str)
        self._flash("Command copied to clipboard")

    # ---- actions ---------------------------------------------------------

    def _validation_problems(self):
        """Things that would be silently wrong if saved as typed. Checked
        at Save time rather than per keystroke (half-typed input is
        normal while editing), and only for the fields where the file
        format has a real constraint."""
        problems = []
        if "QUALITY_PRIORITY" in self.rows:
            bad = [t.strip() for t in self.rows["QUALITY_PRIORITY"]["primary"].text().split(",")
                   if t.strip() and not t.strip().isdigit()]
            if bad:
                problems.append(
                    f"QUALITY_PRIORITY: {', '.join(repr(b) for b in bad)} "
                    "isn't a height (whole numbers only, e.g. 480,720)")
        if "MAX_COMMENTS" in self.rows:
            t = self.rows["MAX_COMMENTS"]["primary"].text().strip()
            if not (t.isdigit() or t.lower() == "all"):
                problems.append(f"MAX_COMMENTS: {t!r} must be a number or 'all'")
        return problems

    def _save(self):
        problems = self._validation_problems()
        if problems:
            QMessageBox.warning(self, "Can't save yet",
                                "Fix these first:\n\n" + "\n".join(problems))
            return
        new_values = {k: self._value_of(k) for k in self.rows}
        try:
            write_settings(self.path, new_values)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.dirty = False
        self._baseline = {k: self._value_of(k) for k in self.rows}
        self._update_title()
        self._flash(f"Saved to {self.path.name}")

    def _reload(self):
        if self.dirty:
            ans = QMessageBox.question(
                self, "Reload", "Discard unsaved changes?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return

        self.doc = read_settings(self.path)

        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        self.rows.clear()
        self.sections.clear()
        self.row_section.clear()
        self.row_subsection.clear()
        self._subsection_hosts.clear()
        self._collapsibles = []
        self._current_subsection = None
        self._current_section = None
        self._autosub_warning = None
        self._autosub_warning_should_show = False
        self._advanced_section = None

        self._render()
        self._baseline = {k: self._value_of(k) for k in self.rows}
        self._refresh_states()
        self._sync_auxiliary()
        self._apply_visibility()

        if self._undo_timer.isActive():
            self._undo_timer.stop()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._last_committed_state = {k: self._value_of(k) for k in self.rows}
        self._update_undo_buttons()

        self.dirty = False
        self._update_title()
        self._flash("Reloaded from file.")

    def _open_raw(self):
        candidates = [
            r"C:\Program Files\Notepad++\notepad++.exe",
            r"C:\Program Files (x86)\Notepad++\notepad++.exe",
            "notepad++.exe",
        ]
        for exe in candidates:
            if exe.endswith(".exe") and not Path(exe).exists() and "\\" in exe:
                continue
            try:
                subprocess.Popen([exe, str(self.path)])
                return
            except FileNotFoundError:
                continue
            except Exception as e:
                QMessageBox.critical(self, "Open in Notepad++", str(e))
                return
        try:
            subprocess.Popen(["notepad.exe", str(self.path)])
        except Exception as e:
            QMessageBox.critical(self, "Open editor", str(e))

    def _browse(self, key):
        current = self._read_value(key)
        if BROWSE_KEYS[key] == "dir":
            chosen = QFileDialog.getExistingDirectory(
                self, "Select folder", current or str(SCRIPT_DIR))
        else:
            start = str(Path(current).parent) if current else str(SCRIPT_DIR)
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Select file", start)
        if chosen:
            self._write_value(key, str(Path(chosen)))

    def _focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _clear_search(self):
        self.search_edit.setText("")
        self.setFocus()

    def closeEvent(self, event):
        if self.dirty:
            ans = QMessageBox.question(
                self, "Unsaved changes", "Save changes before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if ans == QMessageBox.Cancel:
                event.ignore()
                return
            if ans == QMessageBox.Save:
                self._save()
        event.accept()


# ---------------------------------------------------------------------------

def _apply_dark_titlebar(widget):
    try:
        import ctypes
        widget.update()
        hwnd = ctypes.windll.user32.GetParent(widget.winId())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("YouTube Downloader Settings")

    if not SETTINGS_FILE.exists():
        QMessageBox.critical(
            None, "Settings file not found",
            f"Could not find:\n{SETTINGS_FILE}\n\n"
            "Put this script next to yt_settings7.json.")
        return 1

    win = SettingsEditor(SETTINGS_FILE)
    win.show()
    _apply_dark_titlebar(win)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())