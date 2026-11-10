"""Brother Studio 後端。

不含任何 GTK 依賴，可在終端機直接 import 測試：
    python3 -c "import bs_core; print(bs_core.list_devices())"

三個部分：
  * 掃描  —— 包裝 scanimage（SANE）
  * 列印  —— 包裝 lp / lpstat / lpoptions（CUPS）
  * 文件  —— 頁面模型、影像編輯、PDF/PNG 匯出
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageEnhance, ImageOps, ImageStat

import threading

CONFIG_DIR = Path.home() / ".config" / "brother-studio"
CONFIG_PATH = CONFIG_DIR / "config.json"
WORKDIR = Path(tempfile.gettempdir()) / "brother-studio"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# SANE 裝置一次只能被一個行程開啟，所有會碰裝置的操作都排隊
_DEVICE_LOCK = threading.Lock()


class StudioError(Exception):
    """可以直接顯示給使用者的錯誤。"""


def _run(cmd: Sequence[str], timeout: float = 60) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            [str(c) for c in cmd], capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as exc:
        raise StudioError(f"找不到程式：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise StudioError(f"{cmd[0]} 逾時（{timeout:.0f} 秒）") from exc


# ---------------------------------------------------------------------------
# 設定檔
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "device_usb": "",
    "device_last": "",
    "scan_mode": "彩色",
    "scan_resolution": 300,
    "scan_source": "平台玻璃",
    "adf": False,
    "action": "加入文件",
    "output_dir": str(Path.home() / "文件" / "掃描"),
    "filename_prefix": "scan",
    "auto_crop": True,
    "auto_enhance": True,
    "printer": "",
    "paper": "A4",
    "duplex": "關閉",
    "color": "彩色",
    "quality": "標準",
    "copies": 1,
    "paper_type": "Plain",
    "save_pdf_after_scan": False,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 掃描
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Device:
    key: str          # scanimage -d 用的字串
    label: str        # 顯示用
    kind: str         # usb / net / other


def _human_size(text: str) -> str:
    low = text.lower()
    if "adf" in low and "platen" in low:
        return "平台玻璃 + ADF"
    if "adf" in low:
        return "ADF 送紙器"
    if "platen" in low:
        return "平台玻璃"
    if "usb" in low:
        return "USB"
    return ""


def _pretty_device(key: str, desc: str) -> str:
    kind = "USB" if "bus" in key else ("網路" if key.startswith(("escl:", "brother5:net")) else "")
    model = desc
    for junk in (" USB scanner", " platen,adf scanner", " platen scanner", " adf scanner"):
        model = model.replace(junk, "")
    model = model.replace(" series", "").replace(" scanner", "").strip()
    bits: list[str] = []
    for b in (kind, _human_size(desc)):
        if b and b not in bits:
            bits.append(b)
    return f"{model}（{' · '.join(bits)}）" if bits else model


_DEVICE_RE = re.compile(r"device `([^']+)' is a[n]? ([^\n<]+)")


def list_devices(timeout: float = 45) -> list[Device]:
    """列出 SANE 裝置（escl backend 會往 stdout 吐 HTML，用 regex 抓裝置行）。"""
    rc, out, err = _run(["scanimage", "-L"], timeout=timeout)
    text = out + "\n" + err
    found: list[Device] = []
    seen: set[str] = set()
    for m in _DEVICE_RE.finditer(text):
        key = m.group(1).strip()
        if key.startswith("v4l:") or key in seen:
            continue
        seen.add(key)
        found.append(Device(key, _pretty_device(key, m.group(2).strip()), "usb" if "bus" in key else "net"))
    return found


def usb_bus_number(timeout: float = 10) -> str:
    rc, out, _ = _run(["lsusb", "-d", "04f9:"], timeout=timeout)
    if rc == 0 and out.strip():
        m = re.search(r"Bus (\d+) Device", out)
        if m:
            return str(int(m.group(1)))
    return ""


def probe_usb_device(
    bus: str | None = None,
    max_index: int = 8,
    on_try: Callable[[str], None] | None = None,
) -> Device | None:
    """`scanimage -L` 在某些環境不會列出 brother5 的 USB 裝置，
    所以用「真的掃一小塊」逐一探測 brother5:busN;devM 找出可用的那個。
    """
    bus = bus or usb_bus_number()
    if not bus:
        return None
    WORKDIR.mkdir(parents=True, exist_ok=True)
    probe = WORKDIR / "usbprobe.png"
    for idx in range(max_index):
        key = f"brother5:bus{bus};dev{idx}"
        if on_try:
            on_try(key)
        try:
            with _DEVICE_LOCK:
                rc, _, _ = _run(
                [
                    "scanimage", "-d", key, "--format=png", "--source", "FlatBed",
                    "--resolution", "100", "-x", "10", "-y", "10", "-o", str(probe),
                ],
                timeout=25,
            )
        except StudioError:
            continue
        if rc == 0 and probe.exists() and probe.stat().st_size > 0:
            probe.unlink(missing_ok=True)
            return Device(key, "Brother MFC-L8390CDW（USB · 平台玻璃）", "usb")
        probe.unlink(missing_ok=True)
    return None


@dataclass
class ScanCaps:
    modes: list[str] = field(default_factory=list)
    default_mode: str = ""
    resolutions: list[int] = field(default_factory=list)
    default_resolution: int = 300
    sources: list[str] = field(default_factory=list)
    default_source: str = ""
    max_width_mm: float = 215.9
    max_height_mm: float = 355.6


def _option_values(line: str) -> tuple[list[str], str]:
    """`--mode A|B|C [B]` → (['A','B','C'], 'B')；選項名本身可能含 [ ]。"""
    body = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
    default = ""
    idx = body.rfind(" [")
    if idx != -1 and body.rstrip().endswith("]"):
        default = body[idx + 2 : body.rstrip().rfind("]")].strip()
        body = body[:idx]
    body = re.sub(r"\s+\(in steps.*$", "", body)
    return [v.strip() for v in body.split("|") if v.strip()], default


def scan_caps(device_key: str, timeout: float = 40) -> ScanCaps:
    with _DEVICE_LOCK:
        rc, out, err = _run(["scanimage", "-d", device_key, "-A"], timeout=timeout)
    text = out + "\n" + err
    caps = ScanCaps()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("--mode "):
            caps.modes, caps.default_mode = _option_values(line)
        elif line.startswith("--resolution "):
            vals, default = _option_values(line)
            res: list[int] = []
            for v in vals:
                m = re.match(r"(\d+)", v)
                if m:
                    res.append(int(m.group(1)))
            caps.resolutions = sorted(set(res))
            m = re.match(r"(\d+)", default)
            caps.default_resolution = int(m.group(1)) if m else 300
        elif line.startswith("--source "):
            caps.sources, caps.default_source = _option_values(line)
        elif line.startswith("-x "):
            m = re.search(r"[-0-9.]+\.\.([0-9.]+)mm", line)
            if m:
                caps.max_width_mm = float(m.group(1))
        elif line.startswith("-y "):
            m = re.search(r"[-0-9.]+\.\.([0-9.]+)mm", line)
            if m:
                caps.max_height_mm = float(m.group(1))
    return caps


MODE_MAP = {
    "彩色": ("Color",),
    "灰階": ("Gray", "Grayscale"),
    "黑白": ("Black & White", "Lineart", "Binary"),
}

SOURCE_MAP = {
    "平台玻璃": ("FlatBed", "Flatbed"),
    "ADF 單面": ("Automatic Document Feeder",),
    "ADF 雙面": ("Duplex",),
}


def _pick_value(available: Iterable[str], wanted: tuple[str, ...]) -> str:
    avail = list(available)
    for want in wanted:
        for a in avail:
            if a.lower().startswith(want.lower()):
                return a
    for want in wanted:
        for a in avail:
            if want.lower() in a.lower():
                return a
    return ""


def resolve_scan_params(
    caps: ScanCaps, mode: str, resolution: int, source: str, adf: bool
) -> dict[str, str]:
    params: dict[str, str] = {}
    m = _pick_value(caps.modes, MODE_MAP.get(mode, ("Color",)))
    if m:
        params["mode"] = m
    if caps.resolutions:
        nearest = min(caps.resolutions, key=lambda r: abs(r - int(resolution)))
        params["resolution"] = str(nearest)
    if adf:
        src = _pick_value(caps.sources, ("Duplex", "Automatic Document Feeder"))
    else:
        src = _pick_value(caps.sources, SOURCE_MAP.get(source, ("FlatBed",)))
    if src:
        params["source"] = src
    return params


def scan(
    device_key: str,
    outdir: Path,
    *,
    mode: str = "彩色",
    resolution: int = 300,
    source: str = "平台玻璃",
    adf: bool = False,
    caps: ScanCaps | None = None,
    on_progress: Callable[[float, str], None] | None = None,
) -> list[Path]:
    """掃描並回傳產生的 PNG 路徑（ADF 多頁時依序回傳）。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("scan*.png"):
        old.unlink(missing_ok=True)

    caps = caps or scan_caps(device_key)
    params = resolve_scan_params(caps, mode, resolution, source, adf)

    cmd = ["scanimage", "-d", device_key, "--format=png", "--progress"]
    if "mode" in params:
        cmd += ["--mode", params["mode"]]
    if "resolution" in params:
        cmd += ["--resolution", params["resolution"]]
    if "source" in params:
        cmd += ["--source", params["source"]]
    if adf:
        cmd += [f"--batch={outdir / 'scan%03d.png'}"]
    else:
        cmd += ["-o", str(outdir / "scan000.png")]

    acquired = _DEVICE_LOCK.acquire(timeout=120)
    try:
        return _scan_locked(cmd, outdir, adf, on_progress)
    finally:
        if acquired:
            _DEVICE_LOCK.release()


