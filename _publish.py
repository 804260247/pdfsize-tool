#!/usr/bin/env python3
"""把工作目录以「单个 v0.1 提交」重推到 GitHub（Git Data API）。

- 每次运行都新建无父提交，然后强制移动 main 与 tag v0.1 → 历史始终只有 1 个提交
- 上传 release asset（先删同名旧 asset）
用法: python _publish.py
"""
import base64
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OWNER = "804260247"
REPO = "pdfsize-tool"
BRANCH = "main"
TAG = "v0.2"
HERE = Path("/opt/data/pdf-a4a3-tool")
ZIP = Path("/opt/data/pdfsize-tool-v0.2.zip")
ASSET_NAME = "pdfsize-tool-v0.2.zip"

RELEASE_NAME = "v0.2 · 转换规则预设 / 逐文件目标尺寸 / 按尺寸分类提取"

RELEASE_BODY = """v0.1 之后的第一个正式版本。界面标题栏显示 **v0.2**。

## 下载哪个

| 文件 | 内容 | 适合谁 |
|---|---|---|
| `pdfsize-tool-exe.zip` | `PDF页面尺寸工具.exe`（图形界面）+ `pdfsize-cli.exe`（命令行） | 不想装 Python，解压双击就用 |
| `pdfsize-tool-v0.2.zip` | 完整源码 + 启动/打包脚本 + 测试脚本 + 文档 | 想改代码 / 自己打包 / 想先审计 |

> `pdfsize-tool-exe.zip` 由 GitHub Actions 在 Windows 机器上构建（每次打 tag 自动更新）。
> 未做代码签名，首次运行 SmartScreen 提示「未知发布者」，点「更多信息 → 仍要运行」。
> 如果它还没出现在下面，用源码包 + `打包exe.bat` 自己打一个即可。

## 新增

- **转换规则预设**：标准（竖版近A4→A4、横版近A3→A3）/ 扩展（再加横版近A4→A4横版、竖版近A3→A3竖版）/ 全部统一为 A4竖版·A3横版·A4横版
- **逐文件设定目标尺寸**：点表格「目标尺寸」格子直接下拉，A4/A3/A5/B5/Letter × 竖横、跳过、自动；多选批量设；重扫后规则保留
- **表头排序**：9 列全可点，▲▼ 标示方向，序号列恢复原始顺序
- **按尺寸分类提取**：按判定类别或实际尺寸分类；整文件 / 每页 / 混合尺寸三种输出；**只读，不动原文件**
- **对比预览两侧独立显示比例**：左边 100% 看细节、右边适应窗口看整体；比例不同会提示「不能直接比大小」
- **按钮按状态启用/禁用**：没加载文件时【扫描】是灰的，没扫描时【转换】是灰的
- 表格新增「转换」「状态」列，CSV 新增「目标尺寸」「转换记录」列；命令行新增 `extract`

## 修复

- 转换完成后误弹「请先添加 PDF 文件」（移除一行时把整个父文件夹从输入源里丢掉了）
- 「目标尺寸」下拉要点两次才展开（`<FocusOut>` 让弹层把自己关了）
- 英文 Windows 上命令行输出中文直接崩（`UnicodeEncodeError`）

## 已知限制

- 转换后的页面会丢失该页的**批注 / 表单域**（文本、链接保留）；未命中的页面原样复制，不受影响
- 主要验证平台 Windows 10/11 x64

---

如果这个工具帮到了你，欢迎在仓库 README 末尾扫码支持作者，或在 Issues 留言你需要的其它小工具。
"""

FILES = [
    ".gitignore", "LICENSE", "README.md", "CHANGELOG.md", "requirements.txt",
    "启动.bat", "打包exe.bat",
    "pdfsize_core.py", "pdfsize_gui.py", "build_exe.py", "_publish.py",
    "_selftest.py", "_ui_test.py", "_layout_audit.py", "_shot.py", "_dpi_probe.py",
    "docs/screenshot-main.png", "docs/sponsor-wechat.jpg",
    ".github/workflows/build-exe.yml",
]

