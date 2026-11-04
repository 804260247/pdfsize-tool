# -*- coding: utf-8 -*-
"""
pdfsize_core.py —— PDF 页面尺寸检测 / A4·A3 标准化 核心逻辑

功能
----
1. 扫描 PDF：列出每一页的实际显示尺寸（mm / pt）、方向、判定类型
2. 判定规则（默认容差 3%）：
     · 竖版 且 宽高都接近 A4(210×297mm)  -> A4竖版
     · 横版 且 宽高都接近 A3(420×297mm)  -> A3横版
3. 转换：把上面两类页面等比缩放并居中，落到标准 A4 / A3 尺寸
   （其余页面原样复制，不做任何改动）

依赖：PyMuPDF (pip install pymupdf)
命令行用法见文件末尾 main()。
"""

from __future__ import annotations

import csv
import os
import sys
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

try:  # PyMuPDF >= 1.24 推荐 import pymupdf；老版本只有 fitz
    import pymupdf  # type: ignore
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore


# ---------------------------------------------------------------- 常量
PT_PER_MM = 72.0 / 25.4          # 1 mm = 2.8346 pt
MM_PER_PT = 25.4 / 72.0          # 1 pt = 0.3528 mm

A4_MM: Tuple[float, float] = (210.0, 297.0)   # 竖版 宽, 高
A3_MM: Tuple[float, float] = (297.0, 420.0)   # 竖版 宽, 高

# 与目标尺寸相差小于该值(mm)时视为“已是标准尺寸”，不再重写该页
ALREADY_STD_MM = 0.5

DEFAULT_TOL = 0.03               # 默认容差 3%

# 判定类型
K_A4P = "A4竖版"
K_A3L = "A3横版"
K_A4L = "A4横版"     # 只报告，不转换
K_A3P = "A3竖版"     # 只报告，不转换
K_OTHER = "其他"

# 需要转换的类型 -> 目标尺寸(mm, 宽×高)
CONVERTIBLE: dict[str, Tuple[float, float]] = {
    K_A4P: (A4_MM[0], A4_MM[1]),          # 210 × 297
    K_A3L: (A3_MM[1], A3_MM[0]),          # 420 × 297
}

# 可选附加规则（默认关闭）：横版近A4 -> A4横版，竖版近A3 -> A3竖版
CONVERTIBLE_EXTRA: dict[str, Tuple[float, float]] = {
    K_A4L: (A4_MM[1], A4_MM[0]),          # 297 × 210
    K_A3P: (A3_MM[0], A3_MM[1]),          # 297 × 420
}


def target_size(kind: str, extra: bool = False) -> Optional[Tuple[float, float]]:
    """该类型应转成的目标尺寸(mm)；None 表示不处理"""
    if kind in CONVERTIBLE:
        return CONVERTIBLE[kind]
    if extra and kind in CONVERTIBLE_EXTRA:
        return CONVERTIBLE_EXTRA[kind]
    return None


# ---------------------------------------------------------------- 工具函数
def pt_to_mm(v: float) -> float:
    return v * MM_PER_PT


def mm_to_pt(v: float) -> float:
    return v * PT_PER_MM


def _near(w: float, h: float, tw: float, th: float, tol: float) -> bool:
    """宽、高是否都在目标尺寸的 tol 相对误差内"""
    if tw <= 0 or th <= 0:
        return False
    return abs(w - tw) / tw <= tol and abs(h - th) / th <= tol


def classify_page(w_mm: float, h_mm: float, tol: float = DEFAULT_TOL) -> str:
    """按宽高(mm)判定页面类型。tol 为相对误差，0.03 = 3%"""
    portrait = h_mm >= w_mm
    if portrait:
        if _near(w_mm, h_mm, A4_MM[0], A4_MM[1], tol):
            return K_A4P
        if _near(w_mm, h_mm, A3_MM[0], A3_MM[1], tol):
            return K_A3P
    else:
        if _near(w_mm, h_mm, A3_MM[1], A3_MM[0], tol):
            return K_A3L
        if _near(w_mm, h_mm, A4_MM[1], A4_MM[0], tol):
            return K_A4L
    return K_OTHER


def needs_convert(kind: str, w_mm: float, h_mm: float, extra: bool = False) -> bool:
    """该页是否真的需要重写（类型可转换，且当前尺寸不是标准尺寸）"""
    target = target_size(kind, extra)
    if not target:
        return False
    return (abs(w_mm - target[0]) > ALREADY_STD_MM
            or abs(h_mm - target[1]) > ALREADY_STD_MM)