BUSY_HINTS = ("Invalid argument", "Device busy", "device busy", "resource busy", "EBUSY")


def _scan_locked(cmd, outdir, adf, on_progress):
    for attempt in range(3):
        result = _scan_once(cmd, outdir, adf, on_progress)
        if result:
            return result
        import time as _time
        _time.sleep(1.5)
    raise StudioError("掃描失敗：掃描器忙碌或被其他程式佔用，請關掉其他掃描程式後重試")


def _scan_once(cmd, outdir, adf, on_progress):
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    tail: list[str] = []
    assert proc.stderr is not None
    for line in proc.stderr:
        line = line.rstrip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-8:]
        if on_progress:
            m = re.search(r"Progress:\s*([0-9.]+)%", line)
            if m:
                on_progress(float(m.group(1)) / 100.0, line)
            else:
                on_progress(-1.0, line)
    proc.wait()

    files = sorted(outdir.glob("scan*.png"))
    if files:
        return files
    joined = "；".join(tail[-3:])
    if any(h in joined for h in BUSY_HINTS):
        return None          # 交給上層重試
    if proc.returncode == 7:
        raise StudioError("ADF 沒有偵測到紙張")
    raise StudioError(
        "掃描失敗：" + (joined or f"scanimage rc={proc.returncode}")
    )


