# -*- coding: utf-8 -*-
"""
build_exe.py —— 在 Windows 上把 GUI 打包成单文件 exe（PyInstaller 参数集中在这里）

用法（在 Windows 上，本目录内）：
    python build_exe.py            # 单文件 exe（推荐）
    python build_exe.py --onedir   # 目录版（启动更快）
    python build_exe.py --print-only  # 只打印将要执行的命令

打包完成后会自动运行 dist\\<名字>.exe --check 做冒烟测试，返回码非 0 视为失败。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "PDF页面尺寸工具"


def _force_utf8_stdio() -> None:
    """Windows 控制台默认编码不一定是 UTF-8（英文系统上是 cp1252），
    这时 print 中文会直接抛 UnicodeEncodeError 让脚本崩掉。统一切到 UTF-8。"""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
        except Exception:
            pass


_force_utf8_stdio()
ENTRY = "pdfsize_gui.py"

REQUIRED = ["pymupdf>=1.24"]
BUILD_REQUIRED = ["pyinstaller>=6.0"]

# PyMuPDF 2.x 会把 onnxruntime / networkx / 各种 RAG 模块一起带进来（OCR、版式分析用），
# 本工具只做页面几何与渲染，用不到 —— 排除掉，单文件体积能小一大截。
EXCLUDES = [
    "onnxruntime",
    "networkx",
    "pymupdf4llm",
    "pymupdf_layout",
    "pymupdf_rag",
    "pymupdf.pro",
    "scipy",
    "pandas",
    "matplotlib",
    "pytest",
    "IPython",
    "jupyter",
    "setuptools",
    "pip",
]


def run(cmd: list[str], desc: str) -> int:
    print(f"\n>>> {desc}\n    {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=HERE)
    if proc.returncode != 0:
        print(f"!!! 失败（返回码 {proc.returncode}）：{desc}")
    return proc.returncode


def ensure(pkgs: list[str], desc: str) -> bool:
    print(f"\n=== 检查/安装 {desc} ===")
    rc = run([sys.executable, "-m", "pip", "install", "--upgrade"] + pkgs, f"pip install {desc}")
    return rc == 0


def main() -> int:
    onedir = "--onedir" in sys.argv
    print_only = "--print-only" in sys.argv

    if not os.path.exists(os.path.join(HERE, ENTRY)):
        print(f"找不到 {ENTRY}，请把本脚本和源码放在同一目录")
        return 1

    if not print_only:
        if not ensure(REQUIRED, "运行依赖 pymupdf"):
            return 1
        if not ensure(BUILD_REQUIRED, "打包工具 pyinstaller"):
            return 1

    mode = "--onedir" if onedir else "--onefile"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        mode,
        "--windowed",                       # 双击不弹黑框
        "--name", APP_NAME,
        "--hidden-import", "pdfsize_core",
        "--collect-all", "pymupdf",         # 带上 pymupdf 的二进制与数据文件
    ]
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    args.append(ENTRY)

    if print_only:
        print(" ".join(args))
        return 0

    rc = run(args, "PyInstaller 打包")
    if rc != 0:
        return rc

    exe = os.path.join(HERE, "dist", APP_NAME + ".exe")
    if not os.path.exists(exe):
        print(f"!!! 没有找到产物：{exe}")
        return 1
    size_mb = os.path.getsize(exe) / 1024 / 1024
    print(f"\n产物：{exe}  ({size_mb:.1f} MB)")

    # 冒烟测试：真跑一次自检，不能只看文件存不存在
    print("\n=== 冒烟测试（运行 exe --check）===")
    chk = subprocess.run([exe, "--check"], cwd=HERE, capture_output=True, text=True)
    print(chk.stdout.strip() or "(无输出)")
    if chk.returncode != 0:
        print("!!! 打包产物自检失败")
        if chk.stderr.strip():
            print(chk.stderr.strip())
        return 1

    print("\n=== 打包完成 ===")
    print(f"双击运行：dist\\{APP_NAME}.exe")
    print("也可以用命令行：")
    print(f'  "dist\\{APP_NAME}.exe" scan "D:\\某个目录" --csv 报告.csv')
    print(f'  "dist\\{APP_NAME}.exe" convert "D:\\某个目录" --outdir "D:\\输出"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