# ---------------------------------------------------------------- 逐文件目标规则
# 常用纸张尺寸（竖版：宽, 高，单位 mm）
PAPER_MM: dict[str, Tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A5": (148.0, 210.0),
    "B5": (176.0, 250.0),
    "Letter": (215.9, 279.4),
}

RULE_AUTO = "auto"       # 按上面的默认/附加规则自动判定
RULE_TARGET = "target"   # 强制该文件所有页统一到指定尺寸
RULE_SKIP = "skip"       # 该文件不处理


@dataclass(frozen=True)
class Rule:
    """单个文件的处理规则（默认 auto：按规则自动判定）"""
    mode: str = RULE_AUTO
    w_mm: float = 0.0
    h_mm: float = 0.0
    label: str = "自动"

    @property
    def size(self) -> Optional[Tuple[float, float]]:
        return (self.w_mm, self.h_mm) if self.mode == RULE_TARGET else None


def make_rule(paper: str, landscape: bool = False) -> Rule:
    """按纸张名+方向造一条强制规则，例如 make_rule("A4", True) -> 297×210mm"""
    if paper not in PAPER_MM:
        raise KeyError(f"未知纸张尺寸：{paper}")
    w, h = PAPER_MM[paper]
    if landscape:
        w, h = h, w
    return Rule(RULE_TARGET, w, h, f"{paper}{'横版' if landscape else '竖版'}")


RULE_SKIP_OBJ = Rule(RULE_SKIP, 0.0, 0.0, "跳过")


def resolve_target(p: "PageInfo", extra: bool = False,
                   rule: Optional[Rule] = None) -> Optional[Tuple[float, float]]:
    """该页最终要转成的尺寸(mm)；None = 不动。逐文件规则优先于自动判定"""
    if rule is not None:
        if rule.mode == RULE_SKIP:
            return None
        if rule.mode == RULE_TARGET and rule.w_mm > 0 and rule.h_mm > 0:
            return (rule.w_mm, rule.h_mm)
    return target_size(p.kind, extra)


def page_needs_convert(p: "PageInfo", extra: bool = False,
                       rule: Optional[Rule] = None) -> bool:
    """该页是否真的需要重写（已有目标且当前尺寸不等于目标）"""
    tgt = resolve_target(p, extra, rule)
    if not tgt:
        return False
    return (abs(p.width_mm - tgt[0]) > ALREADY_STD_MM
            or abs(p.height_mm - tgt[1]) > ALREADY_STD_MM)


def fmt_size_mm(w_mm: float, h_mm: float) -> str:
    return f"{w_mm:.1f}×{h_mm:.1f}mm"


# ---------------------------------------------------------------- 数据结构
@dataclass
class PageInfo:
    index: int            # 1 起
    width_mm: float
    height_mm: float
    rotation: int
    kind: str

    @property
    def size_text(self) -> str:
        return fmt_size_mm(self.width_mm, self.height_mm)

    @property
    def size_pt_text(self) -> str:
        return f"{mm_to_pt(self.width_mm):.1f}×{mm_to_pt(self.height_mm):.1f}pt"


@dataclass
class FileInfo:
    path: str
    ok: bool = True
    error: str = ""
    pages: List[PageInfo] = field(default_factory=list)
    size_bytes: int = 0
    status: str = ""      # 界面/报告里显示的处理状态
    extra: bool = False   # 是否启用附加规则
    rule: Rule = field(default_factory=Rule)   # 逐文件规则（默认自动）

    # ---------- 派生信息 ----------
    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def size_groups(self, limit: int = 3) -> List[Tuple[str, int]]:
        """按尺寸分组统计，[(尺寸文本, 页数), ...]，按页数降序"""
        groups: dict[str, int] = {}
        for p in self.pages:
            groups[p.size_text] = groups.get(p.size_text, 0) + 1
        items = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
        if len(items) > limit:
            head = items[:limit]
            rest = sum(c for _, c in items[limit:])
            return head + [(f"…另{len(items) - limit}种", rest)]
        return items

    def kind_groups(self, limit: int = 4) -> List[Tuple[str, int]]:
        groups: dict[str, int] = {}
        for p in self.pages:
            groups[p.kind] = groups.get(p.kind, 0) + 1
        items = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
        return items[:limit]

    @property
    def sizes_text(self) -> str:
        return "；".join(f"{t}×{c}" for t, c in self.size_groups())

    @property
    def kinds_text(self) -> str:
        return "、".join(f"{k}×{c}" for k, c in self.kind_groups())

    @property
    def convertible_pages(self) -> List[PageInfo]:
        return [p for p in self.pages
                if page_needs_convert(p, self.extra, self.rule)]

    @property
    def skipped_by_rule(self) -> bool:
        return self.rule.mode == RULE_SKIP

    @property
    def status_text(self) -> str:
        """界面/报告里显示的当前处理状态（会随 rule 变化重新计算）"""
        if not self.ok:
            return f"读取失败：{self.error}"
        if self.rule.mode == RULE_SKIP:
            return "已跳过（不处理）"
        n = len(self.convertible_pages)
        if self.rule.mode == RULE_TARGET:
            return f"→ {self.rule.label}（{n} 页）" if n else f"已是 {self.rule.label}"
        return f"可转换 {n} 页" if n else "无需转换"