# ---------------------------------------------------------------------------
# 列印
# ---------------------------------------------------------------------------

def list_printers(timeout: float = 15) -> list[str]:
    rc, out, _ = _run(["lpstat", "-p"], timeout=timeout)
    queues: list[str] = []
    for line in out.splitlines():
        m = re.match(r"printer (\S+) is ", line)
        if m:
            queues.append(m.group(1))
    return queues


def default_printer(timeout: float = 10) -> str:
    rc, out, _ = _run(["lpstat", "-d"], timeout=timeout)
    m = re.search(r"system default destination:\s*(\S+)", out)
    return m.group(1) if m else ""


def printer_status(queue: str, timeout: float = 15) -> str:
    rc, out, _ = _run(["lpstat", "-p", queue], timeout=timeout)
    for line in out.splitlines():
        m = re.match(r"printer \S+ is (\w+)", line)
        if m:
            state = {"idle": "閒置", "processing": "列印中", "disabled": "已停用"}.get(
                m.group(1), m.group(1)
            )
            if "disabled" in line:
                state = "已停用"
            return state
    return "未知"


def printer_options(queue: str, timeout: float = 15) -> dict[str, tuple[str, list[str], str]]:
    """回傳 {關鍵字: (顯示標題, 可選值, 預設值)}。"""
    rc, out, _ = _run(["lpoptions", "-p", queue, "-l"], timeout=timeout)
    result: dict[str, tuple[str, list[str], str]] = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        head, values = line.split(":", 1)
        title = ""
        if "/" in head:
            head, title = head.split("/", 1)
        head = head.strip()
        vals: list[str] = []
        default = ""
        for tok in values.split():
            if tok.startswith("*"):
                default = tok[1:]
                vals.append(tok[1:])
            else:
                vals.append(tok)
        if head:
            result[head] = (title.strip(), vals, default)
    return result


