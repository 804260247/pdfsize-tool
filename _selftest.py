# -*- coding: utf-8 -*-
"""生成测试 PDF 并验证 pdfsize_core 的检测 / 转换结果"""
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymupdf
import pdfsize_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, "_test", "in")
OUT = os.path.join(HERE, "_test", "out")


def make(path, specs):
    """specs: [(宽mm, 高mm, 文字), ...]"""
    doc = pymupdf.open()
    for w_mm, h_mm, text in specs:
        p = doc.new_page(width=core.mm_to_pt(w_mm), height=core.mm_to_pt(h_mm))
        p.insert_text((30, 60), text, fontsize=18)
        # 画一个占满整页的边框，便于肉眼确认缩放效果
        p.draw_rect(pymupdf.Rect(10, 10, p.rect.width - 10, p.rect.height - 10),
                    color=(0, 0, 1), width=2)
        p.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(30, 80, 200, 110),
                       "uri": "https://example.com"})
    doc.save(path)
    doc.close()
    return path


def main():
    shutil.rmtree(os.path.join(HERE, "_test"), ignore_errors=True)
    os.makedirs(IN, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    make(os.path.join(IN, "01_a4_标准.pdf"), [(210, 297, "A4 exact P1"), (210, 297, "A4 exact P2")])
    make(os.path.join(IN, "02_a4_近_208x295.pdf"), [(208, 295, "near A4 208x295")])
    make(os.path.join(IN, "03_a3横_近_415x292.pdf"), [(415, 292, "near A3 landscape 415x292")])
    make(os.path.join(IN, "04_a4横.pdf"), [(297, 210, "A4 landscape (should NOT change)")])
    make(os.path.join(IN, "05_letter.pdf"), [(215.9, 279.4, "US Letter (should NOT change)")])
    make(os.path.join(IN, "06_a5.pdf"), [(148, 210, "A5 (should NOT change)")])
    make(os.path.join(IN, "07_mixed.pdf"), [
        (210, 297, "A4 portrait P1"),
        (413, 291, "A3-ish landscape P2"),
        (215.9, 279.4, "Letter P3"),
        (207, 294, "near A4 P4"),
        (148, 210, "A5 P5"),
    ])
    with open(os.path.join(IN, "08_坏文件.pdf"), "wb") as fh:
        fh.write(b"not a real pdf at all")

    print("=" * 78)
    print("扫描结果")
    print("=" * 78)
    infos = core.scan_paths([IN])
    for fi in infos:
        if fi.ok:
            print(core.describe_pages(fi))
        else:
            print(f"{fi.name}  ❌ {fi.error}")
        print()

    print("=" * 78)
    print("转换（覆盖模式 + 备份）")
    print("=" * 78)
    results = core.convert_paths([IN], tol=core.DEFAULT_TOL, backup=True)
    for r in results:
        tag = "✅" if r.ok and r.converted else ("－" if r.ok else "❌")
        print(f"{tag} {os.path.basename(r.path):28} 转换 {r.converted}/{r.total} 页"
              f" {r.skipped_reason or r.error}")

    print()
    print("=" * 78)
    print("转换后复检")
    print("=" * 78)
    expect = {
        "01_a4_标准.pdf": None,                # 已是标准，不应重写
        "02_a4_近_208x295.pdf": "A4竖版",
        "03_a3横_近_415x292.pdf": "A3横版",
        "04_a4横.pdf": None,
        "05_letter.pdf": None,
        "06_a5.pdf": None,
        "07_mixed.pdf": None,
    }
    bad = 0
    for fi in core.scan_paths([IN]):
        if not fi.ok:
            print(f"{fi.name}  ❌ {fi.error}")
            continue
        print(core.describe_pages(fi))
        for p in fi.pages:
            if p.kind == core.K_A4P and (abs(p.width_mm - 210) > 0.3 or abs(p.height_mm - 297) > 0.3):
                print("   !! A4 尺寸不正确"); bad += 1
            if p.kind == core.K_A3L and (abs(p.width_mm - 420) > 0.3 or abs(p.height_mm - 297) > 0.3):
                print("   !! A3 尺寸不正确"); bad += 1
        print()

    # 检查 bak 文件存在
    baks = [f for f in os.listdir(IN) if f.endswith(".bak")]
    print("生成的备份文件:", baks)

    # 输出到新目录模式
    print()
    print("=" * 78)
    print("输出到新目录模式")
    print("=" * 78)
    src = os.path.join(IN, "07_mixed.pdf")
    r = core.convert_file(src, out_path=os.path.join(OUT, "07_mixed.pdf"))
    print(f"converted={r.converted} out={r.out_path} ok={r.ok} err={r.error}")
    fi = core.scan_file(r.out_path)
    print(core.describe_pages(fi))

    # 按尺寸分类提取
    print()
    print("=" * 78)
    print("按尺寸分类提取")
    print("=" * 78)
    xout = os.path.join(HERE, "_test", "extract")
    for split in (core.EXTRACT_SPLIT_FILE, core.EXTRACT_SPLIT_PAGE, core.EXTRACT_SPLIT_COPY):
        d = os.path.join(xout, split)
        rs = core.extract_paths([IN], d, split=split)
        okc = sum(1 for r in rs if r.ok)
        made = sum(len(r.outputs) for r in rs)
        subs = sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x)))
        print(f"[{split}] 成功 {okc}/{len(rs)}，生成 {made} 个 PDF，子文件夹 {subs}")
        if split == core.EXTRACT_SPLIT_COPY:
            print("         （copy 模式整份复制，不拆分，尺寸不统一的进「混合尺寸」）")
    # 页数守恒校验：按类别拆分后，每类页数合计必须等于源文件的分类页数
    infos_x = core.scan_paths([IN])
    want = {}
    for fi in infos_x:
        if fi.ok:
            for p in fi.pages:
                want[p.kind] = want.get(p.kind, 0) + 1
    got = {}
    base = os.path.join(xout, core.EXTRACT_SPLIT_FILE)
    for name in os.listdir(base):
        sub = os.path.join(base, name)
        if os.path.isdir(sub):
            got[name] = sum(core.scan_file(os.path.join(sub, f)).page_count
                            for f in os.listdir(sub) if f.lower().endswith(".pdf"))
    print("源文件各类页数:", want)
    print("提取后各类页数:", got)
    if got != want:
        print("   !! 提取页数不守恒"); bad += 1
    # 源文件没被改动
    print("源目录文件数:", len(os.listdir(IN)))

    print()
    print("CSV 报告:", core.write_report_csv(core.scan_paths([IN]), os.path.join(HERE, "_test", "report.csv")))
    print("\nBAD =", bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
