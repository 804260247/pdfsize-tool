# -*- coding: utf-8 -*-
"""量化布局审计：不依赖字体，直接查控件几何，找越界 / 被压缩（文字会被截断）的控件

用法：DISPLAY=:192 python _layout_audit.py [zoom]
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("PDFSIZE_SETTINGS", os.path.join(tempfile.gettempdir(),
                                                       "pdfsize-audit-settings.json"))

import pdfsize_core as core      # noqa: E402
import pdfsize_gui as gui        # noqa: E402

problems = []
SQUEEZE = ("TLabel", "TButton", "TCheckbutton", "TRadiobutton")


def walk(w, depth=0):
    for c in w.winfo_children():
        try:
            x, y = c.winfo_x(), c.winfo_y()
            cw, ch = c.winfo_width(), c.winfo_height()
            rw, rh = c.winfo_reqwidth(), c.winfo_reqheight()
            pw = w.winfo_width()
            name = c.winfo_class()
            txt = ""
            try:
                txt = str(c.cget("text"))[:16]
            except Exception:
                pass
            if pw > 1 and x + cw > pw + 1:
                problems.append(f"[越界] {name} '{txt}' 右边缘 {x + cw} > 容器宽 {pw}")
            if name in SQUEEZE and rw > 1 and cw < rw - 2:
                problems.append(f"[被压缩] {name} '{txt}' 宽 {cw} < 需要 {rw}（文字会被截断）")
            if c.winfo_ismapped() and ch < rh - 2 and name in SQUEEZE:
                problems.append(f"[被压扁] {name} '{txt}' 高 {ch} < 需要 {rh}")
            print(f"{'  ' * depth}{name:<14} '{txt:<16}' x={x:<5} y={y:<5} w={cw:<5}/{rw:<5} h={ch}")
        except Exception:
            pass
        walk(c, depth + 1)


def main() -> int:
    zoom = float(sys.argv[1]) if len(sys.argv) > 1 else None
    app = gui.App(zoom=zoom)
    d = tempfile.mkdtemp(prefix="pdfsize_audit_")
    core.make_samples(d)
    app.paths = [d]
    app.do_scan()
    t0 = time.time()
    while time.time() - t0 < 15:
        app.update()
        if app.infos and not app.busy:
            break
        time.sleep(0.03)
    app.update_idletasks()
    app.update()
    print(f"== 窗口 {app.winfo_width()}x{app.winfo_height()}  k={app.k:.2f}  "
          f"统计条={app.var_stats.get()} ==")
    walk(app)
    print("\n== 结论 ==")
    print("\n".join(problems) if problems else "没有发现控件越界或被压缩")
    app.destroy()
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