PAPER_CHOICES = ["A4", "Letter", "Legal", "A5", "A6", "B5", "EnvC5", "EnvDL"]
DUPLEX_CHOICES = {"關閉": "None", "長邊翻頁": "DuplexNoTumble", "短邊翻頁": "DuplexTumble"}
COLOR_CHOICES = {"彩色": "FullColor", "單色": "Mono"}
QUALITY_CHOICES = {"標準": "600dpi", "精細": "600x2400dpi"}
PAPER_TYPE_CHOICES = ["Plain", "Thick", "Recycled", "Label", "Glossy", "Env"]


def build_print_options(
    *,
    paper: str = "A4",
    duplex: str = "關閉",
    color: str = "彩色",
    quality: str = "標準",
    paper_type: str = "Plain",
    fit: bool = True,
    toner_save: bool = False,
    skip_blank: bool = False,
) -> dict[str, str]:
    opts = {
        "PageSize": paper,
        "Duplex": DUPLEX_CHOICES.get(duplex, "None"),
        "BRMonoColor": COLOR_CHOICES.get(color, "FullColor"),
        "BRResolution": QUALITY_CHOICES.get(quality, "600dpi"),
        "BRMediaType": paper_type,
    }
    if fit:
        opts["fit-to-page"] = "True"
    if toner_save:
        opts["BRTonerSaveMode"] = "ON"
    if skip_blank:
        opts["BRSkipBlank"] = "ON"
    return opts


class PrintError(StudioError):
    pass


def print_files(
    queue: str,
    files: Sequence[Path],
    *,
    options: dict[str, str] | None = None,
    copies: int = 1,
    title: str | None = None,
    hold: bool = False,
    timeout: float = 60,
) -> str:
    files = [Path(f) for f in files]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise PrintError("檔案不存在：" + "、".join(missing))
    cmd = ["lp", "-d", queue, "-n", str(copies)]
    if title:
        cmd += ["-t", title]
    for key, value in (options or {}).items():
        if value not in (None, ""):
            cmd += ["-o", f"{key}={value}"]
    if hold:
        cmd += ["-H", "hold"]
    cmd += [str(f) for f in files]
    rc, out, err = _run(cmd, timeout=timeout)
    if rc != 0:
        raise PrintError((err or out).strip() or f"lp 失敗（rc={rc}）")
    m = re.search(r"request id is (\S+)", out)
    return m.group(1) if m else ""


def list_jobs(timeout: float = 15) -> list[dict[str, str]]:
    rc, out, _ = _run(["lpstat", "-W", "not-completed", "-o"], timeout=timeout)
    jobs: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            jobs.append({"id": parts[0], "user": parts[1], "size": parts[2], "time": " ".join(parts[3:])})
    return jobs


def cancel_job(job_id: str, timeout: float = 15) -> None:
    _run(["cancel", job_id], timeout=timeout)


# ---------------------------------------------------------------------------
# 頁面與影像編輯
# ---------------------------------------------------------------------------

