# -*- coding: utf-8 -*-
"""
pdfsize_gui.py —— PDF 页面尺寸批量检测 / A4·A3 标准化 图形界面

运行：python pdfsize_gui.py
依赖：pymupdf （tkinter 为 Python 自带）

v1.2 新增：
  · 文件列表右键菜单（打开文件 / 打开所在文件夹 / 预览 / 设定尺寸 / 仅转换选中 …）
  · 「只看需转换」筛选
  · 文件数量统计条（需转换 / 无需转换 / 失败 / 已跳过）
  · 上下分区可拖动（分割条变粗，位置会被记住）
  · 逐文件设定目标尺寸与方向（A4/A3/A5/B5/Letter × 竖版/横版），或标记为跳过
  · 多选批量设定 / 批量转换
  · 转换前后对比预览窗口（等比缩放与留白可视化）

高分辨率屏幕（4K / 系统缩放 125%~200%）：
  1. 在创建窗口【之前】声明进程 DPI 感知（Per-Monitor V2）；
  2. 按真实 DPI 设置 tk scaling，并用同一比例放大窗口尺寸、行高、列宽、边距；
  3. 底部有【界面缩放】下拉框，可在 100%~250% 之间实时调整、立即重排并被记住。
"""

from __future__ import annotations

import base64
import os
import queue
import subprocess
import sys
import tempfile
import threading
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import pdfsize_core as core

APP_TITLE = "PDF 页面尺寸检测 / A4·A3 标准化工具"
APP_VER = "v0.1"

# 界面缩放：显示文字 -> 相对系统缩放的倍数
ZOOM_CHOICES: list[tuple[str, float]] = [
    ("100%（跟随系统）", 1.0),
    ("110%", 1.1),
    ("125%", 1.25),
    ("150%", 1.5),
    ("175%", 1.75),
    ("200%", 2.0),
    ("250%", 2.5),
]

BASE_FONT_PT = 10          # 基础字号（磅）；像素大小由 tk scaling 按 DPI 换算

AUTO_TEXT = "自动（按规则）"
SKIP_TEXT = "跳过（不处理）"


