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
from tkinter import filedialog, messagebox   # noqa: E402

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

    print("\n[2] 只看需转换")
    app.var_only_need.set(True)
    app._render_files()
    app.update()
    check(len(rows(app)) == 3, f"筛选后 3 行（实际 {len(rows(app))}）")
    check("列表已筛选" in app.var_stats.get(), "统计条标注已筛选")
    app.var_only_need.set(False)
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
    app.var_only_need.set(True)
    app._render_files()
    check(all(app.row_of_iid[k] is not target_fi for k in app.row_of_iid),
          "筛选【只看需转换】时跳过的文件被隐藏")
    app.var_only_need.set(False)
    app._render_files()
    dlg3 = gui.RuleDialog(app, [target_fi])
    check(dlg3.var_size.get() == gui.SKIP_TEXT, "跳过规则也能回显")
    dlg3.var_size.set(gui.AUTO_TEXT)
    dlg3._ok()
    check(dlg3.result.mode == core.RULE_AUTO, "自动规则")
    app.apply_rule([target_fi], core.make_rule("A4", False))

    print("\n[6] 预览对比窗口")
    pv = gui.PreviewWindow(app, target_fi)
    pump(app, timeout=0.4)
    check(pv.lbl0.cget("image") != "", "左侧渲染出原始页图片")
    check(pv.lbl1.cget("image") != "", "右侧渲染出转换后图片")
    check("295.0×418.0mm" in pv.cap0.cget("text"), f"原始说明：{pv.cap0.cget('text')}")
    check("210.0×297.0mm" in pv.cap1.cget("text"), f"转换说明：{pv.cap1.cget('text')}")
    check("留白" in pv.var_note.get(), f"留白提示：{pv.var_note.get()}")
    letter = [fi for fi in app.infos if "Letter" in fi.name][0]
    pv2 = gui.PreviewWindow(app, letter)
    pump(app, timeout=0.4)
    check(pv2.lbl1.cget("image") == "", "不转换的页右侧无图")
    check("不转换" in pv2.cap1.cget("text"), f"右侧说明：{pv2.cap1.cget('text')}")
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
    global SAVE_AS
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

    print("\n[11] 无异常弹窗")
    errs = [d for d in DIALOGS if d[0] in ("err", "warn")]
    check(not errs, f"没有错误弹窗（{errs}）")

    shutil.rmtree(root, ignore_errors=True)
    print("\n" + ("✅ 全部通过" if not FAILS else f"❌ 失败 {len(FAILS)} 项：{FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