class Page:
    """一頁掃描結果。

    幾何/色調破壞性操作改寫 `base`；亮度與對比是非破壞性的顯示參數，
    這樣拖滑桿就能即時預覽、而且不會愈拖愈糟。
    """

    def __init__(self, image: Image.Image, dpi: int = 300, origin: str = "") -> None:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        self.base: Image.Image = image
        self.dpi = dpi
        self.origin = origin
        self.brightness = 0   # -50..50
        self.contrast = 0     # -50..50
        self._cache: Image.Image | None = None
        self._cache_key: tuple[int, int] | None = None

    # -- 顯示 ---------------------------------------------------------
    def rendered(self) -> Image.Image:
        key = (self.brightness, self.contrast)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        img = self.base
        if self.brightness:
            img = ImageEnhance.Brightness(img).enhance(1.0 + self.brightness / 100.0)
        if self.contrast:
            img = ImageEnhance.Contrast(img).enhance(1.0 + self.contrast / 50.0)
        self._cache = img
        self._cache_key = key
        return img

    def touch(self) -> None:
        """base 被改過後清快取。"""
        self._cache = None
        self._cache_key = None

    @property
    def size(self) -> tuple[int, int]:
        return self.base.size

    def thumbnail(self, box: int = 96) -> Image.Image:
        img = self.rendered()
        img = img.copy()
        img.thumbnail((box, box), Image.LANCZOS)
        return img


def _autocrop_box(image: Image.Image, threshold: int = 245, pad: int = 4) -> tuple[int, int, int, int] | None:
    gray = image.convert("L")
    # 找出不是「紙白」的區域
    mask = gray.point(lambda v: 255 if v < threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    w, h = image.size
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w, right + pad)
    bottom = min(h, bottom + pad)
    # 太小的裁切視為誤判
    if (right - left) < w * 0.15 or (bottom - top) < h * 0.15:
        return None
    return left, top, right, bottom


def op_autocrop(image: Image.Image) -> Image.Image:
    box = _autocrop_box(image)
    return image.crop(box) if box else image


def op_whiten(image: Image.Image) -> Image.Image:
    """去底色／白平衡：把偏灰的紙背景拉成純白。"""
    if image.mode == "L":
        return ImageOps.autocontrast(image, cutoff=0.5)
    return ImageOps.autocontrast(image, cutoff=0.5)


def op_grayscale(image: Image.Image) -> Image.Image:
    return image.convert("L").convert("RGB")


def op_binarize(image: Image.Image, threshold: int = 160) -> Image.Image:
    gray = image.convert("L")
    return gray.point(lambda v: 255 if v > threshold else 0).convert("RGB")


def op_deskew_hint(image: Image.Image) -> float:
    """估算傾斜角度（度）。用投影法粗估，沒有 numpy 依賴以外的东西。"""
    try:
        import numpy as np
    except ImportError:
        return 0.0
    gray = image.convert("L").resize((min(600, image.width), min(600, image.height)))
    arr = np.asarray(gray, dtype=np.float32)
    mask = (arr < 128).astype(np.float32)
    if mask.sum() < 50:
        return 0.0
    best_angle, best_score = 0.0, -1.0
    for angle in [x * 0.5 for x in range(-10, 11)]:
        rotated = Image.fromarray((mask * 255).astype(np.uint8)).rotate(
            angle, resample=Image.BILINEAR, expand=False, fillcolor=0
        )
        rows = np.asarray(rotated, dtype=np.float32).sum(axis=1)
        score = float((rows ** 2).sum())
        if score > best_score:
            best_score, best_angle = score, angle
    return best_angle


