#!/usr/bin/env python3
"""Brother Studio — 掃描 / 編輯 / 列印 工作台。

用法：
    python3 brother_studio.py                 # 正常啟動
    python3 brother_studio.py --demo          # 用示範文件啟動（不需要掃描器）
    python3 brother_studio.py --screenshot out.png   # 開發用：渲染視窗成 PNG
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bs_core as C  # noqa: E402
from bs_ui import APP_CSS, APP_ID, StudioWindow  # noqa: E402

FONT_CANDIDATES = [
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Light.ttc",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_demo_pages() -> list[C.Page]:
    """產生兩頁看起來像掃描件的示範內容（不需要掃描器）。"""
    pages: list[C.Page] = []
    W, H = 1240, 1754  # A4 @150dpi

    # 第一頁：報價單
    img = Image.new("RGB", (W, H), (252, 251, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, W - 60, 190], fill=(238, 242, 233), outline=(200, 210, 195), width=2)
    d.text((90, 90), "兄弟科技  Brother Tech", font=_font(38), fill=(30, 45, 35))
    d.text((90, 148), "報價單 Quotation · 2026-11-10", font=_font(22), fill=(90, 100, 92))
    d.text((W - 380, 100), "編號 QT-2026-1104", font=_font(20), fill=(90, 100, 92))
    d.text((W - 380, 132), "有效期限 14 天", font=_font(20), fill=(90, 100, 92))

    rows = [
        ("品項", "數量", "單價", "小計"),
        ("多功能事務機 MFC-L8390CDW", "1", "12,800", "12,800"),
        ("原廠碳粉匣 TN-269", "2", "2,150", "4,300"),
        ("A4 影印紙 80g", "10", "120", "1,200"),
        ("安裝與設定服務", "1", "1,500", "1,500"),
    ]
    y = 250
    d.rectangle([60, y, W - 60, y + 46], fill=(230, 235, 228))
    xs = [90, 640, 820, 1000]
    for i, cell in enumerate(rows[0]):
        d.text((xs[i], y + 10), cell, font=_font(22), fill=(40, 55, 45))
    y += 46
    for r_i, r in enumerate(rows[1:]):
        if r_i % 2 == 1:
            d.rectangle([60, y, W - 60, y + 42], fill=(248, 249, 246))
        for i, cell in enumerate(r):
            d.text((xs[i], y + 8), cell, font=_font(21), fill=(50, 60, 52))
        y += 42
    d.line([60, y, W - 60, y], fill=(200, 210, 195), width=2)
    d.text((820, y + 20), "合計", font=_font(24), fill=(40, 55, 45))
    d.text((1000, y + 20), "19,800", font=_font(26), fill=(30, 45, 35))
    d.text((90, y + 80), "備註：含基本安裝與一年保固，不含耗材後續費用。", font=_font(20), fill=(90, 100, 92))
    d.text((90, H - 110), "第 1 頁 / 共 2 頁", font=_font(18), fill=(140, 148, 142))
    pages.append(C.Page(img, 150, "demo"))

    # 第二頁：說明文字
    img2 = Image.new("RGB", (W, H), (253, 253, 251))
    d2 = ImageDraw.Draw(img2)
    d2.text((90, 90), "使用說明", font=_font(34), fill=(30, 40, 34))
    lines = [
        "Brother Studio 是一套給多功事務機用的掃描與列印工作台。",
        "",
        "· 掃描：可設定色彩、解析度與進紙來源（平台玻璃或 ADF 送紙器）。",
        "· 編輯：旋轉、裁切、調整亮度對比、灰階、二值化、去底色、自動裁切。",
        "· 預覽：列印前可切換到「列印預覽」，確認每頁在紙張上的位置。",
        "· 列印：可設定紙張、雙面、色彩、品質、份數與縮放。",
        "",
        "所有編輯都是非破壞性的：亮度與對比只是顯示參數，",
        "旋轉與裁切會改寫頁面，但可以用「還原」逐步退回。",
    ]
    yy = 180
    for line in lines:
        d2.text((90, yy), line, font=_font(23), fill=(45, 55, 48))
        yy += 46
    d2.rectangle([90, yy + 20, W - 90, yy + 150], outline=(180, 190, 182), width=2)
    d2.text((110, yy + 40), "提示：按 Ctrl+N 開始掃描，Ctrl+P 直接列印。", font=_font(22), fill=(70, 90, 78))
    d2.text((90, H - 110), "第 2 頁 / 共 2 頁", font=_font(18), fill=(140, 148, 142))
    pages.append(C.Page(img2, 150, "demo"))
    return pages


class BrotherStudioApp(Adw.Application):
    def __init__(self, screenshot: str | None = None, demo: bool = False,
                 open_paths=None, no_anim: bool = False):
        # 截圖／測試時用 NON_UNIQUE，避免被既有實例接管而收不到 activate；
        # 正式執行時接受「檔案管理員雙擊開啟」帶來的檔案（HANDLES_OPEN）
        flags = (Gio.ApplicationFlags.NON_UNIQUE if screenshot
                 else Gio.ApplicationFlags.HANDLES_OPEN)
        super().__init__(application_id=APP_ID, flags=flags)
        self.screenshot = screenshot
        self.demo = demo
        self.no_anim = no_anim
        self.open_paths = open_paths or []
        self.window: StudioWindow | None = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", lambda *_: self._show_about())
        self.add_action(about_action)

    def _show_about(self):
        dialog = Adw.AboutDialog(
            application_name="Brother Studio",
            application_icon="scanner-symbolic",
            developer_name="limOvO",
            version="1.0",
            comments="Brother MFC-L8390CDW 的掃描、編輯與列印工作台。",
            website="https://support.brother.com/",
        )
        dialog.present(self.window)

    def do_activate(self):
        if self.window is None:
            self.window = StudioWindow(self)
            if self.no_anim:
                self.window.stack.set_transition_type(Gtk.StackTransitionType.NONE)
                self.window.mode_stack.set_transition_duration(0)
            self.window.restore_ui_state()
            if self.demo:
                self.window.doc.add_many(make_demo_pages())
                self.window.refresh_all()
                self.window.status("示範文件已載入（不需要掃描器）")
            self.window.present()
            if self.open_paths:
                self.window.load_paths([Path(p) for p in self.open_paths])
            if self.screenshot:
                GLib.timeout_add(3000, self._take_screenshot)
        else:
            self.window.present()

    def do_open(self, files, n_files, hint):
        """檔案管理員用這個 App 開檔案時呼叫（會交給既有視窗）。"""
        self.do_activate()
        paths = []
        for i in range(n_files):
            path = files[i].get_path()
            if path:
                paths.append(Path(path))
        if paths and self.window is not None:
            self.window.load_paths(paths)
            self.window.present()

    # 開發用：把視窗內容渲染成 PNG（此環境沒有截圖工具）
    def _take_screenshot(self):
        win = self.window
        if win is None:
            return False
        path = self.screenshot
        try:
            target = win
            w, h = win.get_width(), win.get_height()
            native = win.get_native()
            renderer = native.get_renderer() if native else None
            if renderer is None:
                raise RuntimeError("renderer 尚未就緒")
            paintable = Gtk.WidgetPaintable.new(target)
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, w, h)
            node = snapshot.to_node()
            if node is None:
                self._shot_tries = getattr(self, "_shot_tries", 0) + 1
                if self._shot_tries < 6:
                    GLib.timeout_add(400, self._take_screenshot)
                    return False
                raise RuntimeError("snapshot 一直拿不到節點")
            texture = renderer.render_texture(node, None)
            texture.save_to_png(path)
            print(f"SCREENSHOT {path} {w}x{h}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print("SCREENSHOT_FAIL", exc, flush=True)
        GLib.timeout_add(100, lambda: (os._exit(0), False)[1])
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Brother Studio")
    parser.add_argument("files", nargs="*", help="要開啟的圖片或 PDF")
    parser.add_argument("--demo", action="store_true", help="用示範文件啟動")
    parser.add_argument("--screenshot", metavar="PNG", help="渲染視窗成 PNG 後結束（開發用）")
    parser.add_argument("--no-anim", action="store_true", help="關閉轉場動畫（截圖／遠端桌面用）")
    args = parser.parse_args(argv)
    app = BrotherStudioApp(
        screenshot=args.screenshot, demo=args.demo, open_paths=args.files,
        no_anim=args.no_anim,
    )
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())