@dataclass
class ConvertResult:
    path: str
    ok: bool = True
    error: str = ""
    converted: int = 0        # 实际重写的页数
    total: int = 0
    out_path: str = ""
    backup_path: str = ""
    skipped_reason: str = ""


ProgressCb = Optional[Callable[[str, int, int], None]]   # (消息, 当前, 总数)


# ---------------------------------------------------------------- 文件收集
def collect_pdfs(paths: Iterable[str], recursive: bool = True) -> List[str]:
    """把文件/文件夹混合列表展开为 PDF 文件列表（去重、排序）"""
    out: List[str] = []
    seen = set()

    def add(p: str) -> None:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append(p)

    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in sorted(files):
                        if f.lower().endswith(".pdf"):
                            add(os.path.join(root, f))
            else:
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if os.path.isfile(fp) and f.lower().endswith(".pdf"):
                        add(fp)
        elif os.path.isfile(p) and p.lower().endswith(".pdf"):
            add(p)
    out.sort(key=lambda s: s.lower())
    return out


# ---------------------------------------------------------------- 扫描
def scan_file(path: str, tol: float = DEFAULT_TOL, extra: bool = False,
              rule: Optional[Rule] = None) -> FileInfo:
    info = FileInfo(path=path, extra=extra, rule=rule or Rule())
    try:
        info.size_bytes = os.path.getsize(path)
    except OSError:
        pass
    try:
        doc = pymupdf.open(path)
    except Exception as exc:                      # 打不开 / 损坏
        info.ok = False
        info.error = f"无法打开：{exc}"
        return info

    try:
        if doc.needs_pass:
            info.ok = False
            info.error = "文件已加密，需要密码"
            return info
        for i, page in enumerate(doc):
            r = page.rect                        # 已含 /Rotate 的显示尺寸
            w_mm, h_mm = pt_to_mm(r.width), pt_to_mm(r.height)
            info.pages.append(PageInfo(
                index=i + 1,
                width_mm=w_mm,
                height_mm=h_mm,
                rotation=int(page.rotation or 0),
                kind=classify_page(w_mm, h_mm, tol),
            ))
        if not info.pages:
            info.ok = False
            info.error = "文件没有页面"
    except Exception as exc:
        info.ok = False
        info.error = f"读取失败：{exc}"
    finally:
        doc.close()

    if info.ok:
        info.status = info.status_text
    return info


def scan_paths(paths: Sequence[str], tol: float = DEFAULT_TOL,
               progress: ProgressCb = None, recursive: bool = True,
               extra: bool = False) -> List[FileInfo]:
    files = collect_pdfs(paths, recursive=recursive)
    total = len(files)
    out: List[FileInfo] = []
    for i, fp in enumerate(files, 1):
        if progress:
            progress(f"扫描 {os.path.basename(fp)}", i, total)
        out.append(scan_file(fp, tol, extra=extra))
    return out


