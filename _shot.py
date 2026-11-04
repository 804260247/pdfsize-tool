# -*- coding: utf-8 -*-
"""在 Xvfb 里真实渲染 GUI 并截图，用于目视检查布局与文字大小

用法：DISPLAY=:192 python _shot.py out.png [zoom] [--preview]
  加 --preview 时额外打开预览对比窗口，并把整个屏幕截下来（两张窗都在图里）
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["PDFSIZE_SETTINGS"] = os.path.join(tempfile.gettempdir(),
                                              "pdfsize-shot-settings.json")

import pdfsize_core as core      # noqa: E402
import pdfsize_gui as gui        # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shot.png"
ZOOM = None
for a in sys.argv[2:]:
    try:
        ZOOM = float(a)
    except ValueError:
        pass
WANT_PREVIEW = "--preview" in sys.argv


def grab(win, path):
    raw = path + ".raw.png"
    subprocess.run(["import", "-window", "root", raw], capture_output=True, text=True)
    geo = win.winfo_geometry()          # 例如 2360x1560+740+180
    size, xs, ys = geo.split("+")
    w, h = (int(v) for v in size.split("x"))
    subprocess.run(["convert", raw, "-crop", f"{w}x{h}+{int(xs)}+{int(ys)}",
                    "+repage", path], capture_output=True, text=True)
    try:
        os.remove(raw)
    except OSError:
        pass
    return geo


def main() -> None:
    d = tempfile.mkdtemp(prefix="pdfsize_shot_")
    core.make_samples(d)
    gui.enable_high_dpi()
    app = gui.App(zoom=ZOOM)
    app.paths = [d]
    app.do_scan()
    t0 = time.time()
    while time.time() - t0 < 15:
        app.update()
        if app.infos and not app.busy:
            break
        time.sleep(0.03)
    # 造一点可看的多样性：一个文件强制 A4 横版、一个跳过
    app.apply_rule([app.infos[0]], core.make_rule("A4", True))
    app.apply_rule([app.infos[2]], core.RULE_SKIP_OBJ)
    app.update()
    time.sleep(0.4)
    app.update()

    if WANT_PREVIEW:
        fi = [f for f in app.infos if f.convertible_pages][0]
        pv = gui.PreviewWindow(app, fi)
        app._previews.append(pv)
        t0 = time.time()
        while time.time() - t0 < 3:
            app.update()
            time.sleep(0.05)
        raw = OUT + ".raw.png"
        subprocess.run(["import", "-window", "root", raw], capture_output=True, text=True)
        os.replace(raw, OUT)
        print("shot:", OUT, "preview geo:", pv.winfo_geometry())
    else:
        print("shot:", OUT, "geo:", grab(app, OUT))

    print("dpi:", app.dpi, "k:", round(app.k, 2),
          "scaling:", app.tk.call("tk", "scaling"),
          "window:", app.winfo_width(), "x", app.winfo_height(),
          "stats:", app.var_stats.get())
    app.destroy()


if __name__ == "__main__":
    main()
