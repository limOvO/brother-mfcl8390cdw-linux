"""Brother Studio — GTK4 介面（分頁式流程）。

流程：
    首頁 ──► 掃描分頁：掃描設定 → 掃描 → 編輯 →（存檔 / 送到列印）
       └──► 列印分頁：選檔案 → 列印設定 → 列印預覽 → 列印

掃描與列印的設定各在自己的分頁，不會擠在同一頁；
兩邊共用同一份文件，掃描完可以按「送到列印」直接帶過去。
"""

from __future__ import annotations

import io
import threading
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

import cairo  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from PIL import Image  # noqa: E402

import bs_core as C  # noqa: E402

APP_ID = "io.github.limovo.BrotherStudio"

APP_CSS = """
.card {
    background-color: alpha(currentColor, 0.05);
    border-radius: 12px;
    padding: 8px 10px;
}
.card-title {
    font-weight: bold;
    font-size: 0.95em;
    opacity: 0.85;
}
.toolbar-row {
    padding: 4px 8px;
}
.preview-frame {
    background-color: #2b2b2b;
    border-radius: 8px;
}
.statusbar {
    padding: 4px 12px;
}
.thumb-selected {
    outline: 2px solid @accent_bg_color;
    outline-offset: -2px;
    border-radius: 6px;
}
.thumb {
    padding: 4px;
    border-radius: 6px;
}
.page-badge {
    font-size: 0.8em;
    opacity: 0.7;
}
.crop-bar {
    background-color: rgba(20, 20, 22, 0.88);
    color: #f2f2f2;
    border-radius: 999px;
    padding: 6px 14px;
}
.crop-bar label {
    color: #f2f2f2;
}
.home-hero {
    font-size: 2.1em;
    font-weight: 800;
}
.home-sub {
    font-size: 1.02em;
    opacity: 0.7;
}
.tile {
    padding: 20px 18px;
    border-radius: 16px;
    background-color: alpha(currentColor, 0.07);
    min-width: 240px;
    min-height: 170px;
}
.tile:hover {
    background-color: alpha(currentColor, 0.12);
}
.tile-title {
    font-size: 1.12em;
    font-weight: bold;
}
.tile-desc {
    font-size: 0.9em;
    opacity: 0.72;
}
.step-hint {
    font-size: 0.86em;
    opacity: 0.65;
}
"""


# ---------------------------------------------------------------------------
# 影像 → cairo / Gdk
# ---------------------------------------------------------------------------

def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def pil_to_texture(img: Image.Image) -> Gdk.Texture:
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(_png_bytes(img)))


def pil_to_surface(img: Image.Image) -> cairo.ImageSurface:
    return cairo.ImageSurface.create_from_png(io.BytesIO(_png_bytes(img)))


# ---------------------------------------------------------------------------
# 預覽畫布（掃描分頁用）：縮放 / 平移 / 拖框裁切
# ---------------------------------------------------------------------------