# ---------------------------------------------------------------- 转换
def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{base}({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def _verify_output(tmp_path: str, total: int, info: FileInfo,
                   extra: bool = False, rule: Optional[Rule] = None) -> str:
    """校验临时输出：页数一致、转换页已达标、未转换页尺寸未变。返回错误描述，空串=通过"""
    try:
        chk = pymupdf.open(tmp_path)
    except Exception as exc:
        return f"无法重新打开输出文件（{exc}）"
    try:
        if chk.page_count != total:
            return f"页数不一致（原 {total} 页，输出 {chk.page_count} 页）"
        for i, page in enumerate(chk):
            w_mm, h_mm = pt_to_mm(page.rect.width), pt_to_mm(page.rect.height)
            src_p = info.pages[i]
            tgt = resolve_target(src_p, extra, rule)
            if tgt and page_needs_convert(src_p, extra, rule):
                if abs(w_mm - tgt[0]) > 0.5 or abs(h_mm - tgt[1]) > 0.5:
                    return f"第{i + 1}页尺寸未达标（{w_mm:.1f}×{h_mm:.1f}mm）"
            else:
                if (abs(w_mm - src_p.width_mm) > 0.5
                        or abs(h_mm - src_p.height_mm) > 0.5):
                    return f"第{i + 1}页本应保持不变，但尺寸变了"
        return ""
    finally:
        chk.close()


def convert_file(path: str, tol: float = DEFAULT_TOL, backup: bool = True,
                 out_path: Optional[str] = None,
                 progress: ProgressCb = None,
                 extra: bool = False,
                 rule: Optional[Rule] = None) -> ConvertResult:
    """
    把 path 中【竖版近A4】【横版近A3】的页面缩放到标准 A4 / A3。
    rule 不为 None 时按逐文件规则处理（强制指定尺寸 / 跳过）。

    out_path=None  -> 就地覆盖原文件（backup=True 时先备份为 xxx.pdf.bak）
    out_path=指定  -> 写到新文件，原文件不动
    """
    res = ConvertResult(path=path)
    info = scan_file(path, tol, extra=extra, rule=rule)
    if not info.ok:
        res.ok = False
        res.error = info.error
        return res
    res.total = info.page_count

    todo = info.convertible_pages
    if not todo:
        if info.rule.mode == RULE_SKIP:
            res.skipped_reason = "按文件规则跳过"
        elif info.rule.mode == RULE_TARGET:
            res.skipped_reason = f"所有页已是 {info.rule.label}"
        else:
            res.skipped_reason = "没有符合【竖版近A4】或【横版近A3】且尺寸不标准的页面"
        return res

    try:
        src = pymupdf.open(path)
    except Exception as exc:
        res.ok = False
        res.error = f"无法打开：{exc}"
        return res

    out = pymupdf.open()
    tmp_path = ""
    try:
        for page in src:
            p_info = info.pages[page.number]
            tgt = resolve_target(p_info, extra, rule)
            if tgt and page_needs_convert(p_info, extra, rule):
                tw_mm, th_mm = tgt
                W, H = mm_to_pt(tw_mm), mm_to_pt(th_mm)
                new_page = out.new_page(width=W, height=H)
                # keep_proportion=True：等比缩放，剩余边距居中留白，不拉伸变形
                new_page.show_pdf_page(pymupdf.Rect(0, 0, W, H), src, page.number,
                                       keep_proportion=True)
                _copy_links(page, new_page, W, H)
                res.converted += 1
                if progress:
                    progress(f"转换 {os.path.basename(path)} 第{page.number + 1}页 → "
                             f"{tw_mm:g}×{th_mm:g}mm", page.number + 1, res.total)
            else:
                out.insert_pdf(src, from_page=page.number, to_page=page.number)

        # 元数据 / 书签尽量保留
        try:
            out.set_metadata(src.metadata or {})
        except Exception:
            pass
        try:
            toc = src.get_toc(simple=True)
            if toc:
                out.set_toc(toc)
        except Exception:
            pass

        if out_path and (os.path.normcase(os.path.abspath(out_path))
                         == os.path.normcase(os.path.abspath(path))):
            out_path = None       # 目标就是原文件 -> 按覆盖模式处理（会备份）
        target = out_path or path
        if out_path:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

        d = os.path.dirname(os.path.abspath(target)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".pdfsize_", suffix=".pdf", dir=d)
        os.close(fd)
        out.save(tmp_path, garbage=3, deflate=True)
        res.out_path = target

        if out_path is None and backup:
            bak = _unique_path(path + ".bak")
            shutil.copy2(path, bak)
            res.backup_path = bak
    except Exception as exc:
        res.ok = False
        res.error = f"转换失败：{exc}"
    finally:
        try:
            out.close()
        except Exception:
            pass
        try:
            src.close()
        except Exception:
            pass

    if res.ok and tmp_path:
        problem = _verify_output(tmp_path, res.total, info, extra, rule)
        if problem:
            res.ok = False
            res.error = f"输出校验失败：{problem}（原文件未改动）"

    if res.ok and tmp_path:
        try:
            os.replace(tmp_path, res.out_path)
            tmp_path = ""
        except Exception as exc:
            res.ok = False
            res.error = f"写回失败：{exc}"
    if tmp_path and os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return res


def _copy_links(old_page, new_page, W: float, H: float) -> None:
    """把原页面的超链接按同一缩放比例搬到新页面（失败忽略）"""
    try:
        r0 = old_page.rect
        if r0.width <= 0 or r0.height <= 0:
            return
        s = min(W / r0.width, H / r0.height)
        ox = (W - r0.width * s) / 2.0
        oy = (H - r0.height * s) / 2.0
        for link in old_page.get_links():
            r = link.get("from")
            if r is None:
                continue
            nr = pymupdf.Rect(r.x0 * s + ox, r.y0 * s + oy,
                              r.x1 * s + ox, r.y1 * s + oy)
            kind = link.get("kind")
            if kind == pymupdf.LINK_GOTO:
                new_page.insert_link({"kind": pymupdf.LINK_GOTO,
                                      "from": nr,
                                      "page": link.get("page", 0)})
            elif kind == pymupdf.LINK_URI:
                new_page.insert_link({"kind": pymupdf.LINK_URI,
                                      "from": nr,
                                      "uri": link.get("uri", "")})
    except Exception:
        pass


def convert_paths(paths: Sequence[str], tol: float = DEFAULT_TOL,
                  backup: bool = True, out_dir: Optional[str] = None,
                  progress: ProgressCb = None,
                  recursive: bool = True,
                  extra: bool = False) -> List[ConvertResult]:
    """批量转换。out_dir 为 None 时覆盖原文件；否则输出到该目录（同名）"""
    files = collect_pdfs(paths, recursive=recursive)
    if out_dir:  # 输出目录若在输入目录内部，避免把刚生成的成品又扫一遍
        od = os.path.normcase(os.path.abspath(out_dir)) + os.sep
        files = [f for f in files
                 if not os.path.normcase(os.path.abspath(f)).startswith(od)]
    results: List[ConvertResult] = []
    total = len(files)
    for i, fp in enumerate(files, 1):
        if progress:
            progress(f"处理 {os.path.basename(fp)}", i - 1, total)
        op = os.path.join(out_dir, os.path.basename(fp)) if out_dir else None
        results.append(convert_file(fp, tol=tol, backup=backup, out_path=op, extra=extra))
        if progress:
            progress(f"处理 {os.path.basename(fp)}", i, total)
    return results


# ---------------------------------------------------------------- 报告
def write_report_csv(infos: Sequence[FileInfo], csv_path: str) -> str:
    """导出扫描报告（UTF-8 BOM，Excel 直接可读）"""
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["文件", "页数", "页面尺寸分布(mm)", "判定", "目标尺寸", "可转换页数",
                    "状态", "完整路径"])
        for fi in infos:
            w.writerow([fi.name, fi.page_count, fi.sizes_text, fi.kinds_text,
                        fi.rule.label,
                        len(fi.convertible_pages),
                        fi.status_text,
                        fi.path])
    return csv_path


def describe_pages(info: FileInfo, limit: int = 0) -> str:
    """纯文本逐页明细"""
    lines = [f"{info.name}  共 {info.page_count} 页  {info.sizes_text}  [{info.kinds_text}]"]
    pages = info.pages if not limit else info.pages[:limit]
    for p in pages:
        tgt = resolve_target(p, info.extra, info.rule)
        flag = (f"→ {tgt[0]:g}×{tgt[1]:g}mm"
                if tgt and page_needs_convert(p, info.extra, info.rule) else "")
        lines.append(f"  P{p.index:<4} {p.size_text:<18} {p.size_pt_text:<20} "
                     f"旋转{p.rotation:<4} {p.kind} {flag}")
    if limit and len(info.pages) > limit:
        lines.append(f"  ... 省略 {len(info.pages) - limit} 页")
    return "\n".join(lines)


# ---------------------------------------------------------------- 预览渲染
@dataclass
class Preview:
    """一页的预览图与几何信息"""
    data: bytes          # 图像字节（png 或 ppm）
    fmt: str             # "png" / "ppm"
    width_mm: float
    height_mm: float
    scale: float = 1.0       # 相对原始尺寸的缩放比例（1.0 = 未缩放）
    margin_x_mm: float = 0.0  # 等比缩放后左右留白
    margin_y_mm: float = 0.0  # 上下留白


def render_page(path: str, index: int, target_mm: Optional[Tuple[float, float]] = None,
                dpi: float = 96.0, fmt: str = "png",
                max_pixels: float = 4.0e6) -> Preview:
    """
    把第 index 页（0 起）渲染成图片字节。
    target_mm 不为 None 时，按 convert_file 的同一算法（等比缩放+居中）渲染转换后的样子，
    但**不写任何文件**，纯内存预览。
    """
    src = pymupdf.open(path)
    doc = None
    try:
        if target_mm:
            W, H = mm_to_pt(target_mm[0]), mm_to_pt(target_mm[1])
            doc = pymupdf.open()
            page = doc.new_page(width=W, height=H)
            page.show_pdf_page(pymupdf.Rect(0, 0, W, H), src, index, keep_proportion=True)
            r0 = src[index].rect
            s = min(W / r0.width, H / r0.height) if r0.width and r0.height else 1.0
            w_mm, h_mm = target_mm
            mx = max(0.0, (W - r0.width * s) / 2.0) * MM_PER_PT
            my = max(0.0, (H - r0.height * s) / 2.0) * MM_PER_PT
        else:
            page = src[index]
            r0 = page.rect
            s, mx, my = 1.0, 0.0, 0.0
            w_mm, h_mm = pt_to_mm(r0.width), pt_to_mm(r0.height)
        r = page.rect
        px = (r.width / 72.0 * dpi) * (r.height / 72.0 * dpi)
        if px > max_pixels:
            dpi *= (max_pixels / px) ** 0.5
        pix = page.get_pixmap(dpi=max(1, int(round(dpi))), alpha=False)
        return Preview(pix.tobytes(fmt), fmt, w_mm, h_mm, s, mx, my)
    finally:
        if doc is not None:
            doc.close()
        src.close()


# ---------------------------------------------------------------- 示例文件
def make_samples(target_dir: str) -> List[str]:
    """生成一组示例 PDF，便于用户自测：近A4竖版 / 近A3横版 / Letter / 混合尺寸"""
    os.makedirs(target_dir, exist_ok=True)
    specs = [
        ("示例1_近A4竖版_208x295.pdf", [(208, 295, "near A4 portrait 208x295")]),
        ("示例2_近A3横版_415x292.pdf", [(415, 292, "near A3 landscape 415x292")]),
        ("示例3_Letter_不改.pdf", [(215.9, 279.4, "US Letter - should NOT change")]),
        ("示例5_近A3竖版_295x418.pdf", [(295, 418, "near A3 portrait - only via override")]),
        ("示例4_混合尺寸_5页.pdf", [
            (210, 297, "P1 A4 portrait exact"),
            (413, 291, "P2 A3-ish landscape"),
            (215.9, 279.4, "P3 US Letter"),
            (207, 294, "P4 near A4"),
            (148, 210, "P5 A5"),
        ]),
    ]
    made: List[str] = []
    for name, pages in specs:
        fp = os.path.join(target_dir, name)
        doc = pymupdf.open()
        for w_mm, h_mm, text in pages:
            pg = doc.new_page(width=mm_to_pt(w_mm), height=mm_to_pt(h_mm))
            pg.insert_text((mm_to_pt(15), mm_to_pt(25)), text, fontsize=14)
            pg.draw_rect(pymupdf.Rect(10, 10, pg.rect.width - 10, pg.rect.height - 10),
                         color=(0.2, 0.3, 0.8), width=2)
        doc.set_metadata({"title": name})
        doc.save(fp)
        doc.close()
        made.append(fp)
    return made


# ---------------------------------------------------------------- 自检
def self_check() -> Tuple[bool, str]:
    """内置自检：造一个 209×296mm 的 PDF → 扫描 → 转换 → 复检尺寸"""
    tmp = tempfile.mkdtemp(prefix="pdfsize_chk_")
    try:
        fp = os.path.join(tmp, "check.pdf")
        doc = pymupdf.open()
        pg = doc.new_page(width=mm_to_pt(209), height=mm_to_pt(296))
        pg.insert_text((40, 60), "self check", fontsize=14)
        doc.save(fp)
        doc.close()

        fi = scan_file(fp)
        if not fi.ok or len(fi.convertible_pages) != 1:
            return False, f"扫描失败：{fi.error or '未识别为近A4'}"
        r = convert_file(fp, backup=False)
        if not r.ok or r.converted != 1:
            return False, f"转换失败：{r.error or '未转换'}"
        fi2 = scan_file(fp)
        p2 = fi2.pages[0]
        if abs(p2.width_mm - 210) > 0.5 or abs(p2.height_mm - 297) > 0.5:
            return False, f"结果尺寸不对：{p2.size_text}"
        return True, p2.size_text
    except Exception as exc:
        return False, f"异常：{exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- CLI
def _cli(argv: List[str]) -> int:
    import argparse

    if argv and argv[0] == "--check":
        ok, msg = self_check()
        print(("OK " if ok else "FAIL ") + msg)
        return 0 if ok else 1

    ap = argparse.ArgumentParser(
        prog="pdfsize",
        description="批量检测 PDF 页面尺寸，并把【竖版近A4】【横版近A3】的页面标准化为 A4 / A3")
    ap.add_argument("action", choices=["scan", "convert"], help="scan=只看尺寸，convert=转换")
    ap.add_argument("paths", nargs="+", help="PDF 文件或文件夹")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL * 100,
                    help="容差百分比，默认 3（即 ±3%%）")
    ap.add_argument("--csv", help="把扫描结果导出为 CSV")
    ap.add_argument("--outdir", help="转换结果输出目录（不给则覆盖原文件）")
    ap.add_argument("--no-backup", action="store_true", help="覆盖时不做 .bak 备份")
    ap.add_argument("--extra", action="store_true",
                    help="附加规则：横版近A4→A4横版、竖版近A3→A3竖版")
    ap.add_argument("--to", metavar="纸张",
                    help="强制把所有文件统一到该尺寸：A4 / A3 / A5 / B5 / Letter")
    ap.add_argument("--landscape", action="store_true", help="配合 --to 使用，横版")
    ap.add_argument("-y", "--yes", action="store_true", help="转换时不再交互确认")
    args = ap.parse_args(argv)

    rule: Optional[Rule] = None
    if args.to:
        try:
            rule = make_rule(args.to, args.landscape)
        except KeyError:
            print(f"未知纸张尺寸：{args.to}（可用：{'、'.join(PAPER_MM)}）")
            return 1
        print(f"强制目标：{rule.label}（{rule.w_mm:g}×{rule.h_mm:g}mm）")

    tol = max(args.tol, 0.0) / 100.0
    files = collect_pdfs(args.paths)
    if not files:
        print("没有找到 PDF 文件")
        return 1
    print(f"共 {len(files)} 个 PDF 文件，容差 ±{args.tol:g}%\n")

    if args.action == "scan":
        infos = []
        for i, fp in enumerate(files, 1):
            fi = scan_file(fp, tol, extra=args.extra, rule=rule)
            infos.append(fi)
            print(f"[{i}/{len(files)}] " + (describe_pages(fi) if fi.ok else f"{fi.name}  ❌ {fi.error}"))
            print()
        if args.csv:
            print("报告已导出：" + write_report_csv(infos, args.csv))
        return 0

    # convert
    total_conv = sum(len(scan_file(f, tol, extra=args.extra, rule=rule).convertible_pages)
                     for f in files)
    print(f"预计需要转换 {total_conv} 页"
          + ("（覆盖原文件）" if not args.outdir else f"（输出到 {args.outdir}）"))
    if total_conv == 0:
        print("没有符合条件的页面，未做任何改动。")
        return 0
    if not args.yes:
        if input("继续？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消")
            return 1
    ok = fail = 0
    for i, fp in enumerate(files, 1):
        op = os.path.join(args.outdir, os.path.basename(fp)) if args.outdir else None
        r = convert_file(fp, tol=tol, backup=not args.no_backup, out_path=op,
                         extra=args.extra, rule=rule)
        if r.ok and r.converted:
            ok += 1
            print(f"[{i}/{len(files)}] ✅ {os.path.basename(fp)}  转换 {r.converted}/{r.total} 页")
        elif r.ok:
            print(f"[{i}/{len(files)}] － {os.path.basename(fp)}  跳过：{r.skipped_reason}")
        else:
            fail += 1
            print(f"[{i}/{len(files)}] ❌ {os.path.basename(fp)}  {r.error}")
    print(f"\n完成：成功 {ok} 个，失败 {fail} 个")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