class Document:
    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.index = 0
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []

    # -- 狀態 ---------------------------------------------------------
    @property
    def current(self) -> Page | None:
        if 0 <= self.index < len(self.pages):
            return self.pages[self.index]
        return None

    def __len__(self) -> int:
        return len(self.pages)

    def add(self, page: Page) -> None:
        self._push_undo(("list",), self._page_state())
        self.pages.append(page)
        self.index = len(self.pages) - 1

    def add_many(self, pages: Sequence[Page]) -> None:
        if not pages:
            return
        self._push_undo(("list",), self._page_state())
        self.pages.extend(pages)
        self.index = len(self.pages) - 1

    def clear(self) -> None:
        self._push_undo(("list",), self._page_state())
        self.pages.clear()
        self.index = 0

    # -- undo ---------------------------------------------------------
    def _page_state(self):
        return (list(self.pages), self.index)

    def _push_undo(self, kind: tuple, payload) -> None:
        self._undo.append((kind, payload, self._page_state()))
        self._undo = self._undo[-30:]
        self._redo.clear()

    def _restore(self, state) -> None:
        pages, index = state
        self.pages = list(pages)
        self.index = max(0, min(index, len(self.pages) - 1))

    def undo(self) -> bool:
        if not self._undo:
            return False
        kind, payload, state = self._undo.pop()
        self._redo.append((kind, payload, self._page_state()))
        if kind[0] == "image":
            idx, old_base = payload
            if idx < len(self.pages):
                self.pages[idx].base = old_base
                self.pages[idx].touch()
            self.index = idx
        else:
            self._restore(payload)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        kind, payload, state = self._redo.pop()
        self._undo.append((kind, payload, self._page_state()))
        if kind[0] == "image":
            idx, new_base = payload
            if idx < len(self.pages):
                self.pages[idx].base = new_base
                self.pages[idx].touch()
            self.index = idx
        else:
            self._restore(payload)
        return True

    # -- 編輯 ---------------------------------------------------------
    def _apply_image_op(self, fn: Callable[[Image.Image], Image.Image]) -> bool:
        page = self.current
        if page is None:
            return False
        old = page.base
        new = fn(old)
        if new is old:
            return False
        self._push_undo(("image",), (self.index, old))
        page.base = new
        page.touch()
        return True

    def rotate(self, degrees: int) -> bool:
        return self._apply_image_op(
            lambda img: img.rotate(-degrees, expand=True, resample=Image.BICUBIC, fillcolor="white")
        )

    def flip(self) -> bool:
        return self._apply_image_op(ImageOps.mirror)

    def crop(self, box: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = [int(round(v)) for v in box]
        if right - left < 8 or bottom - top < 8:
            return False
        return self._apply_image_op(lambda img: img.crop((left, top, right, bottom)))

    def autocrop(self) -> bool:
        def apply(img):
            return op_autocrop(img)
        return self._apply_image_op(apply)

    def grayscale(self) -> bool:
        return self._apply_image_op(op_grayscale)

    def binarize(self, threshold: int = 160) -> bool:
        return self._apply_image_op(lambda img: op_binarize(img, threshold))

    def whiten(self) -> bool:
        return self._apply_image_op(op_whiten)

    def deskew(self) -> bool:
        page = self.current
        if page is None:
            return False
        angle = op_deskew_hint(page.base)
        if abs(angle) < 0.3:
            return False
        return self._apply_image_op(
            lambda img: img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor="white")
        )

    def set_adjust(self, brightness: int, contrast: int) -> bool:
        page = self.current
        if page is None:
            return False
        changed = (page.brightness, page.contrast) != (brightness, contrast)
        page.brightness, page.contrast = brightness, contrast
        return changed

    def reset_adjust(self) -> None:
        for page in self.pages:
            page.brightness = 0
            page.contrast = 0

    def delete_current(self) -> bool:
        if not self.pages:
            return False
        self._push_undo(("list",), self._page_state())
        self.pages.pop(self.index)
        self.index = max(0, min(self.index, len(self.pages) - 1))
        return True

    def duplicate_current(self) -> bool:
        page = self.current
        if page is None:
            return False
        self._push_undo(("list",), self._page_state())
        copy = Page(page.base.copy(), page.dpi, page.origin)
        copy.brightness, copy.contrast = page.brightness, page.contrast
        self.pages.insert(self.index + 1, copy)
        self.index += 1
        return True

    def move(self, delta: int) -> bool:
        target = self.index + delta
        if not (0 <= target < len(self.pages)):
            return False
        self._push_undo(("list",), self._page_state())
        self.pages[self.index], self.pages[target] = self.pages[target], self.pages[self.index]
        self.index = target
        return True

    def apply_to_all(self, fn: Callable[[Image.Image], Image.Image]) -> bool:
        if not self.pages:
            return False
        self._push_undo(("list",), self._page_state())
        for page in self.pages:
            page.base = fn(page.base)
            page.touch()
        return True

    # -- 載入 / 匯出 ---------------------------------------------------
    def import_paths(self, paths: Sequence[Path], dpi: int = 200) -> int:
        added = 0
        pages: list[Page] = []
        for path in paths:
            path = Path(path)
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                pages.extend(self._import_pdf(path, dpi))
            elif suffix in IMAGE_SUFFIXES:
                with Image.open(path) as img:
                    pages.append(Page(img.copy(), dpi, str(path)))
            else:
                continue
            added += 1
        if pages:
            self.add_many(pages)
        return len(pages)

    def _import_pdf(self, path: Path, dpi: int) -> list[Page]:
        if not shutil.which("pdftoppm"):
            raise StudioError("讀取 PDF 需要 pdftoppm（poppler-utils）")
        outdir = WORKDIR / "import"
        if outdir.exists():
            shutil.rmtree(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        rc, out, err = _run(
            ["pdftoppm", "-r", str(dpi), "-png", str(path), str(outdir / "pg")], timeout=180
        )
        pages: list[Page] = []
        for f in sorted(outdir.glob("pg-*.png")):
            with Image.open(f) as img:
                pages.append(Page(img.copy(), dpi, str(path)))
        if not pages:
            raise StudioError(f"PDF 讀不到頁面：{err.strip() or path.name}")
        return pages

    def export_pdf(self, path: Path) -> Path:
        if not self.pages:
            raise StudioError("文件是空的")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        images = [p.rendered().convert("RGB") for p in self.pages]
        dpi = float(self.pages[0].dpi or 300)
        images[0].save(
            path, "PDF", save_all=True, append_images=images[1:],
            resolution=dpi, quality=92,
        )
        return path

    def export_images(self, outdir: Path, prefix: str = "scan", fmt: str = "PNG") -> list[Path]:
        if not self.pages:
            raise StudioError("文件是空的")
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        ext = ".png" if fmt.upper() == "PNG" else ".jpg"
        written: list[Path] = []
        for i, page in enumerate(self.pages, 1):
            target = outdir / f"{prefix}-{i:03d}{ext}"
            img = page.rendered()
            if fmt.upper() == "JPEG":
                img = img.convert("RGB")
            img.save(target, dpi=(page.dpi, page.dpi))
            written.append(target)
        return written


# ---------------------------------------------------------------------------
# 便利函式：掃描 → 載入文件 → 依動作處理
# ---------------------------------------------------------------------------

def scan_to_document(
    doc: Document,
    device_key: str,
    *,
    mode: str = "彩色",
    resolution: int = 300,
    source: str = "平台玻璃",
    adf: bool = False,
    auto_crop: bool = True,
    auto_enhance: bool = True,
    on_progress: Callable[[float, str], None] | None = None,
) -> list[Page]:
    outdir = WORKDIR / "scan"
    files = scan(
        device_key, outdir,
        mode=mode, resolution=resolution, source=source, adf=adf,
        on_progress=on_progress,
    )
    pages: list[Page] = []
    for f in files:
        with Image.open(f) as img:
            page = Page(img.copy(), resolution, str(f))
        if auto_crop:
            page.base = op_autocrop(page.base)
            page.touch()
        if auto_enhance:
            page.base = op_whiten(page.base)
            page.touch()
        pages.append(page)
    doc.add_many(pages)
    return pages


def timestamp_name(prefix: str = "scan") -> str:
    from datetime import datetime

    return f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}"
