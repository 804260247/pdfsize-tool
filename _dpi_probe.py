# -*- coding: utf-8 -*-
"""打印不同 DPI / 缩放下的实际度量，用于核对高分屏适配是否真的生效"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["PDFSIZE_SETTINGS"] = "/tmp/pdfsize-test-settings.json"

import tkinter.font as tkfont
import pdfsize_gui as gui


def probe(zoom: float) -> None:
    app = gui.App(zoom=zoom)
    app.update_idletasks()
    f = tkfont.Font(family=app.family, size=gui.BASE_FONT_PT)
    print(f"  zoom={zoom:<5} sys_dpi={app.sys_dpi:6.1f} eff_dpi={app.dpi:6.1f} "
          f"k={app.k:4.2f} scaling={float(app.tk.call('tk', 'scaling')):4.2f} "
          f"| 窗口 {app.winfo_width()}x{app.winfo_height()} "
          f"| 字号{gui.BASE_FONT_PT}pt→{f.metrics('linespace')}px "
          f"| 行高{app.tree.tk.call('ttk::style','lookup','Treeview','-rowheight')} "
          f"| 列宽[0]={app.tree.column('name', 'width')}")
    app.destroy()


if __name__ == "__main__":
    print(f"X 屏幕: {os.environ.get('DISPLAY')}")
    for z in (1.0, 1.25, 1.5, 2.0):
        probe(z)