class PreviewCanvas(Gtk.DrawingArea):
    def __init__(self, on_selection_changed=None):
        super().__init__()
        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.image: Image.Image | None = None
        self.full_size: tuple[int, int] = (0, 0)
        self._surface: cairo.ImageSurface | None = None
        self._surface_size: tuple[int, int] | None = None
        self.zoom_mode = "fit"
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.crop_active = False
        self.selection: tuple[float, float, float, float] | None = None
        self.on_selection_changed = on_selection_changed
        self._drag_start: tuple[float, float] | None = None
        self._pan_start: tuple[float, float] | None = None
        self._pan_offset = (0.0, 0.0)

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        scroll = Gtk.EventControllerScroll()
        scroll.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def set_page(self, img: Image.Image | None, full_size: tuple[int, int] | None = None):
        self.image = None
        self._surface = None
        self.selection = None
        if img is not None:
            display = img.copy()
            display.thumbnail((2000, 2000), Image.LANCZOS)
            self.image = display
            self.full_size = full_size or img.size
        self.queue_draw()
        if self.on_selection_changed:
            self.on_selection_changed(None)

    def set_zoom(self, mode: str, zoom: float = 1.0):
        self.zoom_mode = mode
        self.zoom = zoom
        self.offset = [0.0, 0.0]
        self.queue_draw()

    def set_crop_active(self, active: bool):
        self.crop_active = active
        self.selection = None
        self.queue_draw()
        if self.on_selection_changed:
            self.on_selection_changed(None)

    def _layout(self, w: int, h: int):
        if self.image is None:
            return 1.0, 0.0, 0.0
        iw, ih = self.image.size
        if self.zoom_mode == "fit" or self.zoom <= 0:
            scale = min((w - 24) / iw, (h - 24) / ih)
        else:
            scale = self.zoom
        dw, dh = iw * scale, ih * scale
        return scale, (w - dw) / 2 + self.offset[0], (h - dh) / 2 + self.offset[1]

    def _widget_to_display(self, x: float, y: float):
        scale, ox, oy = self._layout(self.get_width(), self.get_height())
        if scale <= 0:
            return 0.0, 0.0
        return (x - ox) / scale, (y - oy) / scale

    def _display_to_full(self, rect):
        if self.image is None or not self.full_size:
            return None
        fw, fh = self.full_size
        iw, ih = self.image.size
        kx, ky = fw / iw, fh / ih
        x, y, w, h = rect
        return (x * kx, y * ky, w * kx, h * ky)

    def _on_drag_begin(self, gesture, sx, sy):
        if self.crop_active:
            self._drag_start = self._widget_to_display(sx, sy)
            self.selection = (self._drag_start[0], self._drag_start[1], 0.0, 0.0)
        else:
            self._pan_start = (sx, sy)
            self._pan_offset = tuple(self.offset)

    def _on_drag_update(self, gesture, ox, oy):
        if self.crop_active and self._drag_start:
            scale, _gx, _gy = self._layout(self.get_width(), self.get_height())
            if scale <= 0:
                return
            x0, y0 = self._drag_start
            x1, y1 = x0 + ox / scale, y0 + oy / scale
            self.selection = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            self.queue_draw()
            if self.on_selection_changed:
                self.on_selection_changed(self._display_to_full(self.selection))
        elif self._pan_start is not None and self.zoom_mode == "free":
            dox, doy = gesture.get_offset()
            self.offset = [self._pan_offset[0] + dox, self._pan_offset[1] + doy]
            self.queue_draw()

    def _on_drag_end(self, gesture, ox, oy):
        self._drag_start = None
        self._pan_start = None
        if self.crop_active and self.selection:
            if self.selection[2] < 8 or self.selection[3] < 8:
                self.selection = None
                if self.on_selection_changed:
                    self.on_selection_changed(None)
            self.queue_draw()

    def _on_scroll(self, controller, dx, dy):
        if self.image is None or self.zoom_mode == "fit":
            return False
        self.zoom = max(0.1, min(8.0, self.zoom * (1.1 if dy < 0 else 1 / 1.1)))
        self.queue_draw()
        return True

    def _draw(self, area, cr, w, h):
        cr.set_source_rgb(0.16, 0.16, 0.17)
        cr.paint()
        if self.image is None:
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(15)
            text = "還沒有頁面 — 按「開始掃描」或開啟檔案"
            ext = cr.text_extents(text)
            cr.move_to((w - ext.width) / 2, h / 2)
            cr.show_text(text)
            return

        key = (id(self.image), self.image.size)
        if self._surface is None or self._surface_size != key:
            self._surface = pil_to_surface(self.image)
            self._surface_size = key

        scale, ox, oy = self._layout(w, h)
        iw, ih = self.image.size
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.rectangle(ox + 6, oy + 8, iw * scale, ih * scale)
        cr.fill()
        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)
        cr.set_source_surface(self._surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
        cr.paint()
        cr.restore()

        cr.set_source_rgba(1, 1, 1, 0.18)
        cr.set_line_width(1)
        cr.rectangle(ox + 0.5, oy + 0.5, iw * scale - 1, ih * scale - 1)
        cr.stroke()

        if self.crop_active:
            if self.selection and self.selection[2] > 1:
                sx, sy, sw, sh = self.selection
                cr.save()
                cr.set_source_rgba(0, 0, 0, 0.5)
                cr.rectangle(ox, oy, iw * scale, ih * scale)
                cr.rectangle(ox + sx * scale, oy + sy * scale, sw * scale, sh * scale)
                cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
                cr.fill()
                cr.set_fill_rule(cairo.FILL_RULE_WINDING)
                cr.set_source_rgba(0.31, 0.62, 1.0, 1.0)
                cr.set_line_width(2)
                cr.rectangle(ox + sx * scale, oy + sy * scale, sw * scale, sh * scale)
                cr.stroke()
                cr.restore()
            else:
                cr.set_source_rgba(1, 1, 1, 0.5)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(13)
                cr.move_to(ox, oy - 10)
                cr.show_text("在畫面上拖曳出要保留的範圍")


# ---------------------------------------------------------------------------
# 列印預覽畫布（列印分頁用）
# ---------------------------------------------------------------------------

class PrintPreviewCanvas(Gtk.DrawingArea):
    PAPER = {"A4": (210.0, 297.0), "Letter": (215.9, 279.4), "A5": (148.0, 210.0),
             "Legal": (215.9, 355.6), "A6": (105.0, 148.0), "B5": (182.0, 257.0),
             "EnvC5": (162.0, 229.0), "EnvDL": (110.0, 220.0)}

    def __init__(self):
        super().__init__()
        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.scroll_index = 0
        self.pages: list[Image.Image] = []
        self.paper = "A4"
        self.duplex = False
        self.current = 0
        scroll = Gtk.EventControllerScroll()
        scroll.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def set_pages(self, images: list[Image.Image], paper: str, duplex: bool, current: int = 0):
        self.pages = images
        self.paper = paper
        self.duplex = duplex
        self.current = current
        per = self._per_page()
        if self.scroll_index >= len(images):
            self.scroll_index = 0
        self.queue_draw()

    def _on_scroll(self, _c, _dx, dy):
        per = max(1, self._per_page())
        if len(self.pages) <= per:
            return False
        step = per if dy > 0 else -per
        self.scroll_index = max(0, min(self.scroll_index + step,
                                       max(0, len(self.pages) - per)))
        self.queue_draw()
        return True

    def _geometry(self, w: int, h: int):
        pw, ph = self.PAPER.get(self.paper, self.PAPER["A4"])
        aspect = ph / pw
        margin, gap, label_h = 12, 20, 16
        cols = max(1, int((w - 2 * margin + gap) // 240))
        sheet_w = (w - 2 * margin - (cols - 1) * gap) / cols
        sheet_h = sheet_w * aspect
        max_h = h - 2 * margin - label_h - gap
        if sheet_h > max_h:
            sheet_w *= max_h / sheet_h
            sheet_h = max_h
        rows = max(1, int((h - 2 * margin) // (sheet_h + label_h + gap)))
        return cols, sheet_w, sheet_h, rows, margin, gap, label_h

    def _per_page(self) -> int:
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return 1
        cols, _sw, _sh, rows, *_ = self._geometry(w, h)
        return cols * rows

    def _draw(self, area, cr, w, h):
        cr.set_source_rgb(0.16, 0.16, 0.17)
        cr.paint()
        if not self.pages:
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(15)
            text = "還沒有文件 — 先把檔案加進來，或回掃描分頁掃描"
            ext = cr.text_extents(text)
            cr.move_to((w - ext.width) / 2, h / 2)
            cr.show_text(text)
            return

        cols, sheet_w, sheet_h, rows, margin, gap, label_h = self._geometry(w, h)
        per_page = cols * rows
        start = max(0, min(self.scroll_index, max(0, len(self.pages) - 1)))
        shown = self.pages[start:start + per_page]
        for i, img in enumerate(shown):
            col, row = i % cols, i // cols
            x = margin + col * (sheet_w + gap)
            y = margin + row * (sheet_h + label_h + gap)
            self._draw_sheet(cr, x, y, sheet_w, sheet_h, img, start + i + 1, len(self.pages))

    def _draw_sheet(self, cr, x, y, sw, sh, img: Image.Image, number: int, total: int):
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.rectangle(x + 4, y + 6, sw, sh)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(x, y, sw, sh)
        cr.fill()

        margin = sw * 0.06
        avail_w, avail_h = sw - margin * 2, sh - margin * 2
        iw, ih = img.size
        scale = min(avail_w / iw, avail_h / ih)
        dw = iw * scale
        dx = x + (sw - dw) / 2
        dy = y + margin
        cr.save()
        cr.translate(dx, dy)
        cr.scale(scale, scale)
        cr.set_source_surface(pil_to_surface(img), 0, 0)
        cr.paint()
        cr.restore()

        if number - 1 == self.current:
            cr.set_source_rgba(0.31, 0.62, 1.0, 0.9)
            cr.set_line_width(2)
            cr.rectangle(x + 1, y + 1, sw - 2, sh - 2)
            cr.stroke()
        else:
            cr.set_source_rgba(0, 0, 0, 0.25)
            cr.set_line_width(1)
            cr.rectangle(x + 0.5, y + 0.5, sw - 1, sh - 1)
            cr.stroke()

        cr.set_source_rgba(0.86, 0.87, 0.90, 0.92)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(11)
        label = f"第 {number} / {total} 頁"
        ext = cr.text_extents(label)
        cr.move_to(x + (sw - ext.width) / 2, y + sh + 13)
        cr.show_text(label)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def icon_button(icon: str, tooltip: str, label: str | None = None) -> Gtk.Button:
    if label:
        btn = Gtk.Button(label=label)
    else:
        btn = Gtk.Button.new_from_icon_name(icon)
    btn.set_tooltip_text(tooltip)
    return btn


def make_dropdown(values: list[str], on_change=None, width: int = 150) -> Gtk.DropDown:
    dd = Gtk.DropDown.new_from_strings(values)
    dd.set_size_request(width, -1)
    if on_change:
        dd.connect("notify::selected", lambda *_a: on_change())
    return dd


def dd_value(dd: Gtk.DropDown) -> str:
    model = dd.get_model()
    idx = dd.get_selected()
    if model is None or idx == Gtk.INVALID_LIST_POSITION:
        return ""
    return model.get_string(idx)


def dd_select(dd: Gtk.DropDown, value: str) -> None:
    model = dd.get_model()
    if model is None:
        return
    for i in range(model.get_n_items()):
        if model.get_string(i) == value:
            dd.set_selected(i)
            return


def row(label: str, widget: Gtk.Widget) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    lbl = Gtk.Label(label=label, xalign=0)
    lbl.set_hexpand(True)
    box.append(lbl)
    box.append(widget)
    return box


def pair(l1: str, w1: Gtk.Widget, l2: str, w2: Gtk.Widget) -> Gtk.Grid:
    grid = Gtk.Grid(column_spacing=8, row_spacing=0)
    lbl1 = Gtk.Label(label=l1, xalign=0)
    lbl1.set_hexpand(True)
    grid.attach(lbl1, 0, 0, 1, 1)
    grid.attach(w1, 1, 0, 1, 1)
    grid.attach(Gtk.Label(label=l2, xalign=0), 2, 0, 1, 1)
    grid.attach(w2, 3, 0, 1, 1)
    return grid


def pair_switches(l1: str, s1: Gtk.Widget, l2: str, s2: Gtk.Widget) -> Gtk.Grid:
    for sw in (s1, s2):
        sw.set_halign(Gtk.Align.END)
    return pair(l1, s1, l2, s2)


def card(title: str) -> tuple[Gtk.Box, Gtk.Box]:
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    outer.add_css_class("card")
    head = Gtk.Label(label=title, xalign=0)
    head.add_css_class("card-title")
    outer.append(head)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    outer.append(body)
    return outer, body


def sidebar_scroller(box: Gtk.Box) -> Gtk.ScrolledWindow:
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(box)
    return scroll


def iter_children(box: Gtk.Box):
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        yield child
        child = nxt


# ---------------------------------------------------------------------------
# 首頁
# ---------------------------------------------------------------------------

class HomePage(Gtk.Box):
    def __init__(self, win: "StudioWindow"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.win = win
        self.set_margin_top(40)
        self.set_margin_bottom(40)
        self.set_margin_start(40)
        self.set_margin_end(40)

        title = Gtk.Label(label="Brother Studio", xalign=0)
        title.add_css_class("home-hero")
        sub = Gtk.Label(label="先選要做的事 — 掃描與列印是兩條獨立的工作流", xalign=0)
        sub.add_css_class("home-sub")
        self.doc_hint = Gtk.Label(label="", xalign=0)
        self.doc_hint.add_css_class("step-hint")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head.append(title)
        head.append(sub)
        head.append(self.doc_hint)
        self.append(head)

        tiles = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        tiles.set_homogeneous(True)
        self.scan_tile = self._tile(
            "scanner-symbolic", "掃描文件",
            "設定解析度、色彩與進紙來源，掃進來直接編輯裁切，\n再存檔或送去列印。",
            lambda: win.show_page("scan"),
        )
        self.print_tile = self._tile(
            "printer-symbolic", "列印文件",
            "開啟 PDF 或圖片，設定紙張、雙面、份數，\n先看列印預覽再送印。",
            lambda: win.show_page("print"),
        )
        self.doc_tile = self._tile(
            "document-open-symbolic", "開啟現有檔案",
            "直接編輯或列印手邊的 PDF、圖片（會帶到列印分頁）。",
            lambda: win.open_files(),
        )
        self.resume_tile = self._tile(
            "go-next-symbolic", "回到目前文件",
            "繼續編輯或列印剛才掃描／開啟的文件。",
            lambda: win.show_page(win.last_work_page),
        )
        for t in (self.scan_tile, self.print_tile, self.doc_tile, self.resume_tile):
            tiles.append(t)
        self.append(tiles)

        hint = Gtk.Label(
            label="提示：Ctrl+N 開始掃描 · Ctrl+P 列印 · 掃描完成後可用「送到列印」把同一份文件帶過去",
            xalign=0,
        )
        hint.add_css_class("step-hint")
        self.append(hint)

        self.set_valign(Gtk.Align.CENTER)

    def sync(self, doc):
        n = len(doc)
        self.doc_hint.set_text(f"目前文件：{n} 頁（尚未儲存）" if n else "")
        self.resume_tile.set_visible(bool(n))
        lbl = self.resume_tile.get_child().get_last_child()
        # 磚塊內容是 Box: [icon, 標題, 說明]
        box = self.resume_tile.get_child()
        child = box.get_first_child()
        texts = []
        while child is not None:
            if isinstance(child, Gtk.Label):
                texts.append(child)
            child = child.get_next_sibling()
        if texts:
            texts[-1].set_text(f"繼續編輯或列印這 {n} 頁的文件。")

    def _tile(self, icon: str, title: str, desc: str, on_click) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("tile")
        btn.set_hexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(44)
        img.set_halign(Gtk.Align.START)
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.add_css_class("tile-title")
        d = Gtk.Label(label=desc, xalign=0)
        d.add_css_class("tile-desc")
        d.set_wrap(True)
        box.append(img)
        box.append(lbl)
        box.append(d)
        btn.set_child(box)
        btn.connect("clicked", lambda *_: on_click())
        return btn


# ---------------------------------------------------------------------------
# 掃描分頁
# ---------------------------------------------------------------------------

class ScanPage(Gtk.Box):
    def __init__(self, win: "StudioWindow"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.win = win
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(330)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_start_child(self._build_sidebar())
        paned.set_end_child(self._build_main())
        self.append(paned)

    # -- 左側：掃描設定 ------------------------------------------------
    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        win = self.win

        c1, b1 = card("① 掃描設定")
        self.device_dd = make_dropdown([], on_change=win.on_device_changed, width=176)
        dev_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        dev_row.append(self.device_dd)
        rescan = icon_button("view-refresh-symbolic", "重新偵測掃描器")
        rescan.connect("clicked", lambda *_: win.discover_devices())
        dev_row.append(rescan)
        b1.append(row("掃描器", dev_row))

        self.mode_dd = make_dropdown(["彩色", "灰階", "黑白"], win.save_ui_state)
        b1.append(row("色彩", self.mode_dd))
        self.res_dd = make_dropdown(["100", "150", "200", "300", "400", "600", "1200"],
                                    win.save_ui_state)
        b1.append(row("解析度 dpi", self.res_dd))
        self.source_dd = make_dropdown(["平台玻璃", "ADF 單面", "ADF 雙面"],
                                       self._on_source_changed)
        b1.append(row("進紙來源", self.source_dd))
        self.adf_switch = Gtk.Switch()
        self.adf_switch.set_halign(Gtk.Align.END)
        self.adf_switch.connect("notify::active", lambda *_: win.save_ui_state())
        b1.append(row("ADF 多頁掃描", self.adf_switch))
        self.scan_btn = Gtk.Button(label="開始掃描")
        self.scan_btn.add_css_class("suggested-action")
        self.scan_btn.connect("clicked", lambda *_: win.start_scan())
        b1.append(self.scan_btn)
        box.append(c1)

        c2, b2 = card("② 掃描後動作")
        self.action_dd = make_dropdown(
            ["加入文件（可編輯）", "存成 PDF 到資料夾", "存成 PNG 到資料夾", "直接列印"],
            self._on_action_changed,
        )
        b2.append(row("動作", self.action_dd))
        self.outdir_btn = Gtk.Button(label="…")
        self.outdir_btn.set_tooltip_text("選擇輸出資料夾")
        self.outdir_btn.connect("clicked", lambda *_: win.choose_output_dir())
        outdir_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.outdir_label = Gtk.Label(xalign=0, ellipsize=3)
        self.outdir_label.add_css_class("dim-label")
        self.outdir_label.set_hexpand(True)
        outdir_row.append(self.outdir_label)
        outdir_row.append(self.outdir_btn)
        self.outdir_row = row("資料夾", outdir_row)
        b2.append(self.outdir_row)
        self.prefix_entry = Gtk.Entry()
        self.prefix_entry.set_width_chars(12)
        self.prefix_entry.connect("changed", lambda *_: win.save_ui_state())
        self.prefix_row = row("檔名前綴", self.prefix_entry)
        b2.append(self.prefix_row)
        self.autocrop_switch = Gtk.Switch()
        self.autocrop_switch.set_halign(Gtk.Align.END)
        self.autocrop_switch.connect("notify::active", lambda *_: win.save_ui_state())
        b2.append(row("自動裁白邊", self.autocrop_switch))
        self.enhance_switch = Gtk.Switch()
        self.enhance_switch.set_halign(Gtk.Align.END)
        self.enhance_switch.connect("notify::active", lambda *_: win.save_ui_state())
        b2.append(row("自動去底色", self.enhance_switch))
        box.append(c2)

        c3, b3 = card("③ 下一步")
        self.doc_label = Gtk.Label(label="目前沒有頁面", xalign=0)
        self.doc_label.add_css_class("dim-label")
        b3.append(self.doc_label)
        self.to_print_btn = Gtk.Button(label="送到列印分頁")
        self.to_print_btn.set_tooltip_text("把這份文件帶到列印分頁")
        self.to_print_btn.connect("clicked", lambda *_: self._goto_print())
        b3.append(self.to_print_btn)
        self.save_pdf_btn = Gtk.Button(label="儲存 PDF…")
        self.save_pdf_btn.connect("clicked", lambda *_: win.choose_save_pdf())
        b3.append(self.save_pdf_btn)
        box.append(c3)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        box.append(spacer)
        return sidebar_scroller(box)

    def _goto_print(self):
        self.win.show_page("print")
        self.win.toast(f"文件已帶到列印分頁（{len(self.win.doc)} 頁）")

    def _on_source_changed(self):
        self.adf_switch.set_active(dd_value(self.source_dd).startswith("ADF"))
        self.win.save_ui_state()

    def _on_action_changed(self):
        needs_dir = dd_value(self.action_dd) in ("存成 PDF 到資料夾", "存成 PNG 到資料夾")
        self.outdir_row.set_visible(needs_dir)
        self.prefix_row.set_visible(needs_dir)
        self.win.save_ui_state()

    # -- 右側：編輯器 --------------------------------------------------
    def _build_main(self) -> Gtk.Widget:
        win = self.win
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.add_css_class("toolbar-row")

        self.prev_btn = icon_button("go-previous-symbolic", "上一頁")
        self.prev_btn.connect("clicked", lambda *_: win.goto_page(win.doc.index - 1))
        self.page_label = Gtk.Label(label="0 / 0")
        self.page_label.set_width_chars(9)
        self.next_btn = icon_button("go-next-symbolic", "下一頁")
        self.next_btn.connect("clicked", lambda *_: win.goto_page(win.doc.index + 1))
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav.add_css_class("linked")
        for w in (self.prev_btn, self.page_label, self.next_btn):
            nav.append(w)
        bar.append(nav)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.rot_l = icon_button("object-rotate-left-symbolic", "向左旋轉（R）")
        self.rot_l.connect("clicked", lambda *_: win.edit(lambda: win.doc.rotate(90)))
        self.rot_r = icon_button("object-rotate-right-symbolic", "向右旋轉（Shift+R）")
        self.rot_r.connect("clicked", lambda *_: win.edit(lambda: win.doc.rotate(-90)))
        self.flip_b = icon_button("object-flip-horizontal-symbolic", "水平翻轉")
        self.flip_b.connect("clicked", lambda *_: win.edit(win.doc.flip))
        self.crop_toggle = Gtk.ToggleButton(label="裁切")
        self.crop_toggle.set_tooltip_text("拖曳選取要保留的範圍")
        self.crop_toggle.connect("toggled", lambda *_: self._on_crop_toggled())
        edit_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        edit_group.add_css_class("linked")
        for w in (self.rot_l, self.rot_r, self.flip_b, self.crop_toggle):
            edit_group.append(w)
        bar.append(edit_group)

        self.adjust_btn = Gtk.MenuButton(label="調整")
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pbox.set_margin_top(10)
        pbox.set_margin_bottom(10)
        pbox.set_margin_start(10)
        pbox.set_margin_end(10)
        self.bright_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -50, 50, 1)
        self.bright_scale.set_draw_value(True)
        self.bright_scale.set_size_request(220, -1)
        self._bright_sig = self.bright_scale.connect("value-changed", self._on_scale_changed)
        self.contrast_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -50, 50, 1)
        self.contrast_scale.set_draw_value(True)
        self.contrast_scale.set_size_request(220, -1)
        self._contrast_sig = self.contrast_scale.connect("value-changed", self._on_scale_changed)
        pbox.append(row("亮度", self.bright_scale))
        pbox.append(row("對比", self.contrast_scale))
        reset_adj = Gtk.Button(label="重設目前頁")
        reset_adj.connect("clicked", lambda *_: self._reset_adjust())
        pbox.append(reset_adj)
        pop.set_child(pbox)
        self.adjust_btn.set_popover(pop)
        bar.append(self.adjust_btn)

        self.gray_b = Gtk.Button(label="灰階")
        self.gray_b.connect("clicked", lambda *_: win.edit(win.doc.grayscale))
        self.bw_b = Gtk.Button(label="黑白（二值化）")
        self.bw_b.connect("clicked", lambda *_: win.edit(lambda: win.doc.binarize(160)))
        self.white_b = Gtk.Button(label="去底色")
        self.white_b.connect("clicked", lambda *_: win.edit(win.doc.whiten))
        self.auto_crop_b = Gtk.Button(label="自動裁切白邊")
        self.auto_crop_b.connect("clicked", lambda *_: win.edit(win.doc.autocrop))
        self.deskew_b = Gtk.Button(label="校正歪斜")
        self.deskew_b.connect("clicked", lambda *_: win.edit(win.doc.deskew))
        enh_pop = Gtk.Popover()
        enh_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        enh_box.set_margin_top(8)
        enh_box.set_margin_bottom(8)
        enh_box.set_margin_start(8)
        enh_box.set_margin_end(8)
        for w in (self.gray_b, self.bw_b, self.white_b, self.auto_crop_b, self.deskew_b):
            w.add_css_class("flat")
            enh_box.append(w)
        enh_pop.set_child(enh_box)
        self.enh_btn = Gtk.MenuButton(label="增強")
        self.enh_btn.set_tooltip_text("灰階 / 黑白 / 去底色 / 自動裁切 / 校正歪斜")
        self.enh_btn.set_popover(enh_pop)
        bar.append(self.enh_btn)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.del_b = icon_button("edit-delete-symbolic", "刪除本頁（Delete）")
        self.del_b.connect("clicked", lambda *_: win.edit(win.doc.delete_current))
        self.dup_b = icon_button("edit-copy-symbolic", "複製本頁")
        self.dup_b.connect("clicked", lambda *_: win.edit(win.doc.duplicate_current))
        self.up_b = icon_button("go-up-symbolic", "往前移一頁")
        self.up_b.connect("clicked", lambda *_: win.edit(lambda: win.doc.move(-1)))
        self.down_b = icon_button("go-down-symbolic", "往後移一頁")
        self.down_b.connect("clicked", lambda *_: win.edit(lambda: win.doc.move(1)))
        page_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        page_group.add_css_class("linked")
        for w in (self.del_b, self.dup_b, self.up_b, self.down_b):
            page_group.append(w)
        bar.append(page_group)

        self.undo_b = icon_button("edit-undo-symbolic", "還原（Ctrl+Z）")
        self.undo_b.connect("clicked", lambda *_: win.undo())
        self.redo_b = icon_button("edit-redo-symbolic", "重做（Ctrl+Shift+Z）")
        self.redo_b.connect("clicked", lambda *_: win.redo())
        undo_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        undo_group.add_css_class("linked")
        undo_group.append(self.undo_b)
        undo_group.append(self.redo_b)
        bar.append(undo_group)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)
        self.zoom_dd = make_dropdown(["適合視窗", "50%", "100%", "150%", "200%"],
                                     self._on_zoom_changed, width=110)
        bar.append(self.zoom_dd)

        bar_scroll = Gtk.ScrolledWindow()
        bar_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        bar_scroll.set_child(bar)
        bar_scroll.set_propagate_natural_height(True)
        outer.append(bar_scroll)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_box.add_css_class("preview-frame")
        self.canvas = PreviewCanvas(on_selection_changed=self._on_crop_selection)
        preview_box.append(self.canvas)

        self.crop_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.crop_bar.add_css_class("crop-bar")
        self.crop_bar.set_halign(Gtk.Align.CENTER)
        self.crop_bar.set_valign(Gtk.Align.END)
        self.crop_bar.set_hexpand(False)
        self.crop_bar.set_vexpand(False)
        self.crop_bar.set_margin_bottom(16)
        self.crop_info = Gtk.Label(label="拖曳選取範圍")
        self.crop_bar.append(self.crop_info)
        apply_crop = Gtk.Button(label="套用裁切")
        apply_crop.add_css_class("suggested-action")
        apply_crop.connect("clicked", lambda *_: self.apply_crop())
        cancel_crop = Gtk.Button(label="取消")
        cancel_crop.connect("clicked", lambda *_: self.exit_crop_mode())
        self.crop_bar.append(apply_crop)
        self.crop_bar.append(cancel_crop)
        self.crop_bar.set_visible(False)

        overlay = Gtk.Overlay()
        overlay.set_child(preview_box)
        overlay.add_overlay(self.crop_bar)
        overlay.set_vexpand(True)
        outer.append(overlay)

        self.thumb_scroll = Gtk.ScrolledWindow()
        self.thumb_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.thumb_scroll.set_size_request(-1, 128)
        self.thumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.thumb_box.set_margin_top(8)
        self.thumb_box.set_margin_bottom(8)
        self.thumb_box.set_margin_start(10)
        self.thumb_box.set_margin_end(10)
        self.thumb_scroll.set_child(self.thumb_box)
        outer.append(self.thumb_scroll)
        return outer

    # -- 裁切 ---------------------------------------------------------
    def _on_crop_toggled(self):
        active = self.crop_toggle.get_active()
        self.canvas.set_crop_active(active)
        self.crop_bar.set_visible(active)

    def exit_crop_mode(self):
        if self.crop_toggle.get_active():
            self.crop_toggle.set_active(False)
        self.canvas.set_crop_active(False)
        self.crop_bar.set_visible(False)

    def _on_crop_selection(self, rect):
        self._crop_rect = rect
        if rect and rect[2] > 4 and rect[3] > 4:
            self.crop_info.set_text(f"選取 {int(rect[2])} × {int(rect[3])} px")
        else:
            self.crop_info.set_text("拖曳選取範圍")

    def apply_crop(self):
        rect = getattr(self, "_crop_rect", None)
        if not rect or rect[2] < 8 or rect[3] < 8:
            self.win.toast("請先在畫面上拖曳選取範圍")
            return
        self.exit_crop_mode()
        self.win.edit(lambda: self.win.doc.crop(rect))

    # -- 調整 ---------------------------------------------------------
    def _on_scale_changed(self, _scale):
        self.win.schedule_adjust()

    def _reset_adjust(self):
        self.bright_scale.set_value(0)
        self.contrast_scale.set_value(0)
        self.win.doc.set_adjust(0, 0)
        self.win.refresh_canvas()

    def sync_adjust(self, page):
        self.bright_scale.handler_block(self._bright_sig)
        self.contrast_scale.handler_block(self._contrast_sig)
        self.bright_scale.set_value(page.brightness if page else 0)
        self.contrast_scale.set_value(page.contrast if page else 0)
        self.bright_scale.handler_unblock(self._bright_sig)
        self.contrast_scale.handler_unblock(self._contrast_sig)

    def _on_zoom_changed(self):
        value = dd_value(self.zoom_dd)
        if value == "適合視窗" or not value:
            self.canvas.set_zoom("fit")
        else:
            try:
                self.canvas.set_zoom("free", float(value.strip("%")) / 100.0)
            except ValueError:
                self.canvas.set_zoom("fit")

    # -- 由視窗呼叫的更新 ---------------------------------------------
    def refresh(self):
        win = self.win
        page = win.doc.current
        self.canvas.set_page(page.rendered(), page.size) if page else self.canvas.set_page(None)
        self.sync_adjust(page)
        self.page_label.set_text(
            f"{win.doc.index + 1} / {len(win.doc)}" if win.doc.pages else "0 / 0"
        )
        self._refresh_thumbs()
        self._refresh_buttons()
        self.doc_label.set_text(
            f"目前文件：{len(win.doc)} 頁" if win.doc.pages else "目前沒有頁面"
        )
        self.to_print_btn.set_sensitive(bool(win.doc.pages))
        self.save_pdf_btn.set_sensitive(bool(win.doc.pages))

    def _refresh_thumbs(self):
        for child in list(iter_children(self.thumb_box)):
            self.thumb_box.remove(child)
        for i, page in enumerate(self.win.doc.pages):
            btn = Gtk.Button()
            btn.add_css_class("thumb")
            if i == self.win.doc.index:
                btn.add_css_class("thumb-selected")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            pic = Gtk.Picture()
            pic.set_paintable(pil_to_texture(page.thumbnail(92)))
            pic.set_size_request(80, 80)
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
            box.append(pic)
            num = Gtk.Label(label=str(i + 1))
            num.add_css_class("page-badge")
            box.append(num)
            btn.set_child(box)
            btn.connect("clicked", lambda _b, idx=i: self.win.goto_page(idx))
            self.thumb_box.append(btn)

    def _refresh_buttons(self):
        has = bool(self.win.doc.pages)
        for w in (self.del_b, self.dup_b, self.up_b, self.down_b, self.rot_l, self.rot_r,
                  self.flip_b, self.gray_b, self.bw_b, self.white_b, self.auto_crop_b,
                  self.deskew_b, self.crop_toggle, self.adjust_btn, self.enh_btn):
            w.set_sensitive(has)
        self.prev_btn.set_sensitive(has and self.win.doc.index > 0)
        self.next_btn.set_sensitive(has and self.win.doc.index < len(self.win.doc) - 1)
        self.undo_b.set_sensitive(bool(self.win.doc._undo))
        self.redo_b.set_sensitive(bool(self.win.doc._redo))


# ---------------------------------------------------------------------------
# 列印分頁
# ---------------------------------------------------------------------------

class PrintPage(Gtk.Box):
    def __init__(self, win: "StudioWindow"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.win = win
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(330)
        paned.set_resize_start_child(False)
        paned.set_shrink_start_child(False)
        paned.set_start_child(self._build_sidebar())
        paned.set_end_child(self._build_main())
        self.append(paned)

    def _build_sidebar(self) -> Gtk.Widget:
        win = self.win
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        c1, b1 = card("① 要列印的文件")
        self.file_label = Gtk.Label(label="尚未加入文件", xalign=0)
        self.file_label.set_wrap(True)
        self.file_label.add_css_class("dim-label")
        b1.append(self.file_label)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.open_btn = Gtk.Button(label="選擇檔案…")
        self.open_btn.connect("clicked", lambda *_: win.open_files())
        self.clear_btn = Gtk.Button(label="清除")
        self.clear_btn.connect("clicked", lambda *_: win.clear_document())
        btns.append(self.open_btn)
        btns.append(self.clear_btn)
        b1.append(btns)
        self.to_scan_btn = Gtk.Button(label="回掃描分頁")
        self.to_scan_btn.connect("clicked", lambda *_: win.show_page("scan"))
        b1.append(self.to_scan_btn)
        box.append(c1)

        c2, b2 = card("② 列印設定")
        self.printer_dd = make_dropdown([], on_change=win.on_printer_changed, width=176)
        b2.append(row("印表機", self.printer_dd))
        self.paper_dd = make_dropdown(C.PAPER_CHOICES, self._on_setting_changed, width=100)
        self.duplex_dd = make_dropdown(list(C.DUPLEX_CHOICES), self._on_setting_changed, width=100)
        b2.append(pair("紙張", self.paper_dd, "雙面", self.duplex_dd))
        self.color_dd = make_dropdown(list(C.COLOR_CHOICES), self._on_setting_changed, width=100)
        self.quality_dd = make_dropdown(list(C.QUALITY_CHOICES), self._on_setting_changed, width=100)
        b2.append(pair("色彩", self.color_dd, "品質", self.quality_dd))
        self.papertype_dd = make_dropdown(C.PAPER_TYPE_CHOICES, self._on_setting_changed, width=100)
        self.copies_spin = Gtk.SpinButton.new_with_range(1, 99, 1)
        self.copies_spin.connect("value-changed", lambda *_: self._on_setting_changed())
        b2.append(pair("紙張類型", self.papertype_dd, "份數", self.copies_spin))
        self.fit_switch = Gtk.Switch()
        self.fit_switch.set_active(True)
        self.toner_switch = Gtk.Switch()
        for sw in (self.fit_switch, self.toner_switch):
            sw.connect("notify::active", lambda *_: self._on_setting_changed())
        b2.append(pair_switches("縮放符合頁面", self.fit_switch, "省碳粉", self.toner_switch))
        box.append(c2)

        c3, b3 = card("③ 送印")
        self.summary_label = Gtk.Label(label="", xalign=0)
        self.summary_label.add_css_class("dim-label")
        self.summary_label.set_wrap(True)
        b3.append(self.summary_label)
        self.print_btn = Gtk.Button(label="列印")
        self.print_btn.add_css_class("suggested-action")
        self.print_btn.connect("clicked", lambda *_: win.do_print())
        b3.append(self.print_btn)
        self.queue_label = Gtk.Label(xalign=0)
        self.queue_label.add_css_class("dim-label")
        self.queue_label.set_wrap(True)
        b3.append(self.queue_label)
        box.append(c3)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        box.append(spacer)
        return sidebar_scroller(box)

    def _build_main(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("toolbar-row")
        self.info_label = Gtk.Label(label="", xalign=0)
        self.info_label.set_hexpand(True)
        bar.append(self.info_label)
        self.open_main_btn = Gtk.Button(label="加入檔案…")
        self.open_main_btn.connect("clicked", lambda *_: self.win.open_files())
        bar.append(self.open_main_btn)
        outer.append(bar)

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("preview-frame")
        frame.set_vexpand(True)
        self.canvas = PrintPreviewCanvas()
        frame.append(self.canvas)
        outer.append(frame)
        return outer

    def _on_setting_changed(self):
        self.win.save_ui_state()
        self.win.refresh_print()

    # -- 由視窗呼叫 ---------------------------------------------------
    def refresh(self):
        win = self.win
        n = len(win.doc)
        if n:
            self.file_label.set_text(f"目前文件：{n} 頁（第 {win.doc.index + 1} 頁）")
        else:
            self.file_label.set_text("尚未加入文件 — 可以選擇檔案，或回掃描分頁掃描")
        paper = dd_value(self.paper_dd) or "A4"
        duplex = dd_value(self.duplex_dd)
        duplex_text = "單面" if duplex == "關閉" else duplex
        self.info_label.set_text(
            f"共 {n} 頁 · {paper} · {duplex_text} · {dd_value(self.color_dd)}" if n else "沒有內容"
        )
        self.summary_label.set_text(
            f"共 {n} 頁，{self.copies_spin.get_value_as_int()} 份，{paper}，{duplex_text}"
            if n else "沒有內容可列印"
        )
        self.print_btn.set_sensitive(bool(n))
        self.clear_btn.set_sensitive(bool(n))
        self.canvas.set_pages(
            [p.rendered() for p in win.doc.pages], paper, duplex != "關閉", win.doc.index
        )


# ---------------------------------------------------------------------------
# 主視窗：分頁切換與共用邏輯
# ---------------------------------------------------------------------------

class StudioWindow(Adw.ApplicationWindow):
    PAGES = {"scan": "掃描", "print": "列印"}

    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Brother Studio")
        self.set_default_size(1360, 880)
        self.doc = C.Document()
        self.cfg = C.load_config()
        self.devices: list[C.Device] = []
        self.caps: C.ScanCaps | None = None
        self.device: C.Device | None = None
        self._busy_count = 0
        self._adjust_timer = 0
        self.current_page = "home"
        self.last_work_page = "scan"

        self._build_ui()
        self.home.scan_tile.grab_focus()
        self.show_page("home")
        self._load_printers()
        self.refresh_all()
        self.home.sync(self.doc)
        GLib.idle_add(self.discover_devices)

    # -- 外殼 ---------------------------------------------------------
    def _build_ui(self):
        overlay = Adw.ToastOverlay()
        toolbar_view = Adw.ToolbarView()
        overlay.set_child(toolbar_view)

        header = Adw.HeaderBar()
        self.home_btn = icon_button("go-home-symbolic", "回首頁")
        self.home_btn.connect("clicked", lambda *_: self.show_page("home"))
        header.pack_start(self.home_btn)

        self.open_btn = icon_button("document-open-symbolic", "開啟檔案（Ctrl+O）")
        self.open_btn.connect("clicked", lambda *_: self.open_files())
        header.pack_start(self.open_btn)

        self.scan_action = Gtk.Button(label="開始掃描")
        self.scan_action.add_css_class("suggested-action")
        self.scan_action.connect("clicked", lambda *_: self.start_scan())
        header.pack_end(self.scan_action)

        self.print_action = Gtk.Button(label="列印")
        self.print_action.add_css_class("suggested-action")
        self.print_action.connect("clicked", lambda *_: self.do_print())
        header.pack_end(self.print_action)

        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.set_menu_model(self._build_menu())
        header.pack_end(self.menu_btn)
        toolbar_view.add_top_bar(header)
        self.header = header

        # 首頁 / 工作區
        self.home = HomePage(self)
        self.scan_page = ScanPage(self)
        self.print_page = PrintPage(self)

        self.mode_stack = Adw.ViewStack()
        self.scan_stack_page = self.mode_stack.add_titled_with_icon(
            self.scan_page, "scan", "掃描", "scanner-symbolic")
        self.print_stack_page = self.mode_stack.add_titled_with_icon(
            self.print_page, "print", "列印", "printer-symbolic")

        self.view_switcher = Adw.ViewSwitcher()
        self.view_switcher.set_stack(self.mode_stack)
        self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)

        self.title_home = Adw.WindowTitle(title="Brother Studio", subtitle="選擇要做的事")
        self.title_work = Adw.WindowTitle(title="Brother Studio", subtitle="掃描 · 編輯 · 列印")

        self.stack = Gtk.Stack()          # outer：home / work
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.home, "home")
        self.stack.add_named(self.mode_stack, "work")
        toolbar_view.set_content(self.stack)
        toolbar_view.add_bottom_bar(self._build_statusbar())

        self.toasts = overlay
        self.set_content(overlay)

        # 切換分頁時同步標題與按鈕
        self.mode_stack.connect("notify::visible-child-name", lambda *_: self._sync_chrome())
        self.mode_stack.connect("notify::visible-child", lambda *_: self._sync_chrome())

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key_pressed)
        self.add_controller(keys)

        app = self.get_application()
        for name, accels in {
            "home": ["<Control>1"],
            "go-scan": ["<Control>2"],
            "go-print": ["<Control>3"],
            "open": ["<Control>o"],
            "save-pdf": ["<Control>s"],
            "save-images": ["<Control><Shift>s"],
            "print": ["<Control>p"],
            "scan": ["<Control>n"],
            "undo": ["<Control>z"],
            "redo": ["<Control><Shift>z"],
        }.items():
            app.set_accels_for_action(f"win.{name}", accels)
        for name, handler in {
            "home": lambda *_: self.show_page("home"),
            "go-scan": lambda *_: self.show_page("scan"),
            "go-print": lambda *_: self.show_page("print"),
            "open": lambda *_: self.open_files(),
            "save-pdf": lambda *_: self.choose_save_pdf(),
            "save-images": lambda *_: self.choose_save_images(),
            "print": lambda *_: self.show_page("print"),
            "scan": lambda *_: (self.show_page("scan"), self.start_scan()),
            "undo": lambda *_: self.undo(),
            "redo": lambda *_: self.redo(),
            "about": lambda *_: self.show_about(),
            "clear": lambda *_: self.clear_document(),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def _build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("回首頁", "win.home")
        s1.append("回到掃描分頁", "win.go-scan")
        s1.append("回到列印分頁", "win.go-print")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("開啟檔案…", "win.open")
        s2.append("儲存 PDF…", "win.save-pdf")
        s2.append("儲存圖片…", "win.save-images")
        menu.append_section(None, s2)
        s3 = Gio.Menu()
        s3.append("清除目前文件", "win.clear")
        menu.append_section(None, s3)
        menu.append("關於 Brother Studio", "win.about")
        return menu

    def _build_statusbar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("statusbar")
        self.status_label = Gtk.Label(label="就緒", xalign=0)
        self.status_label.set_hexpand(True)
        self.status_label.set_ellipsize(3)
        bar.append(self.status_label)
        self.progress = Gtk.ProgressBar()
        self.progress.set_size_request(180, -1)
        self.progress.set_visible(False)
        bar.append(self.progress)
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        bar.append(self.spinner)
        return bar

    # -- 分頁切換 -----------------------------------------------------
    def show_page(self, name: str):
        if name == "home":
            self.stack.set_visible_child_name("home")
            self.current_page = "home"
        else:
            self.stack.set_visible_child_name("work")
            self.mode_stack.set_visible_child_name(name)
            self.current_page = name
            self.last_work_page = name
            if name == "scan":
                self.scan_page.exit_crop_mode()
                self.refresh_scan()
            else:
                self.refresh_print()
        self._sync_chrome()

    def _sync_chrome(self):
        on_home = self.current_page == "home"
        self.home_btn.set_visible(not on_home)
        self.open_btn.set_visible(not on_home)
        self.scan_action.set_visible(self.current_page == "scan")
        self.print_action.set_visible(self.current_page == "print")
        if on_home:
            self.header.set_title_widget(self.title_home)
        else:
            self.header.set_title_widget(self.view_switcher)

    # -- 文件變更後的統一更新 ------------------------------------------
    def refresh_all(self):
        self.refresh_scan()
        self.refresh_print()
        self.home.sync(self.doc)

    def refresh_scan(self):
        self.scan_page.refresh()

    def refresh_print(self):
        self.print_page.refresh()

    def refresh_canvas(self):
        page = self.doc.current
        self.scan_page.canvas.set_page(page.rendered(), page.size) if page else \
            self.scan_page.canvas.set_page(None)

    # -- 掃描器 -------------------------------------------------------
    def discover_devices(self):
        self.status("正在偵測掃描器…")
        self.busy_start()
        cached = self.cfg.get("devices_cache", []) or []
        if cached:
            self.populate_devices(cached, discover=False)

        def work():
            devices = C.list_devices()
            has_usb = any(d.kind == "usb" for d in devices)
            if not has_usb:
                cache = C.load_config().get("device_usb", "")
                if cache:
                    devices.append(C.Device(cache, "Brother MFC-L8390CDW（USB · 平台玻璃）", "usb"))
                else:
                    usb = C.probe_usb_device()
                    if usb:
                        devices.append(usb)
            return devices

        def done(devices, err):
            self.busy_end()
            if err:
                self.status("偵測掃描器失敗：" + str(err))
                return
            self.populate_devices(devices)
            self.save_devices_cache(devices)
            self.status(f"找到 {len(devices)} 台掃描器")

        self._async(work, done)

    def populate_devices(self, devices, discover: bool = True):
        items: list[C.Device] = []
        for d in devices:
            if isinstance(d, C.Device):
                items.append(d)
            elif isinstance(d, dict) and d.get("key"):
                label = d.get("label", d["key"])
                for dup in ("（USB · USB）", "（網路 · 網路）"):
                    label = label.replace(dup, dup.split(" · ")[0] + "）")
                items.append(C.Device(d["key"], label, d.get("kind", "other")))
        if not items:
            self.scan_page.device_dd.set_model(Gtk.StringList.new(["（偵測中…）"]))
            return
        self.devices = items
        self.scan_page.device_dd.set_model(Gtk.StringList.new([d.label for d in items]))
        last = self.cfg.get("device_last", "")
        for i, d in enumerate(items):
            if d.key == last:
                self.scan_page.device_dd.set_selected(i)
                break
        if discover:
            self.on_device_changed()

    def save_devices_cache(self, devices):
        cfg = C.load_config()
        cfg["devices_cache"] = [{"key": d.key, "label": d.label, "kind": d.kind} for d in devices]
        if devices:
            usb = [d for d in devices if d.kind == "usb"]
            if usb:
                cfg["device_usb"] = usb[0].key
        C.save_config(cfg)
        self.cfg = cfg

    def current_device(self) -> C.Device | None:
        idx = self.scan_page.device_dd.get_selected()
        if 0 <= idx < len(self.devices):
            return self.devices[idx]
        return None

    def on_device_changed(self):
        dev = self.current_device()
        if dev is None:
            return
        if getattr(self, "_caps_for", None) == dev.key:
            return
        self._caps_for = dev.key
        self.device = dev
        cfg = C.load_config()
        cfg["device_last"] = dev.key
        C.save_config(cfg)
        self.cfg = cfg
        self.status(f"讀取掃描能力：{dev.key}")
        self.busy_start()
        dd = self.scan_page

        def work():
            return C.scan_caps(dev.key)

        def done(caps, err):
            self.busy_end()
            if err or caps is None:
                self._caps_for = None
                self.status("讀不到掃描器能力，使用預設值")
                return
            self.caps = caps
            if caps.resolutions:
                dd.res_dd.set_model(Gtk.StringList.new([str(r) for r in caps.resolutions]))
                dd_select(dd.res_dd, str(caps.default_resolution))
            options = ["平台玻璃"]
            if any("Document Feeder" in s for s in caps.sources):
                options += ["ADF 單面", "ADF 雙面"]
            dd.source_dd.set_model(Gtk.StringList.new(options))
            self.status("掃描器就緒")

        self._async(work, done)

    # -- 掃描 ---------------------------------------------------------
    def start_scan(self):
        dev = self.current_device()
        if dev is None:
            self.toast("沒有可用的掃描器")
            return
        dd = self.scan_page
        mode = dd_value(dd.mode_dd) or "彩色"
        try:
            resolution = int(dd_value(dd.res_dd) or 300)
        except ValueError:
            resolution = 300
        source = dd_value(dd.source_dd) or "平台玻璃"
        adf = dd.adf_switch.get_active()
        auto_crop = dd.autocrop_switch.get_active()
        auto_enhance = dd.enhance_switch.get_active()

        self.busy_start()
        self.progress.set_visible(True)
        self.progress.pulse()
        self.status(f"掃描中…（{mode} {resolution}dpi {'ADF' if adf else '平台'}）")

        def progress(value, message):
            GLib.idle_add(self._scan_progress, value, message)

        def work():
            return C.scan_to_document(
                self.doc, dev.key, mode=mode, resolution=resolution, source=source,
                adf=adf, auto_crop=auto_crop, auto_enhance=auto_enhance,
                on_progress=progress,
            )

        def done(pages, err):
            self.progress.set_visible(False)
            self.busy_end()
            if err:
                self.status("掃描失敗：" + str(err))
                self.toast("掃描失敗：" + str(err))
                return
            self.show_page("scan")
            self.scan_page.canvas.set_zoom("fit")
            self.refresh_all()
            self.status(f"掃描完成：{len(pages)} 頁")
            self.run_post_action(pages)

        self._async(work, done)

    def _scan_progress(self, value, message):
        if value >= 0:
            self.progress.set_fraction(min(1.0, value))
            self.status(f"掃描中… {value * 100:.0f}%")
        return False

    def run_post_action(self, pages):
        dd = self.scan_page
        action = dd_value(dd.action_dd)
        outdir = Path(self.cfg.get("output_dir", str(Path.home() / "文件" / "掃描")))
        prefix = dd.prefix_entry.get_text().strip() or "scan"
        if not pages:
            return
        try:
            if action == "存成 PDF 到資料夾":
                path = outdir / (C.timestamp_name(prefix) + ".pdf")
                self.doc.export_pdf(path)
                self.toast(f"已存 PDF：{path.name}")
                self.status(f"已儲存 {path}")
            elif action == "存成 PNG 到資料夾":
                files = self.doc.export_images(outdir, C.timestamp_name(prefix), "PNG")
                self.toast(f"已存 {len(files)} 張 PNG")
                self.status(f"已儲存到 {outdir}")
            elif action == "直接列印":
                self.show_page("print")
                self.do_print(silent=True)
            else:
                self.toast("已加入文件，可用「送到列印分頁」繼續")
        except C.StudioError as exc:
            self.status(str(exc))
            self.toast(str(exc))

    # -- 編輯 / 頁面 ---------------------------------------------------
    def edit(self, fn):
        try:
            changed = fn()
        except Exception as exc:  # noqa: BLE001
            self.toast(f"操作失敗：{exc}")
            return
        if changed:
            self.refresh_all()

    def undo(self):
        if self.doc.undo():
            self.refresh_all()
            self.status("已還原")

    def redo(self):
        if self.doc.redo():
            self.refresh_all()
            self.status("已重做")

    def goto_page(self, index: int):
        if not self.doc.pages:
            return
        self.doc.index = max(0, min(index, len(self.doc.pages) - 1))
        self.refresh_all()

    def schedule_adjust(self):
        if self._adjust_timer:
            GLib.source_remove(self._adjust_timer)
        self._adjust_timer = GLib.timeout_add(140, self._apply_adjust)

    def _apply_adjust(self):
        self._adjust_timer = 0
        dd = self.scan_page
        if self.doc.set_adjust(int(dd.bright_scale.get_value()),
                               int(dd.contrast_scale.get_value())):
            self.refresh_canvas()
        return False

    def clear_document(self):
        if not self.doc.pages:
            return
        self.doc.clear()
        self.refresh_all()
        self.status("已清除文件")

    # -- 印表機 / 列印 -------------------------------------------------
    def _load_printers(self):
        def work():
            return C.list_printers(), C.default_printer()

        def done(data, err):
            queues, default = (data if data else ([], ""))
            dd = self.print_page.printer_dd
            if err or not queues:
                dd.set_model(Gtk.StringList.new(["（沒有可用印表機）"]))
                self.print_page.queue_label.set_text("")
                return
            ordered = sorted(queues, key=lambda q: (q != default, "FAX" in q.upper(),
                                                    queues.index(q)))
            dd.set_model(Gtk.StringList.new(ordered))
            want = self.cfg.get("printer", "")
            if want in ordered:
                dd_select(dd, want)
            elif default in ordered:
                dd_select(dd, default)
            self.on_printer_changed()

        self._async(work, done)

    def on_printer_changed(self):
        queue = dd_value(self.print_page.printer_dd)
        if not queue or queue.startswith("（"):
            self.print_page.queue_label.set_text("")
            return
        cfg = C.load_config()
        cfg["printer"] = queue
        C.save_config(cfg)
        self.cfg = cfg

        def work():
            return C.printer_status(queue), C.list_jobs()

        def done(data, err):
            if err or not data:
                return
            state, jobs = data
            pending = [j for j in jobs if j["id"].startswith(queue)]
            text = f"狀態：{state}"
            if pending:
                text += f" · 待印 {len(pending)} 件"
            self.print_page.queue_label.set_text(text)

        self._async(work, done)

    def do_print(self, silent: bool = False):
        dd = self.print_page
        queue = dd_value(dd.printer_dd)
        if self.current_page != "print":
            self.show_page("print")
        if not queue or queue.startswith("（"):
            self.toast("沒有可用的印表機")
            return
        if not self.doc.pages:
            self.toast("沒有內容可列印")
            return
        try:
            tmp = C.WORKDIR / "print"
            tmp.mkdir(parents=True, exist_ok=True)
            pdf = self.doc.export_pdf(tmp / "job.pdf")
        except C.StudioError as exc:
            self.toast(str(exc))
            return
        options = C.build_print_options(
            paper=dd_value(dd.paper_dd) or "A4",
            duplex=dd_value(dd.duplex_dd) or "關閉",
            color=dd_value(dd.color_dd) or "彩色",
            quality=dd_value(dd.quality_dd) or "標準",
            paper_type=dd_value(dd.papertype_dd) or "Plain",
            fit=dd.fit_switch.get_active(),
            toner_save=dd.toner_switch.get_active(),
        )
        copies = dd.copies_spin.get_value_as_int()
        self.busy_start()
        self.status(f"送出列印：{len(self.doc.pages)} 頁 × {copies} 份")

        def work():
            return C.print_files(queue, [pdf], options=options, copies=copies,
                                 title=f"Brother Studio {len(self.doc.pages)}p")

        def done(job, err):
            self.busy_end()
            if err:
                self.status("列印失敗：" + str(err))
                self.toast("列印失敗：" + str(err))
                return
            self.status(f"已送出：{job}")
            if not silent:
                self.toast(f"已送出列印：{job}")
            self.on_printer_changed()

        self._async(work, done)

    # -- 開啟 / 儲存 ---------------------------------------------------
    def open_files(self):
        dlg = Gtk.FileDialog(title="開啟圖片或 PDF")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f1 = Gtk.FileFilter()
        f1.set_name("圖片與 PDF")
        for pat in ("*.pdf", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff", "*.bmp", "*.webp"):
            f1.add_pattern(pat)
        filters.append(f1)
        dlg.set_filters(filters)
        dlg.open_multiple(self, None, self._on_open_done)

    def _on_open_done(self, dlg, result):
        try:
            files = dlg.open_multiple_finish(result)
        except GLib.Error:
            return
        paths = [Path(files.get_item(i).get_path()) for i in range(files.get_n_items())]
        self.load_paths(paths)

    def load_paths(self, paths):
        """把檔案載入文件並切到列印分頁（拖放／檔案管理員開啟也走這裡）。"""
        paths = [Path(p) for p in paths if p]
        if not paths:
            return
        self.busy_start()
        self.status(f"載入 {len(paths)} 個檔案…")

        def work():
            return self.doc.import_paths(paths)

        def done(count, err):
            self.busy_end()
            if err:
                self.toast(str(err))
                self.status("載入失敗：" + str(err))
                return
            self.refresh_all()
            self.show_page("print")
            self.status(f"已載入 {count} 頁")

        self._async(work, done)

    def choose_save_pdf(self):
        if not self.doc.pages:
            self.toast("文件是空的")
            return
        dlg = Gtk.FileDialog(title="儲存 PDF")
        prefix = self.scan_page.prefix_entry.get_text() or "scan"
        dlg.set_initial_name(C.timestamp_name(prefix) + ".pdf")
        dlg.save(self, None, self._on_save_pdf_done)

    def _on_save_pdf_done(self, dlg, result):
        try:
            gfile = dlg.save_finish(result)
        except GLib.Error:
            return
        path = Path(gfile.get_path())
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        try:
            self.doc.export_pdf(path)
            self.toast(f"已儲存 {path.name}")
            self.status(f"已儲存 {path}")
        except C.StudioError as exc:
            self.toast(str(exc))

    def choose_save_images(self):
        if not self.doc.pages:
            self.toast("文件是空的")
            return
        dlg = Gtk.FileDialog(title="選擇輸出資料夾")
        dlg.select_folder(self, None, self._on_save_images_done)

    def _on_save_images_done(self, dlg, result):
        try:
            gfile = dlg.select_folder_finish(result)
        except GLib.Error:
            return
        try:
            files = self.doc.export_images(
                Path(gfile.get_path()),
                C.timestamp_name(self.scan_page.prefix_entry.get_text() or "scan"), "PNG",
            )
            self.toast(f"已儲存 {len(files)} 張圖片")
        except C.StudioError as exc:
            self.toast(str(exc))

    def choose_output_dir(self):
        dlg = Gtk.FileDialog(title="選擇掃描輸出資料夾")
        dlg.select_folder(self, None, self._on_output_dir_done)

    def _on_output_dir_done(self, dlg, result):
        try:
            gfile = dlg.select_folder_finish(result)
        except GLib.Error:
            return
        self.set_output_dir(Path(gfile.get_path()))
        self.save_ui_state()

    def set_output_dir(self, path: Path):
        self.cfg["output_dir"] = str(path)
        self.scan_page.outdir_label.set_text(path.name or str(path))
        self.scan_page.outdir_label.set_tooltip_text(str(path))

    # -- 鍵盤 ---------------------------------------------------------
    def on_key_pressed(self, _ctrl, keyval, _keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape:
            if self.current_page == "scan" and self.scan_page.crop_toggle.get_active():
                self.scan_page.exit_crop_mode()
                return True
            if self.current_page != "home":
                self.show_page("home")
                return True
            return False
        if ctrl:
            return False
        if self.current_page != "scan":
            return False
        if keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.edit(lambda: self.doc.rotate(90 if not shift else -90))
            return True
        if keyval == Gdk.KEY_Delete:
            self.edit(self.doc.delete_current)
            return True
        if keyval == Gdk.KEY_Left:
            self.goto_page(self.doc.index - 1)
            return True
        if keyval == Gdk.KEY_Right:
            self.goto_page(self.doc.index + 1)
            return True
        return False

    # -- 狀態 / 非同步 ------------------------------------------------
    def status(self, text: str):
        self.status_label.set_text(text)

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast.new(text))

    def busy_start(self):
        self._busy_count += 1
        self.spinner.set_visible(True)
        self.spinner.start()

    def busy_end(self):
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.spinner.stop()
            self.spinner.set_visible(False)

    def _async(self, work, done):
        def runner():
            try:
                result, err = work(), None
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                result, err = None, exc
            GLib.idle_add(lambda: (done(result, err), False)[1])

        threading.Thread(target=runner, daemon=True).start()

    # -- 設定持久化 ---------------------------------------------------
    def save_ui_state(self):
        sp, pp = self.scan_page, self.print_page
        cfg = C.load_config()
        cfg.update({
            "scan_mode": dd_value(sp.mode_dd),
            "scan_resolution": dd_value(sp.res_dd),
            "scan_source": dd_value(sp.source_dd),
            "adf": sp.adf_switch.get_active(),
            "action": dd_value(sp.action_dd),
            "output_dir": self.cfg.get("output_dir"),
            "filename_prefix": sp.prefix_entry.get_text(),
            "auto_crop": sp.autocrop_switch.get_active(),
            "auto_enhance": sp.enhance_switch.get_active(),
            "printer": dd_value(pp.printer_dd),
            "paper": dd_value(pp.paper_dd),
            "duplex": dd_value(pp.duplex_dd),
            "color": dd_value(pp.color_dd),
            "quality": dd_value(pp.quality_dd),
            "paper_type": dd_value(pp.papertype_dd),
            "copies": pp.copies_spin.get_value_as_int(),
        })
        self.cfg = cfg
        C.save_config(cfg)

    def restore_ui_state(self):
        cfg, sp, pp = self.cfg, self.scan_page, self.print_page
        dd_select(sp.mode_dd, cfg.get("scan_mode", "彩色"))
        dd_select(sp.source_dd, cfg.get("scan_source", "平台玻璃"))
        dd_select(sp.action_dd, cfg.get("action", "加入文件（可編輯）"))
        sp.adf_switch.set_active(bool(cfg.get("adf", False)))
        sp.prefix_entry.set_text(cfg.get("filename_prefix", "scan"))
        sp.autocrop_switch.set_active(bool(cfg.get("auto_crop", True)))
        sp.enhance_switch.set_active(bool(cfg.get("auto_enhance", True)))
        self.set_output_dir(Path(cfg.get("output_dir", str(Path.home() / "文件" / "掃描"))))
        sp._on_action_changed()
        dd_select(pp.paper_dd, cfg.get("paper", "A4"))
        dd_select(pp.duplex_dd, cfg.get("duplex", "關閉"))
        dd_select(pp.color_dd, cfg.get("color", "彩色"))
        dd_select(pp.quality_dd, cfg.get("quality", "標準"))
        dd_select(pp.papertype_dd, cfg.get("paper_type", "Plain"))
        pp.copies_spin.set_value(float(cfg.get("copies", 1)))

    # 舊名稱（brother_studio.py / 舊腳本會用到）
    _restore_ui_state = restore_ui_state
    _refresh_all = refresh_all
    _status = status
    _toast = toast

    def show_about(self):
        dialog = Adw.AboutDialog(
            application_name="Brother Studio",
            application_icon="scanner-symbolic",
            developer_name="limOvO",
            version="1.1",
            comments="Brother MFC-L8390CDW 的掃描、編輯與列印工作台。\n"
                     "掃描與列印各自獨立分頁，共用同一份文件。",
        )
        dialog.present(self)

    def do_close_request(self):  # type: ignore[override]
        self.save_ui_state()
        return False
