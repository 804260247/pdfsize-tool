#!/usr/bin/env python3
"""把工作目录以「单个 v0.1 提交」重推到 GitHub（Git Data API）。

- 每次运行都新建无父提交，然后强制移动 main 与 tag v0.1 → 历史始终只有 1 个提交
- 上传 release asset（先删同名旧 asset）
用法: python _publish.py
"""
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OWNER = "804260247"
REPO = "pdfsize-tool"
BRANCH = "main"
TAG = "v0.1"
HERE = Path("/opt/data/pdf-a4a3-tool")
ZIP = Path("/opt/data/PDF页面尺寸工具-v0.1.zip")
ASSET_NAME = "pdfsize-tool-v0.1.zip"

FILES = [
    ".gitignore", "LICENSE", "README.md", "requirements.txt", "启动.bat", "打包exe.bat",
    "pdfsize_core.py", "pdfsize_gui.py", "build_exe.py", "_publish.py",
    "_selftest.py", "_ui_test.py", "_layout_audit.py", "_shot.py", "_dpi_probe.py",
    "docs/screenshot-main.png",
    ".github/workflows/build-exe.yml",
]

COMMIT_MSG = """v0.1: PDF页面尺寸提取及转换（首个公开版本）

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


def main():
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
        print("  %-18s -> %s %s" % (ref, st, r.get("message", "ok")))

    if "--no-asset" in sys.argv:
        print("  跳过 asset 上传（--no-asset）")
        _verify(tree, tree_sha)
        return

    # release asset：删旧传新
    st, rels = call("GET", REPO_PATH + "/releases/tags/" + TAG)
    rid = rels["id"]
    st, assets = call("GET", "%s/releases/%d/assets" % (REPO_PATH, rid))
    for a in assets:
        print("  删除旧 asset:", a["name"],
              call("DELETE", "%s/releases/assets/%d" % (REPO_PATH, a["id"]))[0])
    q = urllib.parse.quote(ASSET_NAME)
    st, a = call("POST", "%s/releases/%d/assets?name=%s" % (REPO_PATH, rid, q),
                 raw=ZIP.read_bytes(), ctype="application/zip",
                 base="https://uploads.github.com")
    print("  上传 asset:", st, a.get("name"), a.get("size"), a.get("state"), a.get("message", ""))

    _verify(tree, tree_sha)


def _verify(tree, tree_sha):
    # 回读校验
    st, t = call("GET", REPO_PATH + "/git/trees/%s?recursive=1" % tree_sha)
    remote = {e["path"]: e["sha"] for e in t["tree"] if e["type"] == "blob"}
    bad = [e["path"] for e in tree if remote.get(e["path"]) != e["sha"]]
    print("校验：远端 %d 文件，不一致 %s" % (len(remote), bad or "无"))
    if bad:
        raise SystemExit(1)
    print("PUBLISHED", sha)


if __name__ == "__main__":
    main()
