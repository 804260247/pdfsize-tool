# -*- coding: utf-8 -*-
"""真实 Tk 端到端自测（v1.2）：扫描 / 筛选 / 统计 / 右键菜单 / 逐文件规则 / 多选 / 预览 / 转换

运行（需要桌面环境或 Xvfb）：
    DISPLAY=:96 python _ui_test.py
不会修改任何真实文件：全部在临时目录里跑，弹窗自动应答。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ.setdefault("PDFSIZE_SETTINGS", os.path.join(tempfile.gettempdir(),
                                                       "pdfsize-test-settings.json"))
try:
    os.remove(os.environ["PDFSIZE_SETTINGS"])
except OSError:
    pass

import pdfsize_core as core            # noqa: E402
import pdfsize_gui as gui              # noqa: E402
from tkinter import filedialog, messagebox, ttk   # noqa: E402

FAILS: list[str] = []
DIALOGS: list[tuple[str, str]] = []
SAVE_AS = ""


def check(cond, msg: str) -> None:
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        FAILS.append(msg)


def pump(app, until=None, timeout: float = 25.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.update()
        if until is None:
            time.sleep(0.02)
            return True
        if until():
            app.update()
            return True
        time.sleep(0.02)
    return False


# ---- 弹窗自动应答
messagebox.askyesno = lambda *a, **k: (DIALOGS.append(("ask", a[0] if a else "")), True)[1]
messagebox.showinfo = lambda *a, **k: DIALOGS.append(("info", a[0] if a else ""))
messagebox.showwarning = lambda *a, **k: DIALOGS.append(("warn", a[0] if a else ""))
messagebox.showerror = lambda *a, **k: DIALOGS.append(("err", f"{a}"))


def _save_as(**kw):
    return SAVE_AS


filedialog.asksaveasfilename = _save_as
filedialog.askdirectory = lambda **kw: ""


def rows(app):
    return app.tree.get_children()


def cell(app, iid, col):
    idx = list(app.tree["columns"]).index(col)
    return app.tree.item(iid, "values")[idx]


def main() -> int:
    global SAVE_AS
    root = tempfile.mkdtemp(prefix="pdfsize_e2e_")
    src = os.path.join(root, "src")
    out = os.path.join(root, "out")
    core.make_samples(src)

    print("\n[1] 启动与扫描")
    app = gui.App()
    app.update()
    app.paths = [src]
    app.do_scan()
    ok = pump(app, lambda: not app.busy and len(app.infos) > 0)
    check(ok, "扫描完成")
    check(len(rows(app)) == 5, f"文件表 5 行（实际 {len(rows(app))}）")
    st = app.var_stats.get()
    check("文件 5 个" in st, f"统计条含文件总数：{st}")
    check("需转换 3 个（4 页）" in st,
          "统计条含需转换数（示例1 + 示例2 + 示例4 的 2 页）")

    print("\n[1b] 添加文件夹后自动扫描 + 醒目提示条")
    app.clear_all()
    app.update()
    check(not rows(app), "清空后没有行")
    app._add_paths([src])
    ok = pump(app, lambda: not app.busy and len(app.infos) == 5, timeout=20)
    check(ok, "添加文件夹后自动开始扫描并完成")
    check(len(rows(app)) == 5, f"自动扫描后 5 行（实际 {len(rows(app))}）")
    btxt = str(app.lbl_banner.cget("text"))
    check("扫描完成" in btxt, f"提示条显示扫描结果：{btxt}")
    check(app.lbl_banner.winfo_ismapped() == 1, "提示条可见")
    app.var_autoscan.set(False)
    app.clear_all()
    app._add_paths([src])
    app.update()
    check(len(app.infos) == 0, "关掉【添加后自动扫描】后不自动扫描")
    check("点【① 扫描尺寸】" in str(app.lbl_banner.cget("text")), "提示条改为提示手动扫描")
    app.var_autoscan.set(True)
    app.do_scan()
    pump(app, lambda: not app.busy and len(app.infos) == 5, timeout=20)

    print("\n[2] 筛选：只看需转换 / 只看「其他」")
    app.var_filter.set(gui.FILTER_NEED)
    app._render_files()
    app.update()
    check(len(rows(app)) == 3, f"筛选后 3 行（实际 {len(rows(app))}）")
    check("筛选：只看需转换" in app.var_stats.get(), f"统计条标注已筛选：{app.var_stats.get()}")
    app.var_filter.set(gui.FILTER_OTHER)
    app._render_files()
    app.update()
    others = [app.row_of_iid[k] for k in rows(app)]
    check(len(others) >= 1 and all(any(p.kind == core.K_OTHER for p in fi.pages)
                                   for fi in others),
          f"只看「其他」筛出 {len(others)} 个且都含其他尺寸")
    check(any("Letter" in fi.name for fi in others), "Letter 文件被归到「其他」里")
    app.var_filter.set(gui.FILTER_ALL)
    app._render_files()
    app.update()
    check(len(rows(app)) == 5, "取消筛选恢复 5 行")

    print("\n[3] 逐文件规则（把 近A3竖版 强制成 A4 竖版）")
    target_fi = [fi for fi in app.infos if "示例5" in fi.name][0]
    check(target_fi.convertible_pages == [], "近A3竖版 默认不转换")
    app.apply_rule([target_fi], core.make_rule("A4", False))
    app.update()
    check(len(target_fi.convertible_pages) == 1, "强制 A4 后 1 页需转换")
    iid = [k for k, v in app.row_of_iid.items() if v is target_fi][0]
    check(cell(app, iid, "target") == "A4竖版",
          f"目标尺寸列显示 A4竖版（实际 {cell(app, iid, 'target')}）")
    check("需转换 4 个（5 页）" in app.var_stats.get(),
          f"统计条更新为 4 个：{app.var_stats.get()}")

    print("\n[3b] 「目标尺寸」列内下拉（单选 + 多选批量）")
    iid5 = [k for k, v in app.row_of_iid.items() if "示例5" in v.name][0]
    bb = app.tree.bbox(iid5, "target")
    check(bool(bb), "取到目标尺寸格子的坐标")

    class _Ev:
        pass

    ev = _Ev()
    ev.x, ev.y = bb[0] + 5, bb[1] + 5
    app.tree.selection_set(iid5)
    app._on_click(ev)
    app.update()
    check(app._combo is not None, "点击「目标尺寸」格子弹出下拉")
    check(app._combo is not None and app._combo.get() == "A4竖版",
          f"下拉回显当前规则（{app._combo.get() if app._combo else '-'}）")
    check(gui.SKIP_TEXT in tuple(app._combo["values"]), "下拉含「跳过」")
    check("A4竖版" in tuple(app._combo["values"]), "下拉含 A4竖版")
    app._combo.set("A3横版")
    app._combo_apply(app._combo)
    app.update()
    iid5 = [k for k, v in app.row_of_iid.items() if v is target_fi][0]
    check(target_fi.rule.label == "A3横版", f"列内下拉设定生效（{target_fi.rule.label}）")
    check(cell(app, iid5, "target") == "A3横版", "目标尺寸列已刷新")

    ids3 = list(app.row_of_iid)[:3]
    fis3 = [app.row_of_iid[k] for k in ids3]
    app.tree.selection_set(ids3)
    app._open_target_combo(ids3[0])
    cb = app._combo
    check(cb is not None, "多选时也能打开下拉")
    cb.set("A3横版")
    app._combo_apply(cb)
    app.update()
    check(all(fi.rule.label == "A3横版" for fi in fis3), "多选批量设定生效")
    check(app._combo is None, "设定后下拉已关闭")
    app.apply_rule(list(app.row_of_iid.values()), core.Rule())
    app.update()

    print("\n[3c] 「目标尺寸」下拉单击一次就展开（不再闪一下要点击两次）")
    iid_c = list(app.row_of_iid)[0]
    bb_c = app.tree.bbox(iid_c, "target")
    app.tree.event_generate("<Button-1>", x=bb_c[0] + 5, y=bb_c[1] + 5)
    app.update()
    cb_c = app._combo
    check(cb_c is not None, "一次点击就创建了下拉")
    pd = str(cb_c) + ".popdown"
    check(int(app.tk.call("winfo", "ismapped", pd)) == 1, "下拉已展开（popdown 可见）")
    for _ in range(30):                    # 跨过原来 150~250ms 会自动关闭的时间窗
        app.update()
        time.sleep(0.02)
    check(app._combo is cb_c, "600ms 后下拉仍在（不会自己闪掉）")
    check(int(app.tk.call("winfo", "ismapped", pd)) == 1, "600ms 后 popdown 仍可见")
    ev_out = type("_Ev", (), {"widget": app.tree})()
    app._click_elsewhere(ev_out)
    app.update()
    check(app._combo is None, "点窗口别处后下拉收起")

    print("\n[4] 多选 + 右键菜单")
    iid_t = [k for k, v in app.row_of_iid.items() if v is target_fi][0]
    ids = [iid_t] + [k for k in app.row_of_iid if k != iid_t][:2]
    app.tree.selection_set(ids)
    app.update()
    sel = app._selected_infos()
    check(len(sel) == 3, f"多选 3 个（实际 {len(sel)}）")
    check("已选 3 个" in app.var_stats.get(), "统计条显示已选数量")
    app._build_menu(sel)
    labels = [app.menu.entrycget(i, "label") for i in range(app.menu.index("end") + 1)
              if app.menu.type(i) == "command"]
    check(any("批量设定转换尺寸" in x for x in labels), "右键菜单有批量设定尺寸")
    check(any("仅转换选中的 3 个文件" in x for x in labels), "右键菜单有仅转换选中")
    check(any("打开选中的 3 个文件" in x for x in labels), "右键菜单有打开文件")
    check(any("预览" in x for x in labels), "右键菜单有预览")
    app.menu.destroy()
    app.menu = None

    print("\n[4c] 表头点击排序")
    app.var_filter.set(gui.FILTER_ALL)
    app._render_files()
    app.update()
    names_before = [app.row_of_iid[i].name for i in rows(app)]
    app._sort_by("pages")
    app.update()
    v1 = [int(cell(app, i, "pages")) for i in rows(app)]
    check(v1 == sorted(v1), f"按页数升序：{v1}")
    check("▲" in app.tree.heading("pages")["text"],
          f"表头显示升序箭头：{app.tree.heading('pages')['text']}")
    app._sort_by("pages")
    app.update()
    v2 = [int(cell(app, i, "pages")) for i in rows(app)]
    check(v2 == sorted(v2, reverse=True), f"再点一次变降序：{v2}")
    check("▼" in app.tree.heading("pages")["text"],
          f"表头显示降序箭头：{app.tree.heading('pages')['text']}")
    app._sort_by("name")
    app.update()
    ns = [app.row_of_iid[i].name.lower() for i in rows(app)]
    check(ns == sorted(ns), f"按文件名升序：{[n[:6] for n in ns]}")
    nos = [int(cell(app, i, "no")) for i in rows(app)]
    check(sorted(nos) == list(range(1, len(app.infos) + 1)),
          f"序号列始终是原始序号：{nos}")
    app._sort_by("conv")
    app.update()
    cv = [int(cell(app, i, "conv")) for i in rows(app)]
    check(cv == sorted(cv), f"按可转换页数升序：{cv}")
    app._sort_by("no")                     # 点「序号」= 回到原顺序
    app.update()
    check([app.row_of_iid[i].name for i in rows(app)] == names_before, "点序号列恢复原顺序")

    print("\n[4b] 双击 / 右键真实交互")
    iid0 = list(app.row_of_iid)[0]
    bbox = app.tree.bbox(iid0, "name")

    class _E:
        pass

    e = _E()
    e.x, e.y = bbox[0] + 5, bbox[1] + 5
    e.x_root, e.y_root = 0, 0
    app._on_right_click(e)
    app.update()
    check(app.menu is not None and app.menu.index("end") >= 0, "右键弹出菜单")
    app.menu.unpost()
    app.menu.destroy()
    app.menu = None
    n_before = len(app._previews)
    app._on_double(e)
    pump(app, timeout=1.0)
    check(len(app._previews) == n_before + 1,
          f"双击打开预览窗口（{n_before} -> {len(app._previews)}）")
    for p in list(app._previews):
        p._close()
    app._previews.clear()

    print("\n[5] 批量恢复自动规则")
    app.apply_rule(sel, core.Rule())
    app.update()
    check(all(fi.rule.mode == core.RULE_AUTO for fi in sel), "恢复自动规则（含被强制的示例5）")
    check(target_fi.convertible_pages == [], "近A3竖版 回到不转换")
    check("需转换 3 个（4 页）" in app.var_stats.get(), f"统计条回到 3 个：{app.var_stats.get()}")
    app.apply_rule([target_fi], core.make_rule("A4", False))

    print("\n[5b] 设定尺寸对话框")
    dlg = gui.RuleDialog(app, [target_fi])
    check(dlg.var_size.get() == "A4" and dlg.var_dir.get() == "竖版",
          f"回显已有规则（{dlg.var_size.get()} / {dlg.var_dir.get()}）")
    dlg.var_size.set("A3")
    dlg._sync()
    check(str(dlg.cb_dir.cget("state")) == "readonly", "选了纸张后方向可编辑")
    dlg.var_dir.set("横版")
    dlg._ok()
    check(dlg.result is not None and dlg.result.w_mm == 420 and dlg.result.h_mm == 297,
          f"A3 横版 = 420×297（实际 {dlg.result.size if dlg.result else None}）")
    dlg2 = gui.RuleDialog(app, [target_fi])
    dlg2.var_size.set(gui.SKIP_TEXT)
    dlg2._sync()
    check(str(dlg2.cb_dir.cget("state")) == "disabled", "选【跳过】时方向禁用")
    dlg2._ok()
    check(dlg2.result.mode == core.RULE_SKIP, "跳过规则生效")
    app.apply_rule([target_fi], dlg2.result)
    check(target_fi.convertible_pages == [], "跳过后该文件不再需要转换")
    check("已跳过 1 个" in app.var_stats.get(), f"统计条显示已跳过：{app.var_stats.get()}")
    app.var_filter.set(gui.FILTER_NEED)
    app._render_files()
    check(all(app.row_of_iid[k] is not target_fi for k in app.row_of_iid),
          "筛选【只看需转换】时跳过的文件被隐藏")
    app.var_filter.set(gui.FILTER_ALL)
    app._render_files()
    dlg3 = gui.RuleDialog(app, [target_fi])
    check(dlg3.var_size.get() == gui.SKIP_TEXT, "跳过规则也能回显")
    dlg3.var_size.set(gui.AUTO_TEXT)
    dlg3._ok()
    check(dlg3.result.mode == core.RULE_AUTO, "自动规则")
    app.apply_rule([target_fi], core.make_rule("A4", False))

    print("\n[6] 预览对比窗口")
    pv = gui.PreviewWindow(app, target_fi)
    pump(app, lambda: pv.pane0.photo is not None, timeout=8)
    check(pv.pane0.photo is not None, "左侧渲染出原始页图片")
    check(pv.pane1.photo is not None, "右侧渲染出转换后图片")
    check("295.0×418.0mm" in pv.pane0.cap.cget("text"), f"原始说明：{pv.pane0.cap.cget('text')}")
    check("210.0×297.0mm" in pv.pane1.cap.cget("text"), f"转换说明：{pv.pane1.cap.cget('text')}")
    check("留白" in pv.var_note.get(), f"留白提示：{pv.var_note.get()}")

    print("\n[6b] 预览：两侧各自独立的显示比例（默认 100%）")
    check(pv.pane0.var_scale.get() == gui.SCALE_ACTUAL and pv.pane1.var_scale.get() == gui.SCALE_ACTUAL,
          f"两侧默认都是 100%（{pv.pane0.var_scale.get()} / {pv.pane1.var_scale.get()}）")
    check(not hasattr(pv, "var_scale") and not hasattr(pv, "cb_scale"),
          "窗口上已经没有全局比例控件")
    # 标签必须在 own 下拉框的左边（pack side=right 是先放的靠最右）
    for name, pane in (("原始", pv.pane0), ("转换后", pv.pane1)):
        lab = [w for w in pane.cb_scale.master.winfo_children()
               if isinstance(w, ttk.Label) and "显示比例" in str(w.cget("text"))]
        check(len(lab) == 1, f"{name}侧有「显示比例」标签")
        check(lab[0].winfo_x() + lab[0].winfo_width() <= pane.cb_scale.winfo_x(),
              f"{name}侧标签在下拉框前面（标签右边界 {lab[0].winfo_x() + lab[0].winfo_width()} "
              f"<= 下拉左边界 {pane.cb_scale.winfo_x()}）")
    i0, i1 = pv.pane0.photo, pv.pane1.photo
    check(i0.width() > i1.width() and i0.height() > i1.height(),
          f"原始图 {i0.width()}×{i0.height()} 明显大于转换图 {i1.width()}×{i1.height()}")
    dpi = app.sys_dpi
    check(abs(i0.width() - 295 / 25.4 * dpi) <= 2,
          f"原始图宽 {i0.width()}px = 295mm @{dpi:.0f}dpi（实际大小）")
    check(abs(i1.width() - 210 / 25.4 * dpi) <= 2,
          f"转换图宽 {i1.width()}px = 210mm @{dpi:.0f}dpi（同一比例，可直接比大小）")
    check(f"{dpi:.0f}dpi" in pv.pane0.cap.cget("text"), "说明里标了实际 dpi/像素")
    bw, bh = pv.pane0.box()
    check(i0.width() > bw, f"原始图比面板宽（{i0.width()} > {bw}），要看全得滚动")
    sr = str(pv.pane0.canvas.cget("scrollregion")).split()
    check(len(sr) == 4 and int(float(sr[2])) >= i0.width(), f"滚动区域覆盖整图：{sr}")

    # 只把左侧改成「适应窗口」，右侧必须不受影响
    pv.pane0.var_scale.set(gui.SCALE_FIT)
    pv._render()
    app.update()
    check(pv.pane0.photo.width() <= pv.pane0.box()[0] + 4,
          f"左侧切「适应窗口」后原始图缩到 {pv.pane0.photo.width()}px 放得下")
    check(pv.pane1.photo.width() == i1.width(),
          f"右侧不受影响，仍是 {pv.pane1.photo.width()}px（实际大小）")
    check(pv.pane1.var_scale.get() == gui.SCALE_ACTUAL, "右侧下拉仍是 100%")
    check("左侧适应窗口、右侧实际大小" in pv.var_note.get(),
          f"底部提示两侧比例不同：{pv.var_note.get()}")

    # 再把右侧改成「适应窗口」，左侧保持；然后两边都适应窗口
    pv.pane1.var_scale.set(gui.SCALE_FIT)
    pv._render()
    app.update()
    check(pv.pane1.photo.width() <= pv.pane1.box()[0] + 4,
          f"右侧切「适应窗口」后转换图缩到 {pv.pane1.photo.width()}px 放得下")
    check("两侧各自缩放到窗口" in pv.var_note.get(), f"两侧都适应窗口时提示：{pv.var_note.get()}")

    pv.pane0.var_scale.set(gui.SCALE_ACTUAL)
    pv.pane1.var_scale.set(gui.SCALE_ACTUAL)
    pv._render()
    app.update()
    check(pv.pane0.photo.width() == i0.width(), "左侧切回 100% 恢复实际大小")
    check(pv.pane1.photo.width() == i1.width(), "右侧切回 100% 恢复实际大小")
    check("可直接比大小" in pv.var_note.get(), "两侧都实际大小时提示可直接比大小")

    letter = [fi for fi in app.infos if "Letter" in fi.name][0]
    pv2 = gui.PreviewWindow(app, letter)
    pump(app, lambda: pv2.pane0.photo is not None, timeout=8)
    check(pv2.pane1.photo is None, "不转换的页右侧无图")
    check("不转换" in pv2.pane1.cap.cget("text"), f"右侧说明：{pv2.pane1.cap.cget('text')}")
    pv._close()
    pv2._close()
    app._previews.clear()

    print("\n[7] 仅转换选中文件（另存到目录）")
    app.var_mode.set("outdir")
    app.var_outdir.set(out)
    app._sync_outdir()
    app.do_convert(targets=[target_fi])
    ok = pump(app, lambda: not app.busy and os.path.isdir(out))
    check(ok, "转换线程结束")
    outp = os.path.join(out, os.path.basename(target_fi.path))
    check(os.path.isfile(outp), "输出文件存在")
    fi_out = core.scan_file(outp)
    check(fi_out.pages[0].size_text == "210.0×297.0mm",
          f"输出尺寸 210×297（实际 {fi_out.pages[0].size_text}）")
    check(len(os.listdir(out)) == 1,
          f"只转换了选中的 1 个文件（目录里 {len(os.listdir(out))} 个）")
    pump(app, lambda: not app.busy and len(app.infos) == 5, timeout=20)
    still = [fi for fi in app.infos
             if os.path.basename(fi.path) == os.path.basename(target_fi.path)]
    check(bool(still) and still[0].rule.mode == core.RULE_TARGET,
          f"重扫后逐文件规则仍在（{still[0].rule.label if still else '缺失'}）")

    print("\n[7b] 转换后的「已转换」标记")
    key5 = os.path.normcase(os.path.abspath(target_fi.path))
    rec = app.converted.get(key5)
    check(rec is not None and rec.get("pages") == 1, f"记录了已转换：{rec}")
    pump(app, lambda: not app.busy and len(app.infos) == 5, timeout=20)
    iidT = [k for k, v in app.row_of_iid.items()
            if os.path.normcase(os.path.abspath(v.path)) == key5]
    check(bool(iidT), "重扫后仍能在列表里找到该文件")
    mk = str(cell(app, iidT[0], "mark"))
    check("✔" in mk, f"「转换」列有标记：{mk}")
    check("done" in tuple(app.tree.item(iidT[0], "tags")), "行带已转换底色标记")
    check("已转换 1 个" in app.var_stats.get(), f"统计条含已转换：{app.var_stats.get()}")
    app.var_filter.set(gui.FILTER_DONE)
    app._render_files()
    app.update()
    check(len(rows(app)) == 1, f"只看已转换 1 行（实际 {len(rows(app))}）")
    app.var_filter.set(gui.FILTER_ALL)
    app._render_files()
    SAVE_AS = os.path.join(root, "r2.csv")
    app.export_csv()
    with open(SAVE_AS, encoding="utf-8-sig") as fh:
        head2 = fh.readline().strip()
        body2 = fh.read()
    check("转换记录" in head2, f"CSV 含转换记录列：{head2}")
    check("已转换 1 页" in body2, "CSV 里记录了转换页数")
    fiT = [fi for fi in app.infos
           if os.path.normcase(os.path.abspath(fi.path)) == key5][0]
    app.clear_marks([fiT])
    app.update()
    iidT = [k for k, v in app.row_of_iid.items() if v is fiT]
    check(not app.converted, "清除标记后记录为空")
    check(bool(iidT) and str(cell(app, iidT[0], "mark")) == "", "清除后转换列为空")

    print("\n[12] 转换规则预设")
    app.apply_rule(list(app.row_of_iid.values()), core.Rule())
    app.update()
    check("需转换 3 个（4 页）" in app.var_stats.get(), f"标准预设基线：{app.var_stats.get()}")
    app.var_preset.set(gui.PRESETS[2][0])
    app._on_preset()
    app.update()
    check(app.global_rule.mode == core.RULE_TARGET and app.global_rule.label == "A4竖版",
          f"预设生成全局强制规则：{app.global_rule.label}")
    check("需转换 5 个" in app.var_stats.get(), f"全部统一后 5 个都需转换：{app.var_stats.get()}")
    check(all(cell(app, k, "target") == "A4竖版" for k in rows(app)),
          "目标尺寸列全部显示 A4竖版")
    app.var_preset.set(gui.PRESETS[1][0])
    app._on_preset()
    app.update()
    check(app.global_rule.mode == core.RULE_AUTO and app.var_extra.get() is True,
          "扩展预设 = 自动判定 + 附加规则")
    check("需转换 4 个" in app.var_stats.get(), f"扩展预设把 A3竖版算进来：{app.var_stats.get()}")
    app.var_preset.set(gui.PRESETS[0][0])
    app._on_preset()
    app.update()
    check("需转换 3 个（4 页）" in app.var_stats.get(), f"回到标准预设：{app.var_stats.get()}")

    print("\n[12b] 预设强制尺寸真实转换（全部统一 A3 横版）")
    out2 = os.path.join(root, "out2")
    letter = [fi for fi in app.infos if "Letter" in fi.name][0]
    app.var_preset.set(gui.PRESETS[3][0])
    app._on_preset()
    app.update()
    check(letter.rule.label == "A3横版" and len(letter.convertible_pages) == 1,
          f"Letter 在强制预设下 1 页需转换（{letter.rule.label}）")
    app.var_mode.set("outdir")
    app.var_outdir.set(out2)
    app._sync_outdir()
    app.do_convert(targets=[letter])
    ok = pump(app, lambda: not app.busy and os.path.isdir(out2), timeout=30)
    check(ok, "强制预设转换线程结束")
    outp2 = os.path.join(out2, os.path.basename(letter.path))
    check(os.path.isfile(outp2), "强制预设输出文件存在")
    fi2 = core.scan_file(outp2)
    check(fi2.pages[0].size_text == "420.0×297.0mm",
          f"输出被改成 A3 横版（实际 {fi2.pages[0].size_text}）")
    pump(app, lambda: not app.busy and len(app.infos) == 5, timeout=20)
    app.var_preset.set(gui.PRESETS[0][0])
    app._on_preset()
    app.var_mode.set("backup")
    app._sync_outdir()
    app.update()

    print("\n[13] 按页面尺寸分类提取")
    xout = os.path.join(root, "extract")
    xfiles = [fi for fi in app.infos if fi.ok]
    dlg = gui.ExtractDialog(app, xfiles, default_out=xout)
    dlg.update()
    check(dlg.var_out.get() == xout, "对话框带入默认输出目录")
    check("预计" in dlg.var_hint.get(), f"对话框给出预计数量：{dlg.var_hint.get()[:40]}")
    check(dlg.var_group.get() == gui.GROUP_KIND_TEXT, "默认按判定类别分类")
    check(dlg.var_split.get() == gui.SPLIT_FILE_TEXT, "默认每类合并成一个 PDF")
    dlg.var_group.set(gui.GROUP_EXACT_TEXT)
    dlg.var_split.set(gui.SPLIT_PAGE_TEXT)
    dlg._sync()
    dlg.update()
    check("每页单独" in dlg.var_hint.get() and "实际尺寸" in dlg.var_hint.get(),
          "切换选项后提示同步更新")
    dlg._cancel()
    check(dlg.result is None, "取消返回 None")

    real_show = gui.ExtractDialog.show
    real_open = gui.open_in_system
    gui.ExtractDialog.show = lambda self: {"out": xout, "group": core.EXTRACT_GROUP_KIND,
                                          "split": core.EXTRACT_SPLIT_FILE}
    gui.open_in_system = lambda p: None
    try:
        app.extract_by_size(xfiles)
        ok = pump(app, lambda: not app.busy and os.path.isdir(xout), timeout=40)
        check(ok, "提取线程结束")
        dirs = sorted(d for d in os.listdir(xout) if os.path.isdir(os.path.join(xout, d)))
        check(len(dirs) >= 2, f"生成了按类别命名的子文件夹：{dirs}")
        check(all(d in (core.K_A4P, core.K_A3L, core.K_A4L, core.K_A3P, core.K_OTHER)
                  for d in dirs), "文件夹名都是判定类别")
        made = sum(len([f for f in fs if f.lower().endswith(".pdf")])
                   for _d, _s, fs in os.walk(xout))
        check(made >= len(dirs), f"共生成 {made} 个 PDF")
        # 抽查：A4竖版文件夹里的文件页数合计 = 原文件里 A4竖版的页数
        want = sum(sum(1 for p in fi.pages if p.kind == core.K_A4P) for fi in xfiles)
        got = 0
        for f in os.listdir(os.path.join(xout, core.K_A4P)) if os.path.isdir(
                os.path.join(xout, core.K_A4P)) else []:
            got += core.scan_file(os.path.join(xout, core.K_A4P, f)).page_count
        check(got == want, f"A4竖版页数一致（{got} == {want}）")
        allfiles = [os.path.join(d, f) for d, _s, fs in os.walk(xout) for f in fs]
        check(all(f.lower().endswith(".pdf") for f in allfiles), "没有残留临时文件")
        # 原文件未被改动
        check(os.path.isdir(src) and len([f for f in os.listdir(src)]) == 5,
              "源目录文件数不变（提取不改原文件）")
        app.update()
        check("提取完成" in str(app.lbl_banner.cget("text")),
              f"提示条显示提取结果：{app.lbl_banner.cget('text')}")

        # 每页一个
        xout2 = os.path.join(root, "extract_page")
        gui.ExtractDialog.show = lambda self: {"out": xout2, "group": core.EXTRACT_GROUP_EXACT,
                                              "split": core.EXTRACT_SPLIT_PAGE}
        app.extract_by_size([xfiles[0]])
        ok = pump(app, lambda: not app.busy and os.path.isdir(xout2), timeout=40)
        check(ok, "每页一个模式结束")
        n2 = sum(len([f for f in fs if f.endswith(".pdf")]) for _d, _s, fs in os.walk(xout2))
        check(n2 == xfiles[0].page_count,
              f"每页一个：{n2} 个 PDF（该文件 {xfiles[0].page_count} 页）")
        sub = [d for d in os.listdir(xout2) if os.path.isdir(os.path.join(xout2, d))]
        check(all("mm" in d for d in sub), f"按实际尺寸命名：{sub}")
    finally:
        gui.ExtractDialog.show = real_show
        gui.open_in_system = real_open

    print("\n[8b] 切换界面缩放（重建整个界面）")
    before = len(rows(app))
    app.var_zoom.set("150%")
    app._on_zoom()
    app.update()
    check(abs(app.k - 1.5 * app.sys_dpi / 96) < 1e-6, f"缩放系数更新为 k={app.k:.2f}")
    check(len(rows(app)) == before, f"重建后文件表行数不变（{len(rows(app))}）")
    check(app.paned.winfo_height() > 50, "分割窗重建正常")
    check(gui._load_settings(os.environ["PDFSIZE_SETTINGS"]).get("zoom") == 1.5, "缩放已保存")
    check(any("目标尺寸" in app.tree.heading(c)["text"] for c in app.tree["columns"]),
          "重建后表头仍是新版列")
    app.var_zoom.set("100%（跟随系统）")
    app._on_zoom()
    app.update()
    check(abs(app.k - app.sys_dpi / 96) < 1e-6, "回到 100%")

    print("\n[8] 分割条可拖动 + 记忆")
    t_wait = time.time()            # 等 after(80) 的「恢复上次分割位置」先跑完
    while time.time() - t_wait < 0.4:
        app.update()
        time.sleep(0.02)
    h = app.paned.winfo_height()
    app.paned.sash_place(0, 0, 300)
    app.update()
    y = app.paned.sash_coord(0)[1]
    check(abs(y - 300) <= 3, f"分割条移到 300（实际 {y}，pane 高 {h}）")
    app._on_close()

    print("\n[9] 设置被记住")
    saved = gui._load_settings(os.environ["PDFSIZE_SETTINGS"])
    check("sash" in saved and 0 < saved["sash"] < 1,
          f"分割条比例已保存：{saved.get('sash')}")

    print("\n[10] CSV 报告带目标尺寸列")
    app2 = gui.App()
    app2.update()
    app2.paths = [src]
    app2.do_scan()
    pump(app2, lambda: not app2.busy and len(app2.infos) == 5)
    app2.apply_rule([app2.infos[0]], core.make_rule("A4", True))
    SAVE_AS = os.path.join(root, "r.csv")
    app2.export_csv()
    with open(SAVE_AS, encoding="utf-8-sig") as fh:
        head = fh.readline().strip()
        body = fh.read()
    check("目标尺寸" in head, f"表头含目标尺寸：{head}")
    check("A4横版" in body, "数据里出现 A4横版")
    app2._on_close()

    print("\n[14] 转换后不应误弹「请先添加PDF文件」+ 按钮可用状态")
    src2 = os.path.join(root, "flow")
    os.makedirs(src2, exist_ok=True)
    core.make_samples(src2)
    app3 = gui.App()
    app3.update()
    check(str(app3.btn_scan["state"]) == "disabled", "未加载文件时【扫描尺寸】禁用")
    check(str(app3.btn_convert["state"]) == "disabled", "未加载文件时【执行转换】禁用")
    check(str(app3.btn_extract["state"]) == "disabled", "未加载文件时【按尺寸分类提取】禁用")
    check(str(app3.btn_clear["state"]) == "disabled", "未加载文件时【清空列表】禁用")

    DIALOGS.clear()
    app3.var_autoscan.set(False)
    app3.var_mode.set("overwrite")
    app3._add_paths([src2])                 # 以「添加文件夹」方式加入（用户的实际用法）
    app3.update()
    check(len(app3.files) == 5 and len(app3.paths) == 1,
          f"加载 5 个文件（paths={len(app3.paths)}，files={len(app3.files)}）")
    check(str(app3.btn_scan["state"]) == "normal", "加载后【扫描尺寸】可用")
    check(str(app3.btn_convert["state"]) == "disabled", "还没扫描时【执行转换】仍禁用")

    app3.do_scan()
    pump(app3, lambda: not app3.busy and len(app3.infos) == 5)
    check(str(app3.btn_convert["state"]) == "normal", "扫描后【执行转换】可用")

    # 用户常用操作：移除列表里的某一行。以前会把包含它的文件夹整个从输入源里删掉
    iid0 = rows(app3)[0]
    app3.tree.selection_set(iid0)
    app3.remove_selected()
    app3.update()
    check(len(app3.paths) == 1, f"移除一行后输入源仍在（{len(app3.paths)} 项）")
    check(len(app3.files) == 4, f"已加载列表剩 4 个（{len(app3.files)}）")

    DIALOGS.clear()
    app3.do_scan()
    pump(app3, lambda: not app3.busy)
    check(len(app3.infos) == 4, f"重新扫描不复活被移除的文件（{len(app3.infos)} 个）")
    check(not any("请先添加" in t for _, t in DIALOGS), f"扫描时没有「请先添加」弹窗（{DIALOGS}）")

    DIALOGS.clear()
    app3.do_convert()
    pump(app3, lambda: not app3.busy, 40)
    time.sleep(0.5)
    app3.update()
    titles = [t for _, t in DIALOGS]
    check(not any("请先添加" in t for t in titles), f"转换后没有「请先添加」弹窗（{titles}）")
    check("转换完成" in titles, f"有转换完成提示（{titles}）")
    check(len(app3.infos) == 4, f"转换后自动重扫，列表仍是 4 个文件（{len(app3.infos)}）")

    DIALOGS.clear()
    app3.do_scan()
    pump(app3, lambda: not app3.busy)
    check(not any("请先添加" in t for _, t in DIALOGS),
          f"转换完成后再点【扫描尺寸】不弹窗（{DIALOGS}）")
    check(len(app3.infos) == 4, f"再扫描仍是 4 个（{len(app3.infos)}）")

    app3.clear_all()
    app3.update()
    check(str(app3.btn_scan["state"]) == "disabled", "清空后【扫描尺寸】回到禁用")
    check(str(app3.btn_convert["state"]) == "disabled", "清空后【执行转换】回到禁用")
    check(not app3.files and not app3.paths and not app3.infos, "清空后输入源/列表都为空")
    app3._on_close()

    print("\n[11] 无异常弹窗")
    errs = [d for d in DIALOGS if d[0] in ("err", "warn")]
    check(not errs, f"没有错误弹窗（{errs}）")

    shutil.rmtree(root, ignore_errors=True)
    print("\n" + ("✅ 全部通过" if not FAILS else f"❌ 失败 {len(FAILS)} 项：{FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