COMMIT_MSG = """v0.2: 转换规则预设、逐文件目标尺寸、表头排序、按尺寸分类提取

- 转换规则预设：标准 / 扩展 / 全部统一为 A4竖版·A3横版·A4横版
- 逐文件设定目标尺寸（A4/A3/A5/B5/Letter × 竖横、跳过、自动），多选批量设，重扫后保留
- 表头 9 列排序（▲▼），序号列可恢复原始顺序
- 按尺寸分类提取：按类别 / 按实际尺寸；整文件 / 每页 / 混合尺寸；只读不改原文件
- 转换前后对比预览：左右各自独立的「显示比例」（100% / 适应窗口）
- 按钮按状态启用/禁用，修复转换完成后误弹「请先添加 PDF 文件」
- 修复「目标尺寸」下拉要点两次；修复英文 Windows 命令行输出中文崩溃
- 表格新增转换/状态列，CSV 新增目标尺寸/转换记录列；命令行新增 extract

--- 以下是 v0.1 的内容 ---
v0.1: PDF页面尺寸提取及转换（首个公开版本）

- 逐页读取真实显示尺寸（含 /Rotate），判定 A4竖版 / A3横版 / A4横版 / A3竖版 / 其他
- 竖版近A4 → 标准 A4、横版近A3 → 标准 A3：等比缩放 + 居中留白，不拉伸
- 未命中的页面原样复制（批注/书签/链接保留）；已是目标尺寸的页面跳过
- 文件列表：右键菜单、双击预览、只看需转换、数量统计、可拖动分割条
- 逐文件 / 批量指定目标纸张与方向、跳过
- 转换前后同 DPI 对比预览（纯内存渲染）
- 高 DPI 适配 + 100%~250% 界面缩放并记忆
- CSV 报告、命令行 scan / convert、PyInstaller 打包脚本
"""

TOK = [l.split("=", 1)[1].strip()
       for l in Path("/home/agent/.hermes/.env").read_text(encoding="utf-8").splitlines()
       if l.startswith("GITHUB_TOKEN=")][0]
API = "https://api.github.com"
REPO_PATH = "/repos/%s/%s" % (OWNER, REPO)


def call(method, path, payload=None, raw=None, ctype=None, base=API):
    url = path if path.startswith("http") else base + path
    headers = {"Authorization": "Bearer " + TOK,
               "Accept": "application/vnd.github+json",
               "User-Agent": "pdfsize-tool-publisher"}
    data = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype or "application/octet-stream"
    elif payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = r.read()
            return r.status, (json.loads(body) if body.strip()[:1] in (b"{", b"[") else body)
    except urllib.error.HTTPError as e:
        return e.code, {"message": e.read().decode("utf-8", "replace")[:600]}


def publish_only(paths, msg):
    """只更新指定文件（保留远端其余内容），不动 tag —— 用于发布后的小修"""
    st, head = call("GET", REPO_PATH + "/git/ref/heads/" + BRANCH)
    if st != 200:
        raise SystemExit("读不到分支：%s" % head)
    parent = head["object"]["sha"]
    st, cm = call("GET", REPO_PATH + "/git/commits/" + parent)
    base_tree = cm["tree"]["sha"]
    entries = []
    for name in paths:
        blob = (HERE / name).read_bytes()
        st, r = call("POST", REPO_PATH + "/git/blobs",
                     {"content": base64.b64encode(blob).decode("ascii"), "encoding": "base64"})
        if st != 201:
            raise SystemExit("blob 失败 %s: %s" % (name, r))
        entries.append({"path": name, "mode": "100644", "type": "blob", "sha": r["sha"]})
        print("  blob %-30s %7d B  %s" % (name, len(blob), r["sha"][:10]))
    st, t = call("POST", REPO_PATH + "/git/trees", {"base_tree": base_tree, "tree": entries})
    if st != 201:
        raise SystemExit("tree 失败: %s" % t)
    st, c = call("POST", REPO_PATH + "/git/commits",
                 {"message": msg, "tree": t["sha"], "parents": [parent]})
    if st != 201:
        raise SystemExit("commit 失败: %s" % c)
    print("commit:", c["sha"])
    st, r = call("PATCH", REPO_PATH + "/git/refs/heads/" + BRANCH, {"sha": c["sha"]})
    print("ref ->", st, r.get("message", "ok"))
    st, rt = call("GET", REPO_PATH + "/git/trees/%s?recursive=1" % t["sha"])
    remote = {e["path"]: e["sha"] for e in rt["tree"] if e["type"] == "blob"}
    bad = [e["path"] for e in entries if remote.get(e["path"]) != e["sha"]]
    print("校验：本次更新 %d 个文件，远端不一致 %s" % (len(entries), bad or "无"))
    if bad:
        raise SystemExit(1)
    print("UPDATED", c["sha"])


