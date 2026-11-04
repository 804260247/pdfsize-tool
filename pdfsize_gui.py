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
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import pdfsize_core as core

APP_TITLE = "PDF 页面尺寸检测 / A4·A3 标准化工具"
APP_VER = "v0.2"

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

AUTO_TEXT = "自动（跟随预设）"
SKIP_TEXT = "跳过（不处理）"

# 全局转换预设：(显示文本, 是否启用附加规则, 强制纸张/方向 或 None)
PRESETS: list[tuple[str, bool, "tuple[str, bool] | None"]] = [
    ("标准：竖版近A4→A4、横版近A3→A3", False, None),
    ("扩展：再加 横版近A4→A4横版、竖版近A3→A3竖版", True, None),
    ("全部统一为 A4 竖版（210×297）", False, ("A4", False)),
    ("全部统一为 A3 横版（420×297）", False, ("A3", True)),
    ("全部统一为 A4 横版（297×210）", False, ("A4", True)),
]

# 列表筛选
FILTER_ALL = "全部"
FILTER_NEED = "只看需转换"
FILTER_OTHER = "只看「其他」尺寸"
FILTER_DONE = "只看已转换"
FILTER_PLAIN = "只看无需转换"
FILTER_SKIP = "只看已跳过"
FILTER_ERR = "只看读取失败"
FILTERS = [FILTER_ALL, FILTER_NEED, FILTER_OTHER, FILTER_DONE,
           FILTER_PLAIN, FILTER_SKIP, FILTER_ERR]

# 「目标尺寸」列内下拉的选项（顺序 = 下拉顺序）
def _target_labels() -> list[str]:
    labels = [AUTO_TEXT]
    for paper in ("A4", "A3", "A5", "B5", "Letter"):
        for land in (False, True):
            if paper not in ("A4", "A3") and land:
                continue          # A5/B5/Letter 只给竖版，避免下拉过长
            labels.append(core.make_rule(paper, land).label)
    labels.append(SKIP_TEXT)
    return labels


TARGET_LABELS = _target_labels()


def _label_to_rule(label: str) -> core.Rule:
    """下拉文本 -> Rule；未知文本按「自动」处理"""
    if label == AUTO_TEXT:
        return core.Rule()
    if label == SKIP_TEXT:
        return core.RULE_SKIP_OBJ
    for paper in ("A4", "A3", "A5", "B5", "Letter"):
        for land in (False, True):
            try:
                r = core.make_rule(paper, land)
            except KeyError:
                continue
            if r.label == label:
                return r
    return core.Rule()


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


# ---------------------------------------------------------------- 按尺寸分类提取
GROUP_KIND_TEXT = "按判定类别（A4竖版 / A3横版 / A4横版 / A3竖版 / 其他）"
GROUP_EXACT_TEXT = "按实际尺寸（如 210x297mm、420x297mm）"
SPLIT_FILE_TEXT = "每类合并成一个 PDF（推荐）"
SPLIT_PAGE_TEXT = "每页单独一个 PDF"
SPLIT_COPY_TEXT = "整份文件复制（不拆分）"


class ExtractDialog(tk.Toplevel):
    """按页面尺寸把页面分类提取到不同子文件夹"""

    def __init__(self, master: "App", files: list[core.FileInfo],
                 default_out: str = "") -> None:
        super().__init__(master)
        self.app = master
        self.files = files
        self.result: "dict | None" = None
        P = master.px

        self.title("按页面尺寸分类提取")
        self.resizable(False, False)
        self.transient(master)

        self.var_group = tk.StringVar(value=GROUP_KIND_TEXT)
        self.var_split = tk.StringVar(value=SPLIT_FILE_TEXT)
        self.var_out = tk.StringVar(value=default_out)
        self.var_hint = tk.StringVar(value="")

        body = ttk.Frame(self, padding=P(14))
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=f"把选中的 {len(files)} 个文件按页面尺寸分类，分别存到子文件夹"
                             f"（原文件不动）",
                  font=(master.family, BASE_FONT_PT, "bold")).pack(anchor="w", pady=(0, P(8)))

        g1 = ttk.LabelFrame(body, text=" 分类依据 ", padding=(P(10), P(4)))
        g1.pack(fill="x", pady=(0, P(8)))
        for text in (GROUP_KIND_TEXT, GROUP_EXACT_TEXT):
            ttk.Radiobutton(g1, text=text, value=text, variable=self.var_group,
                            command=self._sync).pack(anchor="w")

        g2 = ttk.LabelFrame(body, text=" 输出方式 ", padding=(P(10), P(4)))
        g2.pack(fill="x", pady=(0, P(8)))
        for text in (SPLIT_FILE_TEXT, SPLIT_PAGE_TEXT, SPLIT_COPY_TEXT):
            ttk.Radiobutton(g2, text=text, value=text, variable=self.var_split,
                            command=self._sync).pack(anchor="w")

        g3 = ttk.LabelFrame(body, text=" 输出到 ", padding=(P(10), P(6)))
        g3.pack(fill="x", pady=(0, P(8)))
        row = ttk.Frame(g3)
        row.pack(fill="x")
        self.ent = ttk.Entry(row, textvariable=self.var_out, width=48)
        self.ent.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择…", width=8, command=self._choose).pack(side="left", padx=(P(6), 0))

        self.lbl_hint = ttk.Label(body, textvariable=self.var_hint, foreground="#444",
                                  wraplength=P(520), justify="left")
        self.lbl_hint.pack(anchor="w", pady=(0, P(10)))

        btns = ttk.Frame(body)
        btns.pack(fill="x")
        ttk.Button(btns, text="取消", width=8, command=self._cancel).pack(side="right")
        ttk.Button(btns, text="开始提取", width=10, command=self._ok).pack(side="right", padx=(0, P(6)))

        self._sync()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self._center()

    # ---------- 内部 ----------
    def _group(self) -> str:
        return (core.EXTRACT_GROUP_EXACT if self.var_group.get() == GROUP_EXACT_TEXT
                else core.EXTRACT_GROUP_KIND)

    def _split(self) -> str:
        s = self.var_split.get()
        if s == SPLIT_PAGE_TEXT:
            return core.EXTRACT_SPLIT_PAGE
        if s == SPLIT_COPY_TEXT:
            return core.EXTRACT_SPLIT_COPY
        return core.EXTRACT_SPLIT_FILE

    def _estimate(self) -> tuple[int, int]:
        """(预计生成文件数, 页数)"""
        group, split = self._group(), self._split()
        made = 0
        pages = 0
        for fi in self.files:
            if not fi.ok or not fi.pages:
                continue
            pages += fi.page_count
            if split == core.EXTRACT_SPLIT_COPY:
                made += 1
            elif split == core.EXTRACT_SPLIT_PAGE:
                made += fi.page_count
            else:
                buckets = set()
                for pg in fi.pages:
                    buckets.add(core._bucket_name(pg, group))
                made += max(1, len(buckets))
        return made, pages

    def _sync(self, _evt=None) -> None:
        made, pages = self._estimate()
        split = self._split()
        if split == core.EXTRACT_SPLIT_COPY:
            what = "整份复制到对应类别文件夹（尺寸不统一的文件放进「混合尺寸」）"
        elif split == core.EXTRACT_SPLIT_PAGE:
            what = "每页单独存成一个 PDF"
        else:
            what = "同一文件里同类尺寸的页面合并成一个 PDF"
        self.var_hint.set(f"预计：{pages} 页 → {made} 个 PDF。{what}。\n"
                          f"文件夹按「{('实际尺寸' if self._group() == core.EXTRACT_GROUP_EXACT else '判定类别')}」"
                          f"命名，同名文件会被覆盖（重复运行结果一致）。")

    def _choose(self) -> None:
        d = filedialog.askdirectory(title="选择提取输出目录",
                                    initialdir=self.var_out.get() or None)
        if d:
            self.var_out.set(d)
            self._sync()

    def _ok(self) -> None:
        out = self.var_out.get().strip()
        if not out:
            messagebox.showwarning("提示", "请先选择输出目录", parent=self)
            return
        self.result = {"out": out, "group": self._group(), "split": self._split()}
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

    def show(self) -> "dict | None":
        self.grab_set()
        self.wait_window()
        return self.result


