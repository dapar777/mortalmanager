"""Record a short feature tour of Ultimate Commander into docs/demo.mp4.

Drives the real application (hidden window, frames taken with QWidget.grab)
through a scripted tour – panels, marking, quick filter, terminal with a
persistent session and placeholders, command palette, clipboard copies,
in-place rename, zoom and the dark theme – and writes an H.264 video with
Czech captions. Needs ``imageio`` + ``imageio-ffmpeg`` (dev only):

    C:\\mm_venv\\Scripts\\python.exe tools\\make_demo_video.py [output.mp4]
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

import imageio.v2 as imageio  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QBuffer, QEvent, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

FPS = 10
W, H = 1280, 760            # application window
BAND = 56                   # caption band below the window
VH = H + BAND               # video height
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "demo.mp4"

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT = ImageFont.truetype(str(FONT_DIR / "segoeui.ttf"), 22)
FONT_B = ImageFont.truetype(str(FONT_DIR / "segoeuib.ttf"), 54)
FONT_S = ImageFont.truetype(str(FONT_DIR / "segoeui.ttf"), 26)


# ------------------------------------------------------------------ recorder

class Recorder:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = imageio.get_writer(str(path), fps=FPS, codec="libx264", quality=8,
                                         pixelformat="yuv420p", macro_block_size=8)
        self.caption = ""
        self.frames = 0

    def add(self, img: Image.Image) -> None:
        if img.size != (W, VH):
            canvas = Image.new("RGB", (W, VH), (0, 43, 54))
            canvas.paste(img, (0, 0))
            img = canvas
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, H, W, VH], fill=(0, 43, 54))
        if self.caption:
            draw.text((24, H + 12), self.caption, font=FONT, fill=(253, 246, 227))
        self.writer.append_data(__import__("numpy").asarray(img.convert("RGB")))
        self.frames += 1

    def card(self, title: str, subtitle: str, seconds: float) -> None:
        img = Image.new("RGB", (W, VH), (0, 43, 54))
        d = ImageDraw.Draw(img)
        tw = d.textlength(title, font=FONT_B)
        d.text(((W - tw) / 2, VH / 2 - 70), title, font=FONT_B, fill=(253, 246, 227))
        sw = d.textlength(subtitle, font=FONT_S)
        d.text(((W - sw) / 2, VH / 2 + 10), subtitle, font=FONT_S, fill=(147, 161, 161))
        d.rectangle([W / 2 - 40, VH / 2 - 90, W / 2 + 40, VH / 2 - 86], fill=(203, 75, 22))
        saved, self.caption = self.caption, ""
        for _ in range(int(seconds * FPS)):
            self.add(img.copy())
        self.caption = saved

    def close(self) -> None:
        self.writer.close()


def qimage_to_pil(pixmap) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.ReadWrite)
    pixmap.save(buf, "PNG")
    from io import BytesIO
    return Image.open(BytesIO(bytes(buf.data()))).convert("RGB")


# ------------------------------------------------------------------ app

app = QApplication(sys.argv)
from src.solarqt import theme  # noqa: E402
theme.apply(app, "light", zoom=1.0)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
from src.gui.main_window import MainWindow  # noqa: E402

win = MainWindow(loop)
win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
win.setWindowState(Qt.WindowState.WindowNoState)     # the saved config may say "maximized"
# the tour must not touch the user's saved settings (theme, zoom, window geometry)
win._cfg.save = lambda: None
win._save_timer.timeout.disconnect()
win._cfg.config.theme = "light"
win.show()
win.resize(W, H)
app.processEvents()
win.resize(W, H)
rec = Recorder(OUT)
overlays: list = []          # top-level widgets composited over the main window (palette)


def grab_logical(widget) -> Image.Image:
    """QWidget.grab() returns device pixels (display scaling 120 % → 1536×912);
    scale back to the logical size so the video is exactly W×H."""
    img = qimage_to_pil(widget.grab())
    size = (widget.width(), widget.height())
    if img.size != size:
        img = img.resize(size, Image.Resampling.LANCZOS)
    return img


def frame() -> None:
    img = grab_logical(win)
    if img.size != (W, H):                     # zoom can push the window past its minimum size
        canvas = Image.new("RGB", (W, H), img.getpixel((0, 0)))
        canvas.paste(img.crop((0, 0, min(W, img.width), min(H, img.height))), (0, 0))
        img = canvas
    for w in overlays:
        if w.isVisible():
            pos = w.pos() - win.pos()
            x = max(0, min(W - w.width(), pos.x())) if 0 <= pos.x() < W else (W - w.width()) // 2
            y = max(0, min(H - w.height(), pos.y())) if 0 <= pos.y() < H else 90
            img.paste(grab_logical(w), (x, y))
    rec.add(img)


def pump(seconds: float, until=lambda: False) -> None:
    """Run the app for ``seconds`` while recording at FPS."""
    end = time.time() + seconds
    nxt = time.time()
    while time.time() < end and not until():
        loop.call_soon(loop.stop)
        loop.run_forever()
        app.processEvents()
        if time.time() >= nxt:
            frame()
            nxt += 1 / FPS
        time.sleep(0.01)


def key(widget, k, mods=Qt.KeyboardModifier.NoModifier, text="", hold=0.25) -> None:
    app.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    pump(hold)


def type_text(edit: QLineEdit, text: str, per_char=0.12) -> None:
    for ch in text:
        edit.setText(edit.text() + ch)
        edit.setCursorPosition(len(edit.text()))
        edit.textEdited.emit(edit.text())
        pump(per_char)


# ------------------------------------------------------------------ demo data

demo_root = Path.home() / "Ultimate Commander Demo"        # readable in the path bar, removed at the end
if demo_root.exists():
    shutil.rmtree(demo_root, ignore_errors=True)
demo = demo_root / "Projekt Alfa"
demo.mkdir(parents=True)
for d in ("dokumenty", "obrázky", "src", "tests"):
    (demo / d).mkdir()
for n, size in (("README.md", 1800), ("reports.txt", 5400), ("budget.xlsx", 22000), ("main.py", 3100),
                ("notes.md", 900), ("setup.cfg", 400), ("photo_01.jpg", 410_000), ("photo_02.jpg", 385_000),
                ("data.csv", 71_000)):
    (demo / n).write_bytes(b"x" * size)
(demo / "src" / "app.py").write_text("print('hello')\n")
(demo / "dokumenty" / "smlouva.docx").write_bytes(b"x" * 15000)
other = demo.parent / "Záloha"
other.mkdir()

left, right = win._left_panel, win._right_panel
win._set_active("left")
left.navigate_to(str(demo))
right.navigate_to(str(other))
tbl = left._table


def cursor_on(name: str) -> None:
    row = tbl.file_model().row_of_name(name)
    if row >= 0:
        tbl.setCurrentIndex(tbl.model().index(row, 0))


pump(1.0)

# 1 --------------------------------------------------------------- title
rec.card("Ultimate Commander", "Dvoupanelový správce souborů pro Windows · Python + PySide6 · Solarized", 3.0)

# 2 --------------------------------------------------------------- panels & navigation
rec.caption = "Dva panely s taby, klikací cesta vedle tabů, lišta disků, zoomovatelné UI"
pump(1.5)
cursor_on("src")
pump(0.6)
key(tbl, Qt.Key.Key_Return, hold=0.8)                 # enter src
pump(0.8)
left._crumb._buttons[-2].click()                       # breadcrumb: back to the project
pump(1.2)
for _ in range(3):
    key(tbl, Qt.Key.Key_Down, hold=0.25)

# 3 --------------------------------------------------------------- marking
rec.caption = "Označování jako v Total Commanderu: Insert, mezerník, Shift+šipky, Ctrl+klik"
cursor_on("README.md")
pump(0.5)
for _ in range(2):
    key(tbl, Qt.Key.Key_Insert, hold=0.35)
for _ in range(3):
    key(tbl, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier, hold=0.35)
pump(1.2)

# 4 --------------------------------------------------------------- quick filter
rec.caption = "Rychlý filtr Ctrl+S nebo * – podřetězec nebo maska *.jpg, Esc zruší"
left.deselect_all()
left.show_filter()
pump(0.4)
type_text(left._filter_edit, "photo")
pump(1.0)
left._filter_edit.setText("")
left._filter_edit.textEdited.emit("")
pump(0.3)
type_text(left._filter_edit, "*.md")
pump(1.2)
key(left._filter_edit, Qt.Key.Key_Escape, hold=0.8)

# 5 --------------------------------------------------------------- terminal
rec.caption = "Vestavěný terminál s trvalou session – proměnné přežijí mezi příkazy"
if not win._terminal.isVisible():
    win._act_cmdbar.setChecked(True)
    win._toggle_cmdbar()
term = win._terminal
term.clear_output()
term.give_focus()
pump(0.6)


def run_cmd(text: str, wait=lambda: True, timeout=8.0) -> None:
    type_text(term._input, text, per_char=0.06)
    pump(0.3)
    term._on_return()
    pump(timeout, wait)
    pump(0.5)


out = lambda: term._output.toPlainText()  # noqa: E731
run_cmd("set PROJEKT=Alfa", lambda: not term.shell_busy() and term._shell is not None)
run_cmd("echo Projekt %PROJEKT%", lambda: "Projekt Alfa" in out())
rec.caption = "Zástupné znaky jako v TC: %N soubor pod kurzorem, %S označené, %P cesta panelu, %SI iteruje"
left.give_focus()
cursor_on("reports.txt")
key(tbl, Qt.Key.Key_Insert, hold=0.3)
key(tbl, Qt.Key.Key_Insert, hold=0.3)
term.give_focus()
run_cmd("echo Označeno: %S", lambda: "reports.txt" in out().split("%S")[-1])
rec.caption = "Interaktivní Python přímo v terminálu – řádky jdou do něj, Ctrl+D ukončí"
run_cmd("python", lambda: ">>>" in term._prompt_lbl.text(), timeout=10)
run_cmd("print(2 ** 10)", lambda: "1024" in out())
run_cmd("exit()", lambda: not term.session_active())
pump(0.8)

# 6 --------------------------------------------------------------- command palette
rec.caption = "Paleta příkazů Ctrl+Shift+P: víceúrovňová, hledá i v podúrovních, historie a soubory z indexu"
from src.gui.command_palette import CommandPalette  # noqa: E402

pal = CommandPalette(win._build_palette_commands(), win, recent=[], extra_search=win._palette_extra_search,
                     mode_search=win._palette_mode_search)
pal.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
overlays.append(pal)
pal.show()
pump(1.0)
type_text(pal.search, "name")
pump(1.5)
pal.search.setText("")
pal.search.textEdited.emit("")
pump(0.3)
rec.caption = "Prefix „c “ = historie terminálu, výběr příkaz jen předvyplní do řádky"
type_text(pal.search, "c echo")
pump(1.8)
pal.close()
overlays.clear()
pump(0.6)

# 7 --------------------------------------------------------------- clipboard
rec.caption = "Ctrl+C a Ctrl+V do stejné složky = „reports - Kopie.txt“, kompatibilní s Explorerem"
left.deselect_all()
left.give_focus()
cursor_on("reports.txt")
pump(0.4)
key(tbl, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, hold=0.6)
key(tbl, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, hold=0.3)
pump(4.0, lambda: (demo / "reports - Kopie.txt").exists())
pump(1.5)

# 8 --------------------------------------------------------------- rename
rec.caption = "F2 přejmenuje v místě, Ctrl+M hromadně; Enter na .lnk vstoupí do složky"
cursor_on("reports - Kopie.txt")
pump(0.4)
key(tbl, Qt.Key.Key_F2, hold=0.5)
ed = tbl.findChild(QLineEdit, "renameEditor")        # focusWidget() is None for an off-screen window
if isinstance(ed, QLineEdit):
    ed.setText("")
    type_text(ed, "reports_2026.txt", per_char=0.08)
    pump(0.4)
    key(ed, Qt.Key.Key_Return, hold=0.3)
pump(3.0, lambda: (demo / "reports_2026.txt").exists())
pump(1.0)

# 9 --------------------------------------------------------------- zoom & theme
rec.caption = "Zoom celého UI Ctrl+kolečko / Ctrl+±, vše se škáluje včetně ikon"
for z in (1.1, 1.2, 1.3):
    win._apply_zoom(z)
    pump(0.7)
pump(1.0)
win._apply_zoom(1.0)
pump(0.8)
rec.caption = "Tmavé a světlé téma (Solarized), přepínač v hlavičce"
win._set_theme("dark")
pump(2.5)
win._set_theme("light")
pump(1.2)

# 10 -------------------------------------------------------------- outro
rec.caption = ""
rec.card("Ultimate Commander", "Rychlý, klávesnicový, ve stylu Total Commanderu · start.bat", 3.0)

win.close()
pump(0.3)
rec.close()
shutil.rmtree(demo_root, ignore_errors=True)
print(f"wrote {OUT} ({rec.frames} frames, {rec.frames / FPS:.0f} s)")