# ---------------------------------------------------------------- DPI
def enable_high_dpi() -> None:
    """
    声明进程 DPI 感知。**必须在创建 Tk 窗口之前调用**，否则 Tk 只按 96dpi
    计算字号，在高分屏上文字明显偏小。
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:                                        # Windows 10 1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:                                        # Windows 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:                                        # Vista+
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_SYS_DPI_CACHE: list[float] = []


def _settings_path() -> str:
    """记住界面缩放、分割条位置，下次启动沿用"""
    env = os.environ.get("PDFSIZE_SETTINGS")
    if env:
        return env
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "pdfsize-tool.json")


def _load_settings(path: str) -> dict:
    try:
        import json
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings(path: str, data: dict) -> None:
    try:
        import json
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def system_dpi() -> float:
    """系统（主屏）DPI；取不到时返回 96"""
    if sys.platform == "win32":
        import ctypes
        try:
            v = float(ctypes.windll.user32.GetDpiForSystem())
            if v >= 72:
                return v
        except Exception:
            pass
        try:
            dc = ctypes.windll.user32.GetDC(0)
            v = float(ctypes.windll.gdi32.GetDeviceCaps(dc, 88))   # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, dc)
            if v >= 72:
                return v
        except Exception:
            pass
    return 96.0


def open_in_system(path: str) -> None:
    """用系统默认程序打开文件/文件夹"""
    if sys.platform == "win32":
        os.startfile(path)                      # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------- 设定尺寸对话框
class RuleDialog(tk.Toplevel):
    """给一批文件设定目标尺寸/方向，或标记为跳过"""

    def __init__(self, master: "App", files: list[core.FileInfo]) -> None:
        super().__init__(master)
        self.app = master
        self.files = files
        self.result: core.Rule | None = None
        P = master.px

        self.title("设定转换尺寸")
        self.resizable(False, False)
        self.transient(master)

        self.var_size = tk.StringVar(value=AUTO_TEXT)
        self.var_dir = tk.StringVar(value="竖版")
        self.var_hint = tk.StringVar(value="")

        body = ttk.Frame(self, padding=P(14))
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=f"对选中的 {len(files)} 个文件设定：",
                  font=(master.family, BASE_FONT_PT, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, P(10)))

        ttk.Label(body, text="目标尺寸：").grid(row=1, column=0, sticky="e", pady=P(4))
        self.cb_size = ttk.Combobox(
            body, textvariable=self.var_size, state="readonly", width=18,
            values=[AUTO_TEXT] + list(core.PAPER_MM) + [SKIP_TEXT])
        self.cb_size.grid(row=1, column=1, sticky="w", pady=P(4))
        self.cb_size.bind("<<ComboboxSelected>>", self._sync)

        ttk.Label(body, text="方向：").grid(row=2, column=0, sticky="e", pady=P(4))
        self.cb_dir = ttk.Combobox(body, textvariable=self.var_dir, state="readonly",
                                   width=18, values=["竖版", "横版"])
        self.cb_dir.grid(row=2, column=1, sticky="w", pady=P(4))
        self.cb_dir.bind("<<ComboboxSelected>>", self._sync)

        self.lbl_hint = ttk.Label(body, textvariable=self.var_hint, foreground="#444",
                                  wraplength=P(430), justify="left")
        self.lbl_hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(P(10), P(12)))

        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="取消", width=8, command=self._cancel).pack(side="right")
        ttk.Button(btns, text="确定", width=8, command=self._ok).pack(side="right", padx=(0, P(6)))

        self._echo_existing()
        self._sync()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self._center()

    # ---------- 内部 ----------
    def _echo_existing(self) -> None:
        """若所选文件规则一致，回显当前设置"""
        rules = {fi.rule for fi in self.files}
        if len(rules) != 1:
            return
        r = rules.pop()
        if r.mode == core.RULE_SKIP:
            self.var_size.set(SKIP_TEXT)
            return
        if r.mode != core.RULE_TARGET:
            return
        for paper, (w, h) in core.PAPER_MM.items():
            if abs(w - r.w_mm) < 0.01 and abs(h - r.h_mm) < 0.01:
                self.var_size.set(paper)
                self.var_dir.set("竖版")
                return
            if abs(h - r.w_mm) < 0.01 and abs(w - r.h_mm) < 0.01:
                self.var_size.set(paper)
                self.var_dir.set("横版")
                return

    def _sync(self, _evt=None) -> None:
        size = self.var_size.get()
        is_target = size in core.PAPER_MM
        self.cb_dir.configure(state="readonly" if is_target else "disabled")
        n = len(self.files)
        if size == AUTO_TEXT:
            self.var_hint.set("按默认规则判定：竖版近A4 → A4，横版近A3 → A3。"
                              "（勾选附加规则时，再处理横版近A4、竖版近A3）")
        elif size == SKIP_TEXT:
            self.var_hint.set(f"这 {n} 个文件不做任何处理，转换时直接跳过。")
        else:
            w, h = core.PAPER_MM[size]
            if self.var_dir.get() == "横版":
                w, h = h, w
            self.var_hint.set(f"把这 {n} 个文件的每一页等比缩放并居中到 {w:g}×{h:g}mm"
                              f"（不拉伸，多余部分留白）。已经是该尺寸的页不动。")

    def _rule(self) -> core.Rule:
        size = self.var_size.get()
        if size == AUTO_TEXT:
            return core.Rule()
        if size == SKIP_TEXT:
            return core.RULE_SKIP_OBJ
        return core.make_rule(size, self.var_dir.get() == "横版")

    def _ok(self) -> None:
        self.result = self._rule()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        px, py = self.app.winfo_rootx(), self.app.winfo_rooty()
        pw, ph = self.app.winfo_width(), self.app.winfo_height()
        self.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")

    def show(self) -> core.Rule | None:
        self.grab_set()
        self.wait_window()
        return self.result


# ---------------------------------------------------------------- 预览对比窗口
class PreviewWindow(tk.Toplevel):
    """左右并排对比某一页转换前后的样子"""

    def __init__(self, master: "App", fi: core.FileInfo) -> None:
        super().__init__(master)
        self.app = master
        self.fi = fi
        self._imgs: list[tk.PhotoImage] = []
        self._tmp: list[str] = []
        P = master.px

        self.title(f"预览对比 — {fi.name}")
        self.transient(master)

        self.conv = [p.index - 1 for p in fi.convertible_pages]
        self.idx = self.conv[0] if self.conv else 0

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.box = (max(P(220), min(P(430), (int(sw * 0.92) - P(180)) // 2)),
                    max(P(220), min(P(580), int(sh * 0.78) - P(250))))

        self.var_page = tk.StringVar(value=str(self.idx + 1))
        self.var_only = tk.BooleanVar(value=False)
        self.var_note = tk.StringVar(value="")

        head = ttk.Frame(self, padding=(P(10), P(8), P(10), P(2)))
        head.pack(fill="x")
        ttk.Button(head, text="◀ 上一页", command=lambda: self._step(-1)).pack(side="left")
        ttk.Button(head, text="下一页 ▶", command=lambda: self._step(1)).pack(side="left", padx=(P(4), P(10)))
        ttk.Label(head, text="页码：").pack(side="left")
        self.sp = ttk.Spinbox(head, from_=1, to=max(1, fi.page_count), width=6,
                              textvariable=self.var_page, command=self._jump)
        self.sp.pack(side="left")
        self.sp.bind("<Return>", lambda _e: self._jump())
        ttk.Label(head, text=f" / 共 {fi.page_count} 页"
                             f"（{len(self.conv)} 页会转换）").pack(side="left", padx=(P(6), 0))
        ttk.Checkbutton(head, text="只看需转换页", variable=self.var_only,
                        command=self._render).pack(side="left", padx=(P(16), 0))
        ttk.Button(head, text="关闭", command=self.destroy).pack(side="right")

        body = ttk.Frame(self, padding=(P(10), P(4), P(10), P(4)))
        body.pack(fill="both", expand=True)
        left = ttk.LabelFrame(body, text=" 原始 ", padding=P(6))
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.LabelFrame(body, text=" 转换后 ", padding=P(6))
        right.grid(row=0, column=1, sticky="nsew", padx=(P(8), 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self.cap0 = ttk.Label(left, text="", justify="left", foreground="#333")
        self.cap0.pack(fill="x")
        self.lbl0 = ttk.Label(left, anchor="center")
        self.lbl0.pack(fill="both", expand=True, pady=(P(4), 0))

        self.cap1 = ttk.Label(right, text="", justify="left", foreground="#333")
        self.cap1.pack(fill="x")
        self.lbl1 = ttk.Label(right, anchor="center")
        self.lbl1.pack(fill="both", expand=True, pady=(P(4), 0))

        foot = ttk.Frame(self, padding=(P(10), P(2), P(10), P(8)))
        foot.pack(fill="x")
        ttk.Label(foot, textvariable=self.var_note, foreground="#666").pack(side="left")

        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.update_idletasks()
        w = min(int(sw * 0.95), self.box[0] * 2 + P(220))
        h = min(int(sh * 0.92), self.box[1] + P(190))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 4)}")
        self._render()

    # ---------- 渲染 ----------
    def _close(self) -> None:
        for f in self._tmp:
            try:
                os.remove(f)
            except OSError:
                pass
        self._tmp.clear()
        self.destroy()

    def _photo(self, pv: core.Preview) -> tk.PhotoImage:
        try:
            return tk.PhotoImage(master=self,
                                 data=base64.b64encode(pv.data).decode("ascii"))
        except tk.TclError:
            fd, tmp = tempfile.mkstemp(suffix="." + pv.fmt)
            os.write(fd, pv.data)
            os.close(fd)
            self._tmp.append(tmp)
            return tk.PhotoImage(master=self, file=tmp)

    def _fit_dpi(self, p: core.PageInfo, tgt) -> float:
        w_in = max(p.width_mm, tgt[0] if tgt else 0.0) / 25.4
        h_in = max(p.height_mm, tgt[1] if tgt else 0.0) / 25.4
        bw, bh = self.box
        if w_in <= 0 or h_in <= 0:
            return 72.0
        return max(24.0, min(bw / w_in, bh / h_in) * 0.97)

    def _render(self, _evt=None) -> None:
        if not self.fi.pages:
            return
        self.idx = max(0, min(self.idx, len(self.fi.pages) - 1))
        self.var_page.set(str(self.idx + 1))
        p = self.fi.pages[self.idx]
        tgt = core.resolve_target(p, self.fi.extra, self.fi.rule)
        need = bool(tgt and core.page_needs_convert(p, self.fi.extra, self.fi.rule))
        dpi = self._fit_dpi(p, tgt if need else None)
        try:
            pv0 = core.render_page(self.fi.path, self.idx, None, dpi)
        except Exception as exc:
            self.cap0.configure(text=f"渲染失败：{exc}")
            self.lbl0.configure(image="")
            self.cap1.configure(text="")
            self.lbl1.configure(image="")
            return
        self._imgs = [self._photo(pv0)]
        self.lbl0.configure(image=self._imgs[0])
        self.cap0.configure(text=f"第 {p.index} 页   {p.size_text}   旋转 {p.rotation}°   "
                                 f"判定 {p.kind}")

        if need:
            try:
                pv1 = core.render_page(self.fi.path, self.idx, tgt, dpi)
            except Exception as exc:
                self.cap1.configure(text=f"渲染失败：{exc}")
                self.lbl1.configure(image="")
                self.var_note.set("")
                return
            self._imgs.append(self._photo(pv1))
            self.lbl1.configure(image=self._imgs[1])
            self.cap1.configure(
                text=f"第 {p.index} 页   {pv1.width_mm:.1f}×{pv1.height_mm:.1f}mm   "
                     f"缩放 {pv1.scale * 100:.1f}%")
            self.var_note.set(f"留白：左右 {pv1.margin_x_mm:.1f}mm，上下 {pv1.margin_y_mm:.1f}mm"
                              f"（等比缩放，不会拉伸变形）")
        else:
            self.lbl1.configure(image="")
            self.cap1.configure(text="本页不转换：" + self._why_not(p, tgt))
            self.var_note.set("")

    def _why_not(self, p: core.PageInfo, tgt) -> str:
        if self.fi.rule.mode == core.RULE_SKIP:
            return "该文件被设为【跳过】"
        if tgt:
            return f"已经是 {tgt[0]:g}×{tgt[1]:g}mm"
        return f"判定为「{p.kind}」，默认规则不处理（可右键设定转换尺寸）"

    def _allowed(self) -> list[int]:
        if self.var_only.get() and self.conv:
            return self.conv
        return list(range(len(self.fi.pages)))

    def _step(self, delta: int) -> None:
        allowed = self._allowed()
        try:
            pos = allowed.index(self.idx)
        except ValueError:
            pos = 0
        self.idx = allowed[max(0, min(len(allowed) - 1, pos + delta))]
        self._render()

    def _jump(self) -> None:
        try:
            n = int(float(self.var_page.get())) - 1
        except ValueError:
            self.var_page.set(str(self.idx + 1))
            return
        self.idx = max(0, min(n, len(self.fi.pages) - 1))
        self._render()


# ---------------------------------------------------------------- 主窗口
class App(tk.Tk):
    def __init__(self, zoom: "float | None" = None) -> None:
        super().__init__()

        # ---- 先定缩放，再建界面：所有像素都过 px()
        self._settings = _settings_path()
        saved = _load_settings(self._settings)
        if zoom is None:
            try:
                zoom = float(saved.get("zoom", 1.0))
            except Exception:
                zoom = 1.0
        self.zoom = zoom if 0.8 <= zoom <= 3.0 else 1.0
        self.sys_dpi = self._detect_dpi()
        self._apply_scaling()
        self._sash_frac = float(saved.get("sash", 0.62) or 0.62)

        self.paths: list[str] = []              # 用户添加的文件/文件夹
        self.infos: list[core.FileInfo] = []    # 扫描结果
        self.rules: dict[str, core.Rule] = {}   # 逐文件规则（按规范化路径），重扫不丢
        self.row_of_iid: dict[str, core.FileInfo] = {}
        self.busy = False
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._log_lines: list[str] = []
        self._current_path: str = ""
        self._previews: list[PreviewWindow] = []
        self.menu: "tk.Menu | None" = None

        self.var_tol = tk.StringVar(value="3.0")
        self.var_mode = tk.StringVar(value="backup")   # backup / overwrite / outdir
        self.var_outdir = tk.StringVar(value="")
        self.var_extra = tk.BooleanVar(value=False)
        self.var_only_need = tk.BooleanVar(value=False)
        self.var_zoom = tk.StringVar(value=self._zoom_label())
        self.var_status = tk.StringVar(value="就绪：先添加 PDF 文件或文件夹，然后点【扫描尺寸】")
        self.var_stats = tk.StringVar(value="还没有加载文件")

        self.title(f"{APP_TITLE}  {APP_VER}")
        self._init_style()
        self._build_ui()
        self._place_window()
        self.log(f"系统 DPI 约 {self.sys_dpi:.0f}（系统缩放 {self.sys_dpi / 96 * 100:.0f}%），"
                 f"界面再放大 {self.zoom * 100:.0f}%；字太小可用右下角【界面缩放】调整")
        self._after_id = self.after(80, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 缩放 ----------
    def _detect_dpi(self) -> float:
        """
        只测一次：Tk 的 scaling 是「显示级」的，一旦被本程序改过，
        winfo_fpixels('1i') 就不再是系统原始 DPI，重复探测会自我放大。
        """
        if _SYS_DPI_CACHE:
            return _SYS_DPI_CACHE[0]
        dpi = 96.0
        try:
            dpi = max(dpi, float(self.winfo_fpixels("1i")))
        except Exception:
            pass
        if sys.platform == "win32":
            dpi = max(dpi, system_dpi())
        dpi = dpi if dpi >= 72 else 96.0
        _SYS_DPI_CACHE.append(dpi)
        return dpi

    def _apply_scaling(self) -> None:
        self.dpi = self.sys_dpi * self.zoom
        self.k = self.dpi / 96.0
        try:                                     # 让“磅”按真实 DPI 换成像素
            self.tk.call("tk", "scaling", self.dpi / 72.0)
        except Exception:
            pass

    def px(self, v: float) -> int:
        """设计稿像素 -> 当前缩放下的像素"""
        return max(1, int(round(v * self.k)))

    def _zoom_label(self) -> str:
        for text, val in ZOOM_CHOICES:
            if abs(val - self.zoom) < 1e-6:
                return text
        return f"{self.zoom * 100:.0f}%"

    def _pick_family(self) -> str:
        try:
            fams = {f.lower() for f in tkfont.families(self)}
        except Exception:
            fams = set()
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                     "PingFang SC", "WenQuanYi Micro Hei", "Noto Sans CJK SC",
                     "DejaVu Sans"):
            if name.lower() in fams:
                return name
        return "TkDefaultFont"

    def _save_setting(self, **kw) -> None:
        data = _load_settings(self._settings)
        data.update(kw)
        _save_settings(self._settings, data)

    # ---------- 外观 ----------
    def _init_style(self) -> None:
        self.family = self._pick_family()
        fs = BASE_FONT_PT
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        self.option_add("*Font", "{%s} %d" % (self.family, fs))
        self.option_add("*TCombobox*Listbox.font", "{%s} %d" % (self.family, fs))
        style.configure(".", font=(self.family, fs))
        style.configure("TButton", font=(self.family, fs))
        style.configure("TLabel", font=(self.family, fs))
        style.configure("TCheckbutton", font=(self.family, fs))
        style.configure("TRadiobutton", font=(self.family, fs))
        style.configure("TNotebook.Tab", font=(self.family, fs),
                        padding=(self.px(12), self.px(5)))
        style.configure("Treeview", font=(self.family, fs), rowheight=self.px(26))
        style.configure("Treeview.Heading", font=(self.family, fs, "bold"),
                        padding=(self.px(4), self.px(4)))
        style.configure("Run.TButton", font=(self.family, fs + 1, "bold"),
                        padding=(self.px(10), self.px(6)))
        style.configure("TLabelframe.Label", font=(self.family, fs, "bold"))
        style.configure("Stats.TLabel", font=(self.family, fs, "bold"), foreground="#123")

    # ---------- 界面 ----------
    def _build_ui(self) -> None:
        P = self.px
        self.menu = None

        # ---- 顶部按钮条
        bar = ttk.Frame(self, padding=(P(10), P(8), P(10), P(4)))
        bar.pack(fill="x")
        ttk.Button(bar, text="＋ 添加 PDF 文件", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="＋ 添加文件夹", command=self.add_folder).pack(side="left", padx=(P(6), 0))
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(side="left", padx=(P(6), 0))
        ttk.Button(bar, text="清空列表", command=self.clear_all).pack(side="left", padx=(P(6), 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=P(10))
        ttk.Button(bar, text="打开所在文件夹", command=self.open_folder).pack(side="left")
        ttk.Button(bar, text="生成示例文件…", command=self.make_samples).pack(side="left", padx=(P(6), 0))

        # ---- 选项区
        opt = ttk.LabelFrame(self, text=" 选项 ", padding=(P(10), P(6)))
        opt.pack(fill="x", padx=P(10), pady=(P(2), P(6)))

        row1 = ttk.Frame(opt)
        row1.pack(fill="x")
        ttk.Label(row1, text="接近判定容差：").pack(side="left")
        ttk.Spinbox(row1, from_=0.1, to=20.0, increment=0.5, width=6,
                    textvariable=self.var_tol).pack(side="left")
        ttk.Label(row1, text="%（宽、高都在标准尺寸 ±该比例内才算“接近”）").pack(side="left")
        ttk.Checkbutton(row1, text="附加规则：横版近A4→A4横版、竖版近A3→A3竖版",
                        variable=self.var_extra, command=self._refresh_rules_display
                        ).pack(side="left", padx=(P(18), 0))

        row2 = ttk.Frame(opt)
        row2.pack(fill="x", pady=(P(6), 0))
        ttk.Label(row2, text="处理方式：").pack(side="left")
        ttk.Radiobutton(row2, text="覆盖原文件（自动备份 .bak）", value="backup",
                        variable=self.var_mode, command=self._sync_outdir).pack(side="left")
        ttk.Radiobutton(row2, text="覆盖原文件（不备份）", value="overwrite",
                        variable=self.var_mode, command=self._sync_outdir).pack(side="left", padx=(P(8), 0))
        ttk.Radiobutton(row2, text="另存到目录：", value="outdir",
                        variable=self.var_mode, command=self._sync_outdir).pack(side="left", padx=(P(8), 0))
        self.ent_outdir = ttk.Entry(row2, textvariable=self.var_outdir, width=30, state="disabled")
        self.ent_outdir.pack(side="left")
        self.btn_outdir = ttk.Button(row2, text="选择…", width=7, state="disabled",
                                     command=self.choose_outdir)
        self.btn_outdir.pack(side="left", padx=(P(4), 0))

        # ---- 操作按钮
        act = ttk.Frame(self, padding=(P(10), 0))
        act.pack(fill="x")
        self.btn_scan = ttk.Button(act, text="① 扫描尺寸", style="Run.TButton", command=self.do_scan)
        self.btn_scan.pack(side="left")
        self.btn_convert = ttk.Button(act, text="② 执行转换", style="Run.TButton", command=self.do_convert)
        self.btn_convert.pack(side="left", padx=(P(8), 0))
        ttk.Button(act, text="导出报告 CSV", command=self.export_csv).pack(side="left", padx=(P(8), 0))
        ttk.Button(act, text="预览转换前后…", command=self.preview_compare).pack(side="left", padx=(P(8), 0))

        # ---- 底部：状态 + 进度条 + 界面缩放
        # 必须先于 paned 打包（side=bottom），否则 pack 会先满足 paned 的需求，
        # 把底栏挤成 1~2 像素高（高分屏缩放下尤其明显）。
        self._build_foot()

        # ---- 主体：上=文件表，下=明细/日志（分割条可拖动）
        self.paned = tk.PanedWindow(self, orient="vertical", sashwidth=P(10),
                                    sashrelief="raised", opaqueresize=True,
                                    bg="#c9c9c9", bd=1, relief="sunken")
        self.paned.pack(fill="both", expand=True, padx=P(10), pady=(P(6), P(4)))

        top = ttk.Frame(self.paned)

        # 统计条 + 筛选
        srow = ttk.Frame(top)
        srow.pack(fill="x", pady=(0, P(4)))
        ttk.Label(srow, textvariable=self.var_stats, style="Stats.TLabel").pack(side="left")
        ttk.Checkbutton(srow, text="只看需转换", variable=self.var_only_need,
                        command=self._render_files).pack(side="right")
        ttk.Button(srow, text="全选需转换", command=self.select_need).pack(side="right", padx=(0, P(8)))
        ttk.Label(srow, text="右键行可打开文件 / 设定尺寸 / 单独转换",
                  foreground="#777").pack(side="right", padx=(0, P(16)))

        holder = ttk.Frame(top)
        holder.pack(fill="both", expand=True)

        cols = ("no", "name", "pages", "sizes", "kinds", "target", "conv", "status")
        self.tree = ttk.Treeview(holder, columns=cols, show="headings", selectmode="extended")
        heads = [("no", "序号", 52, "center"), ("name", "文件名", 300, "w"),
                 ("pages", "页数", 56, "center"), ("sizes", "页面尺寸(mm) 与页数", 250, "w"),
                 ("kinds", "判定", 170, "w"), ("target", "目标尺寸", 110, "w"),
                 ("conv", "可转换", 68, "center"), ("status", "状态", 150, "w")]
        for cid, text, w, anchor in heads:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=P(w), minwidth=P(w // 2), anchor=anchor,
                             stretch=(cid in ("name", "sizes", "kinds", "status")))
        vs = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(holder, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.tree.tag_configure("need", foreground="#0a5d2a")
        self.tree.tag_configure("none", foreground="#666666")
        self.tree.tag_configure("err", foreground="#b00020")
        self.tree.tag_configure("skip", foreground="#8a6d00")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_file)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Control-a>", self._select_all)
        self.paned.add(top, stretch="always", minsize=P(150))

        bottom = ttk.Notebook(self.paned)
        f1 = ttk.Frame(bottom)
        pcols = ("page", "mm", "pt", "rot", "kind", "act")
        self.ptree = ttk.Treeview(f1, columns=pcols, show="headings")
        for cid, text, w, anchor in [("page", "页码", 66, "center"), ("mm", "尺寸(mm)", 175, "w"),
                                     ("pt", "尺寸(pt)", 195, "w"), ("rot", "旋转", 66, "center"),
                                     ("kind", "判定", 120, "w"), ("act", "处理", 170, "w")]:
            self.ptree.heading(cid, text=text)
            self.ptree.column(cid, width=P(w), minwidth=P(w // 2), anchor=anchor,
                              stretch=(cid in ("mm", "pt", "act")))
        pvs = ttk.Scrollbar(f1, orient="vertical", command=self.ptree.yview)
        self.ptree.configure(yscrollcommand=pvs.set)
        self.ptree.pack(side="left", fill="both", expand=True)
        pvs.pack(side="right", fill="y")
        bottom.add(f1, text="  逐页明细  ")

        f2 = ttk.Frame(bottom)
        log_family = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
        self.txt = tk.Text(f2, wrap="word", height=8, font=(log_family, BASE_FONT_PT - 1),
                           padx=P(6), pady=P(4))
        tvs = ttk.Scrollbar(f2, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=tvs.set, state="disabled")
        self.txt.pack(side="left", fill="both", expand=True)
        tvs.pack(side="right", fill="y")
        bottom.add(f2, text="  运行日志  ")
        self.paned.add(bottom, stretch="always", minsize=P(90))

        self._sync_outdir()
        for logline in self._log_lines:           # 缩放重建后恢复日志
            self._append_log(logline)
        if self.infos:
            self._render_files()
            for iid, fi in self.row_of_iid.items():
                if fi.path == self._current_path:
                    self.tree.selection_set(iid)
                    self._render_pages(fi)
                    break
        self.after(80, self._restore_sash)

    def _build_foot(self) -> None:
        P = self.px
        foot = ttk.Frame(self, padding=(P(10), P(2), P(10), P(8)))
        foot.pack(side="bottom", fill="x")
        ttk.Label(foot, textvariable=self.var_status, foreground="#555",
                  anchor="w").pack(fill="x")
        line = ttk.Frame(foot)
        line.pack(fill="x", pady=(P(2), 0))
        cb = ttk.Combobox(line, textvariable=self.var_zoom, state="readonly", width=16,
                          values=[t for t, _ in ZOOM_CHOICES])
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", self._on_zoom)
        ttk.Label(line, text="界面缩放：").pack(side="right", padx=(P(12), P(4)))
        self.pb = ttk.Progressbar(line, mode="determinate")
        self.pb.pack(side="left", fill="x", expand=True, padx=(0, P(6)))

    def _place_window(self) -> None:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(self.px(1340), int(sw * 0.94))
        h = min(self.px(880), int(sh * 0.92))
        x = max(0, (sw - w) // 2)
        y = max(0, int((sh - h) / 3))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(min(self.px(1000), w), min(self.px(640), h))

    def _restore_sash(self) -> None:
        try:
            h = self.paned.winfo_height()
            if h > self.px(120):
                y = int(h * min(0.9, max(0.2, self._sash_frac)))
                self.paned.sash_place(0, 0, y)
        except Exception:
            pass

    # ---------- 缩放切换 ----------
    def _on_zoom(self, _evt=None) -> None:
        if self.busy:
            self.var_zoom.set(self._zoom_label())
            return
        val = dict(ZOOM_CHOICES).get(self.var_zoom.get(), 1.0)
        if abs(val - self.zoom) < 1e-6:
            return
        self.zoom = val
        self._apply_scaling()
        self._rebuild()
        self._save_setting(zoom=val)
        self.log(f"界面缩放调整为 {val * 100:.0f}%（实际约 {self.dpi:.0f} dpi），下次启动沿用")

    def _rebuild(self) -> None:
        for pv in list(self._previews):
            try:
                pv.destroy()
            except Exception:
                pass
        self._previews.clear()
        for child in self.winfo_children():
            child.destroy()
        self._init_style()
        self._build_ui()
        self._place_window()

    def _on_close(self) -> None:
        try:
            self.after_cancel(self._after_id)
        except Exception:
            pass
        try:
            h = self.paned.winfo_height()
            if h > 50:
                self._sash_frac = self.paned.sash_coord(0)[1] / h
                self._save_setting(sash=round(self._sash_frac, 3))
        except Exception:
            pass
        for pv in list(self._previews):
            try:
                pv._close()
            except Exception:
                pass
        self.destroy()

    # ---------- 小工具 ----------
    def log(self, text: str) -> None:
        text = text.rstrip()
        self._log_lines.append(text)
        if len(self._log_lines) > 2000:
            del self._log_lines[:1000]
        self._append_log(text)

    def _append_log(self, text: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _sync_outdir(self) -> None:
        on = self.var_mode.get() == "outdir"
        self.ent_outdir.configure(state="normal" if on else "disabled")
        self.btn_outdir.configure(state="normal" if on else "disabled")

    def choose_outdir(self) -> None:
        d = filedialog.askdirectory(title="选择转换结果的输出目录")
        if d:
            self.var_outdir.set(d)
            self.var_mode.set("outdir")
            self._sync_outdir()

    def tol(self) -> float:
        try:
            v = float(self.var_tol.get().replace("%", "").strip())
        except ValueError:
            v = 3.0
        return max(0.0, min(v, 50.0)) / 100.0

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        st = "disabled" if busy else "normal"
        for b in (self.btn_scan, self.btn_convert):
            b.configure(state=st)
        self.configure(cursor="watch" if busy else "")

    # ---------- 添加 / 移除 ----------
    def add_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择 PDF 文件", filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        if not files:
            return
        self._add_paths(list(files))

    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="选择包含 PDF 的文件夹（含子文件夹）")
        if not d:
            return
        self._add_paths([d])

    def _add_paths(self, paths: list[str]) -> None:
        before = len(core.collect_pdfs(self.paths))
        for p in paths:
            if p not in self.paths:
                self.paths.append(p)
        after = len(core.collect_pdfs(self.paths))
        self.log(f"已添加 {len(paths)} 项，当前共 {after} 个 PDF 文件"
                 + ("" if after > before else "（无新增）"))
        self.var_status.set(f"已加载 {after} 个文件，点【① 扫描尺寸】开始")
        self.infos = []
        self._current_path = ""
        self.tree.delete(*self.tree.get_children())
        self.ptree.delete(*self.ptree.get_children())
        self._update_stats()

    def make_samples(self) -> None:
        d = filedialog.askdirectory(title="选择存放示例 PDF 的文件夹")
        if not d:
            return
        try:
            made = core.make_samples(d)
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            return
        self.log(f"已生成 {len(made)} 个示例 PDF 到 {d}")
        if messagebox.askyesno("已生成示例",
                               f"已生成 {len(made)} 个示例 PDF：\n{d}\n\n"
                               "要加入列表并立即扫描吗？"):
            self.paths = [d]
            self.do_scan()

    def remove_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        rm = {self.row_of_iid[i].path for i in sel if i in self.row_of_iid}
        rm_abs = {os.path.normcase(os.path.abspath(x)) for x in rm}
        self.infos = [fi for fi in self.infos
                      if os.path.normcase(os.path.abspath(fi.path)) not in rm_abs]
        keep_dirs = [p for p in self.paths if os.path.isdir(p)
                     and not any(f.startswith(os.path.normcase(os.path.abspath(p)) + os.sep)
                                 for f in rm_abs)]
        keep_files = [p for p in self.paths if os.path.isfile(p)
                      and os.path.normcase(os.path.abspath(p)) not in rm_abs]
        self.paths = keep_dirs + keep_files
        self._render_files()

    def clear_all(self) -> None:
        self.paths.clear()
        self.infos.clear()
        self.rules.clear()
        self.row_of_iid.clear()
        self._current_path = ""
        self.tree.delete(*self.tree.get_children())
        self.ptree.delete(*self.ptree.get_children())
        self.var_status.set("已清空")
        self.pb["value"] = 0
        self._update_stats()

    def open_folder(self) -> None:
        infos = self._selected_infos()
        if not infos:
            messagebox.showinfo("提示", "请先在列表里选中一行")
            return
        try:
            open_in_system(os.path.dirname(os.path.abspath(infos[0].path)))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_file(self, infos: "list[core.FileInfo] | None" = None) -> None:
        infos = infos if infos is not None else self._selected_infos()
        if not infos:
            messagebox.showinfo("提示", "请先在列表里选中一行")
            return
        bad = []
        for fi in infos[:15]:
            try:
                open_in_system(fi.path)
            except Exception as exc:
                bad.append(f"{fi.name}：{exc}")
        if bad:
            messagebox.showerror("打开失败", "\n".join(bad))

    def copy_path(self) -> None:
        infos = self._selected_infos()
        if not infos:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(fi.path for fi in infos))
        self.var_status.set(f"已复制 {len(infos)} 个文件路径到剪贴板")

    # ---------- 选择辅助 ----------
    def _selected_infos(self) -> list[core.FileInfo]:
        return [self.row_of_iid[i] for i in self.tree.selection() if i in self.row_of_iid]

    def _select_all(self, _evt=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def select_need(self) -> None:
        iids = [iid for iid, fi in self.row_of_iid.items()
                if fi.ok and fi.convertible_pages]
        if not iids:
            messagebox.showinfo("提示", "列表里没有需要转换的文件")
            return
        self.tree.selection_set(iids)
        self.var_status.set(f"已选中 {len(iids)} 个需转换的文件")

    # ---------- 右键菜单 ----------
    def _on_right_click(self, evt) -> None:
        row = self.tree.identify_row(evt.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.tree.focus(row)
        sel = self._selected_infos()
        if not sel:
            return
        self._build_menu(sel)
        try:
            self.menu.tk_popup(evt.x_root, evt.y_root)   # type: ignore[union-attr]
        finally:
            try:
                self.menu.grab_release()                 # type: ignore[union-attr]
            except Exception:
                pass

    def _build_menu(self, sel: list[core.FileInfo]) -> None:
        if self.menu is not None:
            try:
                self.menu.destroy()
            except Exception:
                pass
        m = tk.Menu(self, tearoff=0)
        n = len(sel)
        if n == 1:
            m.add_command(label="打开文件", command=lambda: self.open_file(sel))
            m.add_command(label="打开所在文件夹",
                          command=lambda: open_in_system(os.path.dirname(os.path.abspath(sel[0].path))))
            m.add_command(label="预览转换前后对比…", command=lambda: self.preview_compare(sel))
        else:
            m.add_command(label=f"打开选中的 {min(n, 15)} 个文件", command=lambda: self.open_file(sel))
            m.add_command(label="预览第一个文件的转换前后…", command=lambda: self.preview_compare(sel))
        m.add_separator()
        m.add_command(label=(f"批量设定转换尺寸…（{n} 个文件）" if n > 1 else "设定转换尺寸…"),
                      command=lambda: self.set_rule_dialog(sel))
        if any(fi.rule.mode != core.RULE_AUTO for fi in sel):
            m.add_command(label="恢复为自动规则",
                          command=lambda: self.apply_rule(sel, core.Rule()))
        m.add_separator()
        m.add_command(label=(f"仅转换选中的 {n} 个文件" if n > 1 else "仅转换此文件"),
                      command=lambda: self.do_convert(targets=sel))
        m.add_separator()
        m.add_command(label="复制文件路径", command=self.copy_path)
        m.add_command(label="从列表移除", command=self.remove_selected)
        self.menu = m

    def _on_double(self, evt) -> str | None:
        if self.tree.identify_region(evt.x, evt.y) != "cell":
            return None
        row = self.tree.identify_row(evt.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.preview_compare()
        return "break"

    # ---------- 逐文件规则 ----------
    def set_rule_dialog(self, infos: "list[core.FileInfo] | None" = None) -> None:
        infos = infos if infos is not None else self._selected_infos()
        if not infos:
            messagebox.showinfo("提示", "请先在列表里选中一个或多个文件")
            return
        dlg = RuleDialog(self, infos)
        rule = dlg.show()
        if rule is not None:
            self.apply_rule(infos, rule)

    def apply_rule(self, infos: list[core.FileInfo], rule: core.Rule) -> None:
        for fi in infos:
            fi.rule = rule
            fi.status = fi.status_text
            key = os.path.normcase(os.path.abspath(fi.path))
            if rule.mode == core.RULE_AUTO:
                self.rules.pop(key, None)
            else:
                self.rules[key] = rule
        self.log(f"已把 {len(infos)} 个文件的目标设为「{rule.label}」")
        self._render_files()
        cur = self._selected_infos()
        if cur:
            self._render_pages(cur[0])

    def _refresh_rules_display(self) -> None:
        """勾/取消附加规则后，可转换页数会变，重算显示"""
        for fi in self.infos:
            fi.extra = self.var_extra.get()
            fi.status = fi.status_text
        self._render_files()

    # ---------- 扫描 ----------
    def do_scan(self) -> None:
        if self.busy:
            return
        files = core.collect_pdfs(self.paths)
        if not files:
            messagebox.showinfo("提示", "请先添加 PDF 文件或包含 PDF 的文件夹")
            return
        tol, extra = self.tol(), self.var_extra.get()
        self._set_busy(True)
        self.log(f"\n=== 开始扫描 {len(files)} 个文件（容差 ±{tol * 100:g}%"
                 f"{'，含附加规则' if extra else ''}）===")
        self.pb.configure(maximum=len(files), value=0)

        def work() -> None:
            try:
                def prog(msg: str, cur: int, total: int) -> None:
                    self.q.put(("prog", msg, cur, total))

                infos = core.scan_paths(self.paths, tol=tol, progress=prog, extra=extra)
                self.q.put(("scan_done", infos))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 转换 ----------
    def do_convert(self, targets: "list[core.FileInfo] | None" = None) -> None:
        if self.busy:
            return
        if not self.infos:
            messagebox.showinfo("提示", "请先【① 扫描尺寸】，确认哪些文件需要处理")
            return
        pool = targets if targets is not None else self.infos
        todo = [fi for fi in pool if fi.ok and fi.convertible_pages]
        if not todo:
            if targets is not None:
                messagebox.showinfo("提示", "选中的文件都不需要转换。")
            else:
                messagebox.showinfo("提示", "没有需要转换的页面。\n"
                                            "（只有【竖版且接近A4】和【横版且接近A3】的页面会被处理）")
            return
        pages = sum(len(fi.convertible_pages) for fi in todo)
        mode = self.var_mode.get()
        outdir = self.var_outdir.get().strip()
        if mode == "outdir":
            if not outdir:
                messagebox.showwarning("提示", "请先选择输出目录")
                return
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception as exc:
                messagebox.showerror("输出目录不可用", str(exc))
                return
        where = {"backup": "覆盖原文件（生成 .bak 备份）", "overwrite": "直接覆盖原文件（不备份）",
                 "outdir": f"另存到 {outdir}"}[mode]
        forced = [fi for fi in todo if fi.rule.mode == core.RULE_TARGET]
        scope = ""
        if forced:
            scope = "\n其中 {} 个文件按【指定尺寸】处理：{}\n".format(
                len(forced), "、".join(sorted({f.rule.label for f in forced})))
        if not messagebox.askyesno(
                "确认转换",
                f"将处理 {len(todo)} 个文件，共 {pages} 页。{scope}\n处理方式：{where}\n\n继续？"):
            return

        tol, extra = self.tol(), self.var_extra.get()
        backup = mode == "backup"
        out_dir = outdir if mode == "outdir" else None
        self._set_busy(True)
        self.log(f"\n=== 开始转换：{len(todo)} 个文件 / {pages} 页 ===")
        self.pb.configure(maximum=len(todo), value=0)

        def work() -> None:
            try:
                def prog(msg: str, cur: int, total: int) -> None:
                    self.q.put(("prog", msg, cur, total))

                results = []
                for i, fi in enumerate(todo, 1):
                    op = os.path.join(out_dir, os.path.basename(fi.path)) if out_dir else None
                    results.append(core.convert_file(fi.path, tol=tol, backup=backup,
                                                     out_path=op, extra=extra,
                                                     rule=fi.rule))
                    self.q.put(("prog", f"已处理 {i}/{len(todo)}", i, len(todo)))
                self.q.put(("conv_done", results))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 预览 ----------
    def preview_compare(self, infos: "list[core.FileInfo] | None" = None) -> None:
        infos = infos if infos is not None else self._selected_infos()
        if not infos:
            messagebox.showinfo("提示", "请先在列表里选中一个文件")
            return
        fi = infos[0]
        if not fi.ok:
            messagebox.showerror("无法预览", f"{fi.name}\n{fi.error}")
            return
        if not fi.pages:
            messagebox.showinfo("提示", "该文件没有页面")
            return
        pv = PreviewWindow(self, fi)
        self._previews.append(pv)

    # ---------- 渲染 ----------
    def _visible_infos(self) -> list[core.FileInfo]:
        if self.var_only_need.get():
            return [fi for fi in self.infos if fi.ok and fi.convertible_pages]
        return list(self.infos)

    def _render_files(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.row_of_iid.clear()
        for i, fi in enumerate(self._visible_infos(), 1):
            if not fi.ok:
                tag, conv, status = "err", "-", fi.status_text
            else:
                n = len(fi.convertible_pages)
                conv = str(n) if n else "0"
                status = fi.status_text
                if fi.rule.mode == core.RULE_SKIP:
                    tag = "skip"
                else:
                    tag = "need" if n else "none"
            iid = self.tree.insert("", "end", values=(
                i, fi.name, fi.page_count, fi.sizes_text, fi.kinds_text,
                fi.rule.label, conv, status), tags=(tag,))
            self.row_of_iid[iid] = fi
        self._update_stats()

    def _update_stats(self) -> None:
        total = len(self.infos)
        ok = [fi for fi in self.infos if fi.ok]
        skip = [fi for fi in ok if fi.rule.mode == core.RULE_SKIP]
        need = [fi for fi in ok if fi.convertible_pages]
        plain = len(ok) - len(need) - len(skip)
        bad = total - len(ok)
        pages = sum(len(fi.convertible_pages) for fi in ok)
        parts = [f"文件 {total} 个", f"需转换 {len(need)} 个（{pages} 页）",
                 f"无需转换 {plain} 个"]
        if skip:
            parts.append(f"已跳过 {len(skip)} 个")
        if bad:
            parts.append(f"读取失败 {bad} 个")
        txt = " ｜ ".join(parts) if total else "还没有加载文件"
        sel = len(self.tree.selection())
        if sel > 1:
            txt += f"   ← 已选 {sel} 个"
        if self.var_only_need.get() and total:
            txt += "   【列表已筛选】"
        self.var_stats.set(txt)

    def _render_pages(self, fi: core.FileInfo) -> None:
        self.ptree.delete(*self.ptree.get_children())
        self._current_path = fi.path
        if not fi.ok:
            return
        for p in fi.pages:
            tgt = core.resolve_target(p, fi.extra, fi.rule)
            need = core.page_needs_convert(p, fi.extra, fi.rule)
            if need and tgt:
                act = f"→ {tgt[0]:g}×{tgt[1]:g}mm"
            elif tgt:
                act = "已是标准尺寸"
            elif fi.rule.mode == core.RULE_SKIP:
                act = "跳过（文件规则）"
            else:
                act = "不动"
            self.ptree.insert("", "end", values=(p.index, p.size_text, p.size_pt_text,
                                                p.rotation, p.kind, act))

    def _on_select_file(self, _evt=None) -> None:
        sel = self.tree.selection()
        self._update_stats()
        if not sel:
            return
        fi = self.row_of_iid.get(sel[0])
        if fi:
            self._render_pages(fi)

    # ---------- 导出 ----------
    def export_csv(self) -> None:
        if not self.infos:
            messagebox.showinfo("提示", "请先扫描")
            return
        path = filedialog.asksaveasfilename(
            title="导出扫描报告", defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")], initialfile="PDF页面尺寸报告.csv")
        if not path:
            return
        try:
            core.write_report_csv(self.infos, path)
            self.log(f"报告已导出：{path}")
            messagebox.showinfo("完成", f"报告已导出：\n{path}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    # ---------- 队列轮询 ----------
    def _drain_queue(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "prog":
                    _, text, cur, total = msg
                    self.pb.configure(maximum=max(total, 1), value=cur)
                    self.var_status.set(text)
                elif kind == "scan_done":
                    self._on_scan_done(msg[1])
                elif kind == "conv_done":
                    self._on_conv_done(msg[1])
                elif kind == "error":
                    self._set_busy(False)
                    self.log("出错：\n" + msg[1])
                    messagebox.showerror("出错了", msg[1].splitlines()[-1] if msg[1] else "未知错误")
        except queue.Empty:
            pass
        self._after_id = self.after(80, self._drain_queue)

    def _on_scan_done(self, infos: list[core.FileInfo]) -> None:
        # 重扫后把之前设定的逐文件规则接回来
        for fi in infos:
            key = os.path.normcase(os.path.abspath(fi.path))
            fi.rule = self.rules.get(key, core.Rule())
            fi.status = fi.status_text
        self.infos = infos
        self._set_busy(False)
        self.pb["value"] = self.pb["maximum"]
        self._render_files()
        for fi in infos:
            if not fi.ok:
                self.log(f"❌ {fi.name}：{fi.error}")
            else:
                n = len(fi.convertible_pages)
                self.log(f"{'✔' if n else '·'} {fi.name}  {fi.page_count}页  "
                         f"{fi.sizes_text}  [{fi.kinds_text}]  {fi.status_text}")
        vis = self.tree.get_children()
        if vis:
            self.tree.selection_set(vis[0])
            fi0 = self.row_of_iid.get(vis[0])
            if fi0:
                self._render_pages(fi0)

    def _on_conv_done(self, results: list[core.ConvertResult]) -> None:
        ok = [r for r in results if r.ok and r.converted]
        skip = [r for r in results if r.ok and not r.converted]
        bad = [r for r in results if not r.ok]
        self._set_busy(False)
        self.pb["value"] = self.pb["maximum"]
        for r in ok:
            self.log(f"✅ {os.path.basename(r.path)}  转换 {r.converted}/{r.total} 页"
                     + (f"  →  {r.out_path}" if r.out_path and r.out_path != r.path else "")
                     + (f"（备份 {os.path.basename(r.backup_path)}）" if r.backup_path else ""))
        for r in skip:
            self.log(f"· {os.path.basename(r.path)}  跳过：{r.skipped_reason}")
        for r in bad:
            self.log(f"❌ {os.path.basename(r.path)}  {r.error}")
        messagebox.showinfo(
            "转换完成",
            f"成功 {len(ok)} 个（共 {sum(r.converted for r in ok)} 页）\n"
            f"跳过 {len(skip)} 个\n失败 {len(bad)} 个"
            + ("\n\n失败详情见运行日志。" if bad else ""))
        # 转换后重新扫描，界面显示真实的新尺寸（逐文件规则会自动接回）
        self.do_scan()


# ---------------------------------------------------------------- 入口
def main() -> int:
    # 必须在创建窗口之前声明 DPI 感知，否则高分屏上文字会偏小
    enable_high_dpi()

    # 命令行入口（打包成 exe 后同样可用）：
    #   xxx.exe --check            自检，返回码 0 表示正常
    #   xxx.exe scan  <路径...>     只列尺寸
    #   xxx.exe convert <路径...>   执行转换
    argv = sys.argv[1:]
    if argv and argv[0] == "--check":
        ok, msg = core.self_check()
        print(("OK " if ok else "FAIL ") + msg)
        return 0 if ok else 1
    if argv and argv[0] in ("scan", "convert"):
        return core._cli(argv)

    if sys.version_info < (3, 8):
        print("需要 Python 3.8 及以上版本")
        return 1
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        try:
            import fitz  # noqa: F401
        except ImportError:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("缺少依赖",
                                 "未安装 PyMuPDF，请先执行：\n\npip install pymupdf")
            return 1
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