# ---------------------------------------------------------------- 预览对比窗口
SCALE_ACTUAL = "100%（实际大小）"
SCALE_FIT = "适应窗口"
SCALE_CHOICES = [SCALE_ACTUAL, SCALE_FIT]
PREVIEW_MAX_PIXELS = 12.0e6      # 单张预览图最多像素数，防止 A0/大幅面把内存吃满


class _PreviewPane(ttk.LabelFrame):
    """带滚动条的预览面板，并且**自带一份「显示比例」**

    图比窗口大时可用滚轮/滚动条看全，比窗口小时居中。
    显示比例左右各自独立：左边「原始」和右边「转换后」可以分别选
    100%（实际大小）或适应窗口，互不影响。
    """

    def __init__(self, master: tk.Misc, title: str, P, on_scale=None) -> None:
        super().__init__(master, text=title, padding=P(6))
        head = ttk.Frame(self)
        head.pack(fill="x")
        self.var_scale = tk.StringVar(value=SCALE_ACTUAL)
        # pack 顺序有两层讲究：
        #  1) 同是 side="right" 时，先放的贴最右边、后放的往左排 ——
        #     所以必须先放下拉框、再放标签，标签才会出现在下拉框**前面**（左边）。
        #  2) 右侧这组要比左边那段说明文字先放，pack 才会先给下拉框留出位置；
        #     否则说明文字一长就把下拉框挤出面板（窗口拉窄时必现）。
        self.cb_scale = ttk.Combobox(head, textvariable=self.var_scale, state="readonly",
                                     width=15, values=SCALE_CHOICES)
        self.cb_scale.pack(side="right", padx=(P(8), 0))
        ttk.Label(head, text="显示比例：").pack(side="right")
        self.cap = ttk.Label(head, text="", justify="left", foreground="#333")
        self.cap.pack(side="left", anchor="w", fill="x", expand=True)
        if on_scale is not None:
            self.cb_scale.bind("<<ComboboxSelected>>", on_scale)
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, pady=(P(4), 0))
        self.canvas = tk.Canvas(wrap, bg="#f1f1f1", highlightthickness=0)
        self.vs = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vs.set, xscrollcommand=self.hs.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vs.grid(row=0, column=1, sticky="ns")
        self.hs.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.photo: "tk.PhotoImage | None" = None
        self.item: "int | None" = None
        self.canvas.bind("<Configure>", lambda _e: self._place())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(2, "units"))

    # ---------- 内部 ----------
    def _wheel(self, evt) -> str:
        step = -1 if getattr(evt, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")
        return "break"

    def _place(self) -> None:
        if self.photo is None or self.item is None:
            return
        try:
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        except Exception:
            return
        iw, ih = self.photo.width(), self.photo.height()
        self.canvas.coords(self.item, max(0, (cw - iw) // 2), max(0, (ch - ih) // 2))
        self.canvas.configure(scrollregion=(0, 0, max(cw, iw), max(ch, ih)))

    # ---------- 对外 ----------
    def actual(self) -> bool:
        """这一侧是不是 100%（实际大小）"""
        return self.var_scale.get() == SCALE_ACTUAL

    def box(self) -> tuple[int, int]:
        try:
            return max(60, self.canvas.winfo_width() - 4), max(60, self.canvas.winfo_height() - 4)
        except Exception:
            return 400, 400

    def show(self, photo: tk.PhotoImage) -> None:
        self.photo = photo
        self.canvas.delete("all")
        self.item = self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._place()

    def clear(self) -> None:
        self.photo = None
        self.item = None
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 0, 0))


class PreviewWindow(tk.Toplevel):
    """左右并排对比某一页转换前后的样子（默认两侧都按实际大小显示）"""

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
        ttk.Button(head, text="关闭", command=self._close).pack(side="right")

        body = ttk.Frame(self, padding=(P(10), P(4), P(10), P(4)))
        body.pack(fill="both", expand=True)
        self.pane0 = _PreviewPane(body, " 原始 ", P, on_scale=self._render)
        self.pane0.grid(row=0, column=0, sticky="nsew")
        self.pane1 = _PreviewPane(body, " 转换后 ", P, on_scale=self._render)
        self.pane1.grid(row=0, column=1, sticky="nsew", padx=(P(8), 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        foot = ttk.Frame(self, padding=(P(10), P(2), P(10), P(8)))
        foot.pack(fill="x")
        ttk.Label(foot, textvariable=self.var_note, foreground="#666").pack(side="left")

        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.update_idletasks()
        w = min(int(sw * 0.95), P(1180))
        h = min(int(sh * 0.92), P(860))
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 4)}")
        self.minsize(P(760), P(520))      # 再窄两侧面板顶部就会被挤
        self.after(60, self._render)      # 等窗口真正布局完再按实际大小算 dpi

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

    def _dpi_for(self, w_mm: float, h_mm: float, pane: "_PreviewPane") -> float:
        """100% 用屏幕真实 DPI（屏幕上一个像素 = 一个像素）；适应窗口则按该面板尺寸算"""
        if pane.actual():
            return max(48.0, float(self.app.sys_dpi))
        bw, bh = pane.box()
        w_in, h_in = max(w_mm, 1.0) / 25.4, max(h_mm, 1.0) / 25.4
        return max(24.0, min(bw / w_in, bh / h_in) * 0.98)

    @staticmethod
    def _px_text(pv: core.Preview) -> str:
        w = int(round(pv.width_mm / 25.4 * pv.dpi)) if pv.dpi else 0
        h = int(round(pv.height_mm / 25.4 * pv.dpi)) if pv.dpi else 0
        if not pv.dpi:
            return ""
        return f"{pv.dpi:.0f}dpi，{w}×{h} 像素"

    def _render(self, _evt=None) -> None:
        if not self.fi.pages:
            return
        self.idx = max(0, min(self.idx, len(self.fi.pages) - 1))
        self.var_page.set(str(self.idx + 1))
        p = self.fi.pages[self.idx]
        tgt = core.resolve_target(p, self.fi.extra, self.fi.rule)
        need = bool(tgt and core.page_needs_convert(p, self.fi.extra, self.fi.rule))
        dpi0 = self._dpi_for(p.width_mm, p.height_mm, self.pane0)
        try:
            pv0 = core.render_page(self.fi.path, self.idx, None, dpi0,
                                   max_pixels=PREVIEW_MAX_PIXELS)
        except Exception as exc:
            self.pane0.cap.configure(text=f"渲染失败：{exc}")
            self.pane0.clear()
            self.pane1.cap.configure(text="")
            self.pane1.clear()
            self.var_note.set("")
            return
        self._imgs = [self._photo(pv0)]
        self.pane0.show(self._imgs[0])
        self.pane0.cap.configure(
            text=f"第 {p.index} 页   {p.size_text}   旋转 {p.rotation}°   判定 {p.kind}\n"
                 f"{self._px_text(pv0)}")

        if need:
            dpi1 = self._dpi_for(tgt[0], tgt[1], self.pane1)
            try:
                pv1 = core.render_page(self.fi.path, self.idx, tgt, dpi1,
                                       max_pixels=PREVIEW_MAX_PIXELS)
            except Exception as exc:
                self.pane1.cap.configure(text=f"渲染失败：{exc}")
                self.pane1.clear()
                self.var_note.set("")
                return
            self._imgs.append(self._photo(pv1))
            self.pane1.show(self._imgs[1])
            self.pane1.cap.configure(
                text=f"第 {p.index} 页   {pv1.width_mm:.1f}×{pv1.height_mm:.1f}mm   "
                     f"缩放 {pv1.scale * 100:.1f}%\n{self._px_text(pv1)}")
            self.var_note.set(
                f"留白：左右 {pv1.margin_x_mm:.1f}mm，上下 {pv1.margin_y_mm:.1f}mm"
                f"（等比缩放，不会拉伸变形）　·　{self._scale_note()}")
        else:
            self.pane1.clear()
            self.pane1.cap.configure(text="本页不转换：" + self._why_not(p, tgt))
            self.var_note.set("")

    def _scale_note(self) -> str:
        """说明两侧当前的比例关系，提醒能不能直接比大小"""
        a0, a1 = self.pane0.actual(), self.pane1.actual()
        if a0 and a1:
            return "两侧同为实际大小，可直接比大小"
        if a0:
            return "左侧实际大小、右侧适应窗口：两侧比例不同，不能直接比大小"
        if a1:
            return "左侧适应窗口、右侧实际大小：两侧比例不同，不能直接比大小"
        return "两侧各自缩放到窗口，比例不同，只看内容完整性"

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

        self.paths: list[str] = []              # 用户添加的文件/文件夹（输入源）
        self.files: list[str] = []              # 已展开的 PDF 列表：真正被扫描/转换的对象
        self.excluded: set[str] = set()         # 用户手动移除的（规范化绝对路径），重新展开时跳过
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
        self.var_extra = tk.BooleanVar(value=False)          # 由「转换规则」预设同步
        self.var_preset = tk.StringVar(value=PRESETS[0][0])
        self.var_filter = tk.StringVar(value=FILTER_ALL)
        self.var_autoscan = tk.BooleanVar(value=True)
        self.var_zoom = tk.StringVar(value=self._zoom_label())
        self.var_status = tk.StringVar(value="就绪：点【＋ 添加文件夹】后会自动扫描尺寸")
        self.var_stats = tk.StringVar(value="还没有加载文件")
        self.global_rule: core.Rule = core.Rule()            # 预设带来的全局规则（auto=按判定）
        self.converted: dict[str, dict] = {}                 # 已转换记录：路径 -> {time,pages,out}
        self._combo: "ttk.Combobox | None" = None            # 「目标尺寸」列内下拉
        self._combo_after: "str | None" = None
        self._sort_col: str = ""                             # 表头排序：列 id
        self._sort_desc: bool = False
        self._banner_after: "str | None" = None
        self._conv_summary: str = ""          # 转换后重扫时优先显示的汇总

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
        self.btn_remove = ttk.Button(bar, text="移除选中", command=self.remove_selected)
        self.btn_remove.pack(side="left", padx=(P(6), 0))
        self.btn_clear = ttk.Button(bar, text="清空列表", command=self.clear_all)
        self.btn_clear.pack(side="left", padx=(P(6), 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=P(10))
        self.btn_open = ttk.Button(bar, text="打开所在文件夹", command=self.open_folder)
        self.btn_open.pack(side="left")
        ttk.Button(bar, text="生成示例文件…", command=self.make_samples).pack(side="left", padx=(P(6), 0))

        # ---- 选项区
        opt = ttk.LabelFrame(self, text=" 选项 ", padding=(P(10), P(6)))
        opt.pack(fill="x", padx=P(10), pady=(P(2), P(6)))

        row1 = ttk.Frame(opt)
        row1.pack(fill="x")
        ttk.Label(row1, text="转换规则：").pack(side="left")
        self.cb_preset = ttk.Combobox(row1, textvariable=self.var_preset, state="readonly",
                                      width=36, values=[t for t, _, _ in PRESETS])
        self.cb_preset.pack(side="left")
        self.cb_preset.bind("<<ComboboxSelected>>", self._on_preset)
        ttk.Label(row1, text="接近判定容差：").pack(side="left", padx=(P(16), 0))
        ttk.Spinbox(row1, from_=0.1, to=20.0, increment=0.5, width=6,
                    textvariable=self.var_tol).pack(side="left")
        ttk.Label(row1, text="%（宽、高都要在标准尺寸 ±该比例内）",
                  foreground="#777").pack(side="left")
        ttk.Checkbutton(row1, text="添加后自动扫描", variable=self.var_autoscan
                        ).pack(side="left", padx=(P(16), 0))

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
        self.btn_csv = ttk.Button(act, text="导出报告 CSV", command=self.export_csv)
        self.btn_csv.pack(side="left", padx=(P(8), 0))
        self.btn_preview = ttk.Button(act, text="预览转换前后…", command=self.preview_compare)
        self.btn_preview.pack(side="left", padx=(P(8), 0))
        self.btn_extract = ttk.Button(act, text="按尺寸分类提取…", command=self.extract_by_size)
        self.btn_extract.pack(side="left", padx=(P(8), 0))

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

        # 醒目提示条（添加/扫描时显示，几秒后自动收起）
        self.banner_holder = ttk.Frame(top)
        self.banner_holder.pack(fill="x")
        self.lbl_banner = tk.Label(self.banner_holder, text="", anchor="w", justify="left",
                                   padx=P(10), pady=P(5), bd=0,
                                   font=(self.family, BASE_FONT_PT, "bold"))

        # 统计条 + 筛选
        srow = ttk.Frame(top)
        srow.pack(fill="x", pady=(0, P(4)))
        ttk.Label(srow, textvariable=self.var_stats, style="Stats.TLabel").pack(side="left")
        self.cb_filter = ttk.Combobox(srow, textvariable=self.var_filter, state="readonly",
                                      width=16, values=FILTERS)
        self.cb_filter.pack(side="right")
        self.cb_filter.bind("<<ComboboxSelected>>", lambda e: self._render_files())
        ttk.Label(srow, text="筛选：").pack(side="right", padx=(P(8), P(4)))
        ttk.Button(srow, text="全选需转换", command=self.select_need).pack(side="right", padx=(0, P(8)))
        ttk.Label(srow, text="点表头排序 ｜ 点「目标尺寸」格子直接下拉 ｜ 右键更多操作",
                  foreground="#777").pack(side="right", padx=(0, P(16)))

        holder = ttk.Frame(top)
        holder.pack(fill="both", expand=True)

        cols = ("no", "name", "pages", "sizes", "kinds", "target", "conv", "mark", "status")
        self.tree = ttk.Treeview(holder, columns=cols, show="headings", selectmode="extended")
        heads = [("no", "序号", 52, "center"), ("name", "文件名", 300, "w"),
                 ("pages", "页数", 56, "center"), ("sizes", "页面尺寸(mm) 与页数", 250, "w"),
                 ("kinds", "判定", 170, "w"), ("target", "目标尺寸 ⌄", 110, "w"),
                 ("conv", "可转换", 68, "center"), ("mark", "转换", 84, "center"),
                 ("status", "状态", 150, "w")]
        self._head_text = {cid: text for cid, text, _w, _a in heads}
        for cid, text, w, anchor in heads:
            self.tree.heading(cid, text=text, command=lambda c=cid: self._sort_by(c))
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
        self.tree.tag_configure("done", background="#e6f6ea")
        self.bind("<Button-1>", self._click_elsewhere, add="+")
        self.tree.bind("<<TreeviewSelect>>", self._on_select_file)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-1>", self._on_click, add="+")
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
        self._update_headings()          # 缩放重建后恢复排序箭头
        self._update_actions()           # 缩放重建后恢复按钮可用状态
        self._sash_after = self.after(80, self._restore_sash)

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
        if not self.winfo_exists():
            return
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
        for aid in (getattr(self, "_sash_after", None), getattr(self, "_banner_after", None)):
            if not aid:
                continue
            try:
                self.after_cancel(aid)
            except Exception:
                pass
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
        for aid in (getattr(self, "_after_id", None),
                    getattr(self, "_sash_after", None),
                    getattr(self, "_banner_after", None)):
            if not aid:
                continue
            try:
                self.after_cancel(aid)
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

    # ---------- 醒目提示条 ----------
    def _banner(self, text: str, kind: str = "info", hide_after: "int | None" = None) -> None:
        """在文件表上方显示一条醒目的提示；hide_after 毫秒后自动收起"""
        if self._banner_after is not None:
            try:
                self.after_cancel(self._banner_after)
            except Exception:
                pass
            self._banner_after = None
        if not text:
            self._hide_banner()
            return
        bg, fg = {"info": ("#fff6d8", "#7a5a00"),
                  "busy": ("#e6f0fb", "#0b4a8f"),
                  "ok": ("#e6f6ea", "#0a5d2a"),
                  "warn": ("#fdecea", "#b00020")}.get(kind, ("#fff6d8", "#7a5a00"))
        self.lbl_banner.configure(text=text, background=bg, foreground=fg)
        if not self.lbl_banner.winfo_ismapped():
            self.lbl_banner.pack(fill="x", pady=(0, self.px(4)))
        if hide_after:
            self._banner_after = self.after(hide_after, self._hide_banner)

    def _hide_banner(self) -> None:
        self._banner_after = None
        if not self.winfo_exists():
            return
        try:
            self.lbl_banner.pack_forget()
        except Exception:
            pass

    # ---------- 转换规则预设 ----------
    def _on_preset(self, _evt=None) -> None:
        """切换全局预设：附加规则 + 全局强制尺寸，逐文件单独设定的仍然优先"""
        name = self.var_preset.get()
        extra, forced = False, None
        for text, ex, fr in PRESETS:
            if text == name:
                extra, forced = ex, fr
                break
        self.var_extra.set(extra)
        self.global_rule = core.make_rule(*forced) if forced else core.Rule()
        for fi in self.infos:
            key = os.path.normcase(os.path.abspath(fi.path))
            fi.extra = extra
            fi.rule = self.rules.get(key) or self.global_rule
            fi.status = fi.status_text
        self.log(f"转换规则切换为「{name}」" + ("（全部页面强制转换）" if forced else ""))
        self._render_files()
        cur = self._selected_infos()
        if cur:
            self._render_pages(cur[0])

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.configure(cursor="watch" if busy else "")
        self._update_actions()

    def _update_actions(self) -> None:
        """按「是否已加载文件 / 是否已扫描」启用或禁用操作按钮

        目的是让用户点不到必然只会弹提示的按钮，而不是先让他点、再弹「请先添加」。
        """
        if not hasattr(self, "btn_scan"):
            return
        loaded = bool(self.files)              # 已加载文件（添加后即成立）
        scanned = bool(self.infos)             # 已扫描出结果
        busy = bool(self.busy)
        state = lambda ok: ("normal" if (ok and not busy) else "disabled")  # noqa: E731
        for btn, ok in ((self.btn_scan, loaded),
                        (self.btn_convert, scanned),
                        (self.btn_csv, scanned),
                        (self.btn_preview, scanned),
                        (self.btn_extract, scanned),
                        (self.btn_remove, loaded),
                        (self.btn_clear, loaded or scanned),
                        (self.btn_open, scanned)):
            if btn is not None:
                btn.configure(state=state(ok))

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

    @staticmethod
    def _key(p: str) -> str:
        """路径比对用的规范键"""
        return os.path.normcase(os.path.abspath(p))

    def _recollect(self) -> list[str]:
        """按当前【输入源】重新展开 PDF 列表，跳过用户手动移除过的文件

        注意：这里是「文件夹里现在有哪些 PDF」，所以重新点扫描能把新放进文件夹的
        文件捡回来；而被用户【移除选中】掉的文件靠 self.excluded 挡住，不会复活。
        """
        return [f for f in core.collect_pdfs(self.paths) if self._key(f) not in self.excluded]

    def _loaded_files(self) -> list[str]:
        """当前已加载、可处理的文件列表（扫描/转换的唯一依据）"""
        files = self._recollect()
        if not files:                      # 输入源没了（被移走/删掉）也要保住已加载的列表
            files = [f for f in self.files if os.path.isfile(f)]
        return files

    def has_files(self) -> bool:
        return bool(self.files)

    def _no_files_msg(self) -> None:
        """只有真的一份文件都没有时才提示"""
        if self.paths:
            messagebox.showinfo("没有可处理的文件",
                                "已添加的位置里现在找不到 PDF 文件。\n"
                                "请重新【＋ 添加文件夹】，或把 PDF 放回原目录后再扫描。")
        else:
            messagebox.showinfo("还没有添加文件",
                                "请先点左上角【＋ 添加 PDF 文件】或【＋ 添加文件夹】。")

    def _add_paths(self, paths: list[str]) -> None:
        for p in paths:
            if p not in self.paths:
                self.paths.append(p)
        self.files = self._recollect()
        self.log(f"已添加 {len(paths)} 项，当前共 {len(self.files)} 个 PDF 文件"
                 + ("" if self.files else "（没找到 PDF）"))
        self.infos = []
        self._current_path = ""
        self.tree.delete(*self.tree.get_children())
        self.ptree.delete(*self.ptree.get_children())
        self._update_stats()

        if not self.files:
            self.var_status.set("没有找到 PDF 文件")
            self._banner("这个位置没有找到 PDF 文件，换一个文件夹试试", "warn", hide_after=12000)
            return
        if self.var_autoscan.get():
            self._banner(f"已加入 {len(self.files)} 个 PDF 文件，正在自动扫描尺寸…", "busy")
            self.do_scan()
        else:
            self.var_status.set(f"已加载 {len(self.files)} 个文件，点【① 扫描尺寸】开始")
            self._banner(f"已加入 {len(self.files)} 个 PDF 文件 —— 现在点【① 扫描尺寸】开始",
                         "info", hide_after=15000)

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
            self.excluded.clear()
            self.files = self._recollect()
            self.do_scan()

    def remove_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        rm = {self.row_of_iid[i].path for i in sel if i in self.row_of_iid}
        if not rm:
            return
        rm_abs = {self._key(x) for x in rm}
        # 只把「文件」从已加载列表里剔掉，输入源（文件夹）保持不动 ——
        # 以前把包含被删文件的文件夹整个从 self.paths 里丢掉，列表还在、输入源空了，
        # 之后再点扫描/转换后就重扫就会误报「请先添加 PDF 文件」。
        self.excluded |= rm_abs
        self.files = [f for f in self.files if self._key(f) not in rm_abs]
        self.infos = [fi for fi in self.infos if self._key(fi.path) not in rm_abs]
        for k in rm_abs:
            self.rules.pop(k, None)
            self.converted.pop(k, None)
        self._current_path = ""
        self._render_files()
        self.var_status.set(f"已移除 {len(rm_abs)} 个文件，剩余 {len(self.files)} 个"
                            "（点【① 扫描尺寸】可重新统计）")

    def clear_all(self) -> None:
        self.paths.clear()
        self.files.clear()
        self.excluded.clear()
        self.infos.clear()
        self.rules.clear()
        self.converted.clear()
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

    def clear_marks(self, infos: "list[core.FileInfo] | None" = None) -> None:
        infos = infos if infos is not None else self._selected_infos()
        n = 0
        for fi in infos:
            key = os.path.normcase(os.path.abspath(fi.path))
            if self.converted.pop(key, None) is not None:
                n += 1
        self.log(f"已清除 {n} 个文件的「已转换」标记")
        self._render_files()

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
        m.add_command(label=f"按尺寸分类提取选中的 {n} 个…", command=lambda: self.extract_by_size(sel))
        m.add_command(label="复制文件路径", command=self.copy_path)
        if any(self._is_converted(fi) for fi in sel):
            m.add_command(label="清除「已转换」标记", command=lambda: self.clear_marks(sel))
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

    # ---------- 「目标尺寸」列内下拉 ----------
    def _on_click(self, evt) -> "str | None":
        """点「目标尺寸」那一格 → 就地弹下拉；其余位置保持默认行为"""
        if self.busy or not self.row_of_iid:
            return None
        if self.tree.identify_region(evt.x, evt.y) != "cell":
            return None
        row = self.tree.identify_row(evt.y)
        if not row:
            return None
        # 直接用「目标尺寸」格子的 bbox 命中判断：
        # identify_column() 在高 DPI 下与 bbox 的左边界会差几个像素，导致点不准
        bb = self.tree.bbox(row, "target")
        if not bb or not (bb[0] <= evt.x < bb[0] + bb[2]):
            return None
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus(row)
        self._open_target_combo(row)
        return "break"

    def _open_target_combo(self, row: str) -> None:
        fi = self.row_of_iid.get(row)
        bbox = self.tree.bbox(row, "target")
        if not fi or not bbox:
            return
        self._close_target_combo()
        x, y, w, h = bbox
        cb = ttk.Combobox(self.tree, state="readonly", values=TARGET_LABELS, width=1)
        cb.place(x=x, y=y, width=max(w, self.px(150)), height=max(h, self.px(24)))
        cur = fi.rule.label if fi.rule.mode != core.RULE_AUTO else AUTO_TEXT
        cb.set(cur if cur in TARGET_LABELS else AUTO_TEXT)
        cb.bind("<<ComboboxSelected>>", lambda e: self._combo_apply(cb))
        cb.bind("<Escape>", lambda e: self._close_target_combo())
        cb.bind("<Destroy>", lambda e, c=cb: self._combo_gone(c))
        self._combo = cb
        cb.focus_set()
        # 展开必须延到本次点击处理完之后：
        #  1) 之前用 <FocusOut> 关下拉，展开瞬间焦点转到弹出列表 -> 150ms 后自己关掉，
        #     表现就是「点第一下只闪一下，得点第二下」；
        #  2) 直接在 Button-1 里 Post，按键事件还在队列里，弹出列表可能立刻被收起。
        self._combo_after = self.after_idle(lambda: self._post_combo(cb))

    def _post_combo(self, cb) -> None:
        self._combo_after = None
        if cb is not self._combo or not cb.winfo_exists():
            return
        try:
            self.tk.call("ttk::combobox::Post", str(cb))
        except Exception:
            pass

    def _combo_gone(self, cb) -> None:
        if self._combo is cb:
            self._combo = None

    def _click_elsewhere(self, evt) -> None:
        """点窗口里别的地方就收起下拉（替代不可靠的 FocusOut 自动关闭）"""
        cb = self._combo
        if cb is None:
            return
        w = evt.widget
        try:
            if w is cb or str(w).startswith(str(cb)):
                return
        except Exception:
            pass
        self._close_target_combo()

    def _combo_apply(self, cb: ttk.Combobox) -> None:
        label = cb.get()
        rule = _label_to_rule(label)
        infos = self._selected_infos()
        self._close_target_combo()
        if not infos:
            return
        self.apply_rule(infos, rule)
        self.var_status.set(f"已把 {len(infos)} 个文件的【目标尺寸】设为「{label}」")

    def _close_target_combo(self) -> None:
        if self._combo_after is not None:
            try:
                self.after_cancel(self._combo_after)
            except Exception:
                pass
            self._combo_after = None
        cb, self._combo = self._combo, None
        if cb is not None:
            try:
                cb.destroy()
            except Exception:
                pass

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
            key = os.path.normcase(os.path.abspath(fi.path))
            if rule.mode == core.RULE_AUTO:          # 「自动」= 回到当前预设
                self.rules.pop(key, None)
                fi.rule = self.global_rule
            else:
                self.rules[key] = rule
                fi.rule = rule
            fi.status = fi.status_text
        label = rule.label if rule.mode != core.RULE_AUTO else (
            self.global_rule.label if self.global_rule.mode != core.RULE_AUTO else AUTO_TEXT)
        self.log(f"已把 {len(infos)} 个文件的目标设为「{label}」")
        self._render_files()
        cur = self._selected_infos()
        if cur:
            self._render_pages(cur[0])

    def _refresh_rules_display(self) -> None:
        """规则/容差变化后重算显示（保留给脚本与自测调用）"""
        self._on_preset()

    # ---------- 扫描 ----------
    def do_scan(self) -> None:
        if self.busy:
            return
        files = self._loaded_files()
        if not files:
            self._no_files_msg()
            return
        self.files = files
        tol, extra = self.tol(), self.var_extra.get()
        self._set_busy(True)
        self.log(f"\n=== 开始扫描 {len(files)} 个文件（容差 ±{tol * 100:g}%"
                 f"{'，含附加规则' if extra else ''}）===")
        self.pb.configure(maximum=len(files), value=0)
        self._banner(f"正在扫描 {len(files)} 个文件…", "busy")

        def work() -> None:
            try:
                def prog(msg: str, cur: int, total: int) -> None:
                    self.q.put(("prog", msg, cur, total))

                infos = core.scan_paths(files, tol=tol, progress=prog, extra=extra)
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
        self._banner(f"正在转换 {len(todo)} 个文件（{pages} 页）…", "busy")

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

    # ---------- 按尺寸分类提取 ----------
    def extract_by_size(self, infos: "list[core.FileInfo] | None" = None) -> None:
        if self.busy:
            return
        if not self.infos:
            messagebox.showinfo("提示", "请先【① 扫描尺寸】")
            return
        pool = infos if infos is not None else self._selected_infos()
        if not pool:
            pool = [fi for fi in self.infos if fi.ok]
        pool = [fi for fi in pool if fi.ok]
        if not pool:
            messagebox.showinfo("提示", "没有可提取的文件")
            return
        dlg = ExtractDialog(self, pool, default_out=self.var_outdir.get().strip())
        opt = dlg.show()
        if not opt:
            return
        out, group, split = opt["out"], opt["group"], opt["split"]
        try:
            os.makedirs(out, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("输出目录不可用", str(exc))
            return
        tol = self.tol()
        self._set_busy(True)
        self.log(f"\n=== 按尺寸分类提取：{len(pool)} 个文件 → {out}"
                 f"（{'实际尺寸' if group == core.EXTRACT_GROUP_EXACT else '判定类别'}，"
                 f"{ {core.EXTRACT_SPLIT_FILE: '每类合并', core.EXTRACT_SPLIT_PAGE: '每页一个', core.EXTRACT_SPLIT_COPY: '整份复制'}[split] }）===")
        self.pb.configure(maximum=len(pool), value=0)
        self._banner(f"正在按尺寸提取 {len(pool)} 个文件…", "busy")

        def work() -> None:
            try:
                results = []
                for i, fi in enumerate(pool, 1):
                    results.append(core.extract_file(fi.path, out, group=group,
                                                     split=split, tol=tol))
                    self.q.put(("prog", f"已提取 {i}/{len(pool)}", i, len(pool)))
                self.q.put(("extract_done", results))
            except Exception:
                self.q.put(("error", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _on_extract_done(self, results: list) -> None:
        ok = [r for r in results if r.ok]
        bad = [r for r in results if not r.ok]
        made = sum(len(r.outputs) for r in ok)
        self._set_busy(False)
        self.pb["value"] = self.pb["maximum"]
        for r in ok:
            self.log(f"✅ {os.path.basename(r.path)}  {r.pages} 页 → "
                     + "、".join(f"{k}({v}页)" for k, v in sorted(r.groups.items())))
        for r in bad:
            self.log(f"❌ {os.path.basename(r.path)}  {r.error}")
        self._banner(f"✔ 提取完成：{len(ok)} 个文件 → {made} 个 PDF"
                     + (f"；失败 {len(bad)} 个（见日志）" if bad else "")
                     + "。原文件未改动", "ok", hide_after=12000)
        roots = []
        for r in ok:
            for o in r.outputs:
                d = os.path.dirname(os.path.abspath(o))
                if d not in roots:
                    roots.append(d)
        base = os.path.commonpath(roots) if roots else ""
        if messagebox.askyesno("提取完成",
                               f"生成 {made} 个 PDF（来自 {len(ok)} 个文件）\n"
                               f"失败 {len(bad)} 个\n\n输出目录：\n{base or '（无）'}\n\n"
                               f"现在打开该目录？"):
            if base:
                open_in_system(base)

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
    def _is_converted(self, fi: core.FileInfo) -> bool:
        return os.path.normcase(os.path.abspath(fi.path)) in self.converted

    def _sort_key(self, fi: core.FileInfo, idx: int):
        col = self._sort_col
        if col == "no":
            return idx
        if col == "name":
            return fi.name.lower()
        if col == "pages":
            return fi.page_count
        if col == "sizes":
            return max((p.width_mm * p.height_mm for p in fi.pages), default=0.0)
        if col == "kinds":
            return fi.kinds_text
        if col == "target":
            return fi.rule.label
        if col == "conv":
            return len(fi.convertible_pages) if fi.ok else -1
        if col == "mark":
            rec = self.converted.get(os.path.normcase(os.path.abspath(fi.path)))
            return rec.get("pages", 0) if rec else 0
        if col == "status":
            return fi.status_text
        return idx

    def _sort_by(self, col: str) -> None:
        """点表头排序：同一列再点一次反向"""
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, False
        self._update_headings()
        self._render_files()

    def _update_headings(self) -> None:
        for cid, text in self._head_text.items():
            mark = ""
            if cid == self._sort_col:
                mark = " ▼" if self._sort_desc else " ▲"
            self.tree.heading(cid, text=text + mark)

    def _filtered_infos(self) -> list[core.FileInfo]:
        mode = self.var_filter.get()
        if mode == FILTER_ALL:
            return list(self.infos)
        if mode == FILTER_NEED:
            return [fi for fi in self.infos if fi.ok and fi.convertible_pages]
        if mode == FILTER_OTHER:
            return [fi for fi in self.infos
                    if fi.ok and any(p.kind == core.K_OTHER for p in fi.pages)]
        if mode == FILTER_DONE:
            return [fi for fi in self.infos if self._is_converted(fi)]
        if mode == FILTER_SKIP:
            return [fi for fi in self.infos if fi.ok and fi.rule.mode == core.RULE_SKIP]
        if mode == FILTER_ERR:
            return [fi for fi in self.infos if not fi.ok]
        return [fi for fi in self.infos
                if fi.ok and not fi.convertible_pages and fi.rule.mode != core.RULE_SKIP]

    def _visible_infos(self) -> list[core.FileInfo]:
        """当前筛选 + 当前排序后的行顺序"""
        rows = self._filtered_infos()
        if not self._sort_col:
            return rows
        pos = {id(fi): i for i, fi in enumerate(self.infos)}
        return sorted(rows, key=lambda fi: self._sort_key(fi, pos.get(id(fi), 0)),
                      reverse=self._sort_desc)

    def _render_files(self) -> None:
        self._close_target_combo()
        self.tree.delete(*self.tree.get_children())
        self.row_of_iid.clear()
        pos = {id(fi): i for i, fi in enumerate(self.infos)}
        for i, fi in enumerate(self._visible_infos(), 1):
            no = pos.get(id(fi), i) + 1
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
            rec = self.converted.get(os.path.normcase(os.path.abspath(fi.path)))
            mark = f"✔ {rec['pages']}页" if rec else ""
            tags = (tag, "done") if rec else (tag,)
            iid = self.tree.insert("", "end", values=(
                no, fi.name, fi.page_count, fi.sizes_text, fi.kinds_text,
                fi.rule.label, conv, mark, status), tags=tags)
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
        n_conv = len([fi for fi in self.infos if self._is_converted(fi)])
        if n_conv:
            parts.append(f"已转换 {n_conv} 个")
        txt = " ｜ ".join(parts) if total else "还没有加载文件"
        sel = len(self.tree.selection())
        if sel > 1:
            txt += f"   ← 已选 {sel} 个"
        if self.var_filter.get() != FILTER_ALL and total:
            txt += f"   【筛选：{self.var_filter.get()}，{len(self.tree.get_children())} 行】"
        self.var_stats.set(txt)
        self._update_actions()

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
            core.write_report_csv(self.infos, path, converted=self.converted)
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
                    if self.busy:
                        self._banner(f"{text}（{cur}/{total}）", "busy")
                elif kind == "scan_done":
                    self._on_scan_done(msg[1])
                elif kind == "conv_done":
                    self._on_conv_done(msg[1])
                elif kind == "extract_done":
                    self._on_extract_done(msg[1])
                elif kind == "error":
                    self._set_busy(False)
                    self.log("出错：\n" + msg[1])
                    messagebox.showerror("出错了", msg[1].splitlines()[-1] if msg[1] else "未知错误")
        except queue.Empty:
            pass
        self._after_id = self.after(80, self._drain_queue)

    def _on_scan_done(self, infos: list[core.FileInfo]) -> None:
        # 重扫后把之前设定的逐文件规则接回来；没单独设过的跟随当前预设
        for fi in infos:
            key = os.path.normcase(os.path.abspath(fi.path))
            fi.rule = self.rules.get(key) or self.global_rule
            fi.extra = self.var_extra.get()
            fi.status = fi.status_text
        self.infos = infos
        self._set_busy(False)
        self.pb["value"] = self.pb["maximum"]
        self._render_files()
        ok = [fi for fi in infos if fi.ok]
        need = [fi for fi in ok if fi.convertible_pages]
        pages = sum(len(fi.convertible_pages) for fi in ok)
        bad = len(infos) - len(ok)
        if self._conv_summary:
            self._banner(self._conv_summary, "ok", hide_after=15000)
            self._conv_summary = ""
        elif not infos:
            self._banner("没有扫描到文件", "warn", hide_after=12000)
        elif need:
            self._banner(f"✔ 扫描完成：共 {len(infos)} 个文件，其中 {len(need)} 个需转换"
                         f"（{pages} 页）" + (f"，{bad} 个读取失败" if bad else "")
                         + "。看中哪几个可勾选后右键批量处理",
                         "ok", hide_after=10000)
        else:
            self._banner(f"✔ 扫描完成：共 {len(infos)} 个文件，没有需要转换的页面"
                         + (f"（{bad} 个读取失败）" if bad else ""),
                         "ok", hide_after=8000)
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
        stamp = time.strftime("%m-%d %H:%M")
        for r in ok:
            key = os.path.normcase(os.path.abspath(r.path))
            prev = self.converted.get(key, {})
            moved = bool(r.out_path) and os.path.normcase(os.path.abspath(r.out_path)) != key
            text = f"已转换 {r.converted} 页 {stamp}"
            if moved:
                text += f" → {os.path.basename(r.out_path)}"
            self.converted[key] = {"pages": int(prev.get("pages", 0)) + r.converted,
                                   "time": stamp, "text": text,
                                   "out": r.out_path if moved else ""}
        self._conv_summary = (f"✔ 转换完成：{len(ok)} 个文件、{sum(r.converted for r in ok)} 页已处理"
                             + (f"；跳过 {len(skip)} 个" if skip else "")
                             + (f"；失败 {len(bad)} 个（见日志）" if bad else "")
                             + "。列表里绿色行 + 「转换」列即为已转换的文件")
        messagebox.showinfo(
            "转换完成",
            f"成功 {len(ok)} 个（共 {sum(r.converted for r in ok)} 页）\n"
            f"跳过 {len(skip)} 个\n失败 {len(bad)} 个"
            + ("\n\n失败详情见运行日志。" if bad else ""))
        # 转换后重新扫描，界面显示真实的新尺寸（逐文件规则会自动接回）
        self.do_scan()


# ---------------------------------------------------------------- 入口
def main() -> int:
    # 命令行模式下要 print 中文和「×」：Windows 控制台若不是 UTF-8 会崩，
    # --windowed 打包的 exe 更是连 stdout 都是 None
    core._force_utf8_stdio()

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
    if argv and argv[0] in ("scan", "convert", "extract"):
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