def main():
    if "--only" in sys.argv:
        i = sys.argv.index("--only")
        msg = "chore: 更新 " + "、".join(sys.argv[i + 1:])
        publish_only(sys.argv[i + 1:], msg)
        return
    tree = []
    for name in FILES:
        blob = (HERE / name).read_bytes()
        st, r = call("POST", REPO_PATH + "/git/blobs",
                     {"content": base64.b64encode(blob).decode("ascii"), "encoding": "base64"})
        if st != 201:
            raise SystemExit("blob 失败 %s: %s" % (name, r))
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": r["sha"]})
        print("  blob %-24s %7d B  %s" % (name, len(blob), r["sha"][:10]))

    st, r = call("POST", REPO_PATH + "/git/trees", {"tree": tree})
    if st != 201:
        raise SystemExit("tree 失败: %s" % r)
    tree_sha = r["sha"]

    st, r = call("POST", REPO_PATH + "/git/commits", {"message": COMMIT_MSG, "tree": tree_sha})
    if st != 201:
        raise SystemExit("commit 失败: %s" % r)
    sha = r["sha"]
    print("commit(orphan):", sha)

    for ref in ("refs/heads/" + BRANCH, "refs/tags/" + TAG):
        st, r = call("PATCH", REPO_PATH + "/git/" + ref, {"sha": sha, "force": True})
        if st in (404, 422):                      # 标签还没建过，先创建
            st, r = call("POST", REPO_PATH + "/git/refs", {"ref": ref, "sha": sha})
            print("  %-18s -> 新建 %s %s" % (ref, st, r.get("message", "ok")))
        else:
            print("  %-18s -> %s %s" % (ref, st, r.get("message", "ok")))

    if "--no-asset" in sys.argv:
        print("  跳过 asset 上传（--no-asset）")
        _verify(tree, tree_sha, sha)
        return

    # release asset：删旧传新（release 不存在则先创建）
    st, rels = call("GET", REPO_PATH + "/releases/tags/" + TAG)
    if st == 404:
        st, rels = call("POST", REPO_PATH + "/releases", {
            "tag_name": TAG, "name": RELEASE_NAME, "body": RELEASE_BODY,
            "draft": False, "prerelease": False})
        print("  创建 release:", st, rels.get("html_url") or rels.get("message"))
        if st not in (200, 201):
            raise SystemExit("release 创建失败: %s" % rels)
    rid = rels["id"]
    st, assets = call("GET", "%s/releases/%d/assets" % (REPO_PATH, rid))
    for a in assets:
        if a["name"] != ASSET_NAME:      # 别动 exe 包（那是 Actions 产的）
            print("  保留其它 asset:", a["name"], a["size"])
            continue
        print("  删除同名旧 asset:", a["name"],
              call("DELETE", "%s/releases/assets/%d" % (REPO_PATH, a["id"]))[0])
    q = urllib.parse.quote(ASSET_NAME)
    st, a = call("POST", "%s/releases/%d/assets?name=%s" % (REPO_PATH, rid, q),
                 raw=ZIP.read_bytes(), ctype="application/zip",
                 base="https://uploads.github.com")
    print("  上传 asset:", st, a.get("name"), a.get("size"), a.get("state"), a.get("message", ""))

    _verify(tree, tree_sha, sha)


def _verify(tree, tree_sha, sha):
    # 回读校验
    st, t = call("GET", REPO_PATH + "/git/trees/%s?recursive=1" % tree_sha)
    remote = {e["path"]: e["sha"] for e in t["tree"] if e["type"] == "blob"}
    if tree is None:                      # --only 模式：比对远端 tree 与本地文件
        bad = [e["path"] for e in t["tree"]
               if e["type"] == "blob" and (HERE / e["path"]).is_file()
               and (HERE / e["path"]).read_bytes()
               and hashlib.sha1(b"blob %d\0" % len((HERE / e["path"]).read_bytes())
                                + (HERE / e["path"]).read_bytes()).hexdigest() != e["sha"]]
        print("校验：远端 %d 文件，与本地不一致 %s" % (len(remote), bad or "无"))
    else:
        bad = [e["path"] for e in tree if remote.get(e["path"]) != e["sha"]]
        print("校验：远端 %d 文件，不一致 %s" % (len(remote), bad or "无"))
    if bad:
        raise SystemExit(1)
    print("PUBLISHED", sha)


if __name__ == "__main__":
    main()
