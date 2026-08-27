#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/maxkb_sync.py —— 把「水云间」知识库同步到 MaxKB 数据集（RAG 摄取）。

设计原则
--------
- **零第三方依赖**：只用标准库（urllib / json / uuid / mimetypes），可直接在任意 Python 3 环境跑，
  不需要 pip install requests，也不污染打包进 exe 的依赖。
- **配置化、可切换版本**：MaxKB 不同版本（社区版 / 专业版 / V2）的文档写入接口路径不一致，
  本脚本把上传接口做成「完全可覆盖」的环境变量，默认走社区/数据集接口。
- **先 dry-run 再 push**：默认只列出将上传的文档，确认无误后加 `--push` 才真的写。
- **单文件上传 + 服务端分段**：每个 .md 作为一个文档上传，由 MaxKB 服务端自动分段 / 向量化。

用法
----
  # 1) 仅预览（默认）：列出 case-studies + 各 agent cases 里会被上传的文档
  python tools/maxkb_sync.py

  # 2) 实际上传
  MAXKB_BASE_URL=http://10.1.11.58:8080 \
  MAXKB_API_KEY=user-xxxxxxxxxxxxxxxx \
  MAXKB_DATASET_ID=3d1d5d4e-5576-11f0-bc5c-0242ac120003 \
  python tools/maxkb_sync.py --push

  # 3) 自定义上传接口（当你的 MaxKB 版本路径不同）
  MAXKB_UPLOAD_URL=http://HOST:8080/admin/api/workspace/WSID/knowledge/KBID/document/_bach \
  python tools/maxkb_sync.py --push

环境变量
--------
  MAXKB_BASE_URL      必填（--push）。MaxKB 服务地址，如 http://10.1.11.58:8080
  MAXKB_API_KEY       必填（--push）。API Key，形如 user-xxxx，放在 AUTHORIZATION 请求头
  MAXKB_DATASET_ID    必填（--push，且未用 MAXKB_UPLOAD_URL 覆盖时）。目标数据集 ID
  MAXKB_UPLOAD_URL    可选。完整上传接口 URL；设置后忽略 BASE_URL/DATASET_ID 的默认拼接
  MAXKB_NAME_PREFIX   可选。上传文档名前缀，默认 "水云间-"
  MAXKB_KB_DIR        可选。知识库根目录，默认自动定位到本仓库的 knowledge-base/
  MAXKB_INCLUDE_INDEX 可选。设为 1 时把 knowledge-index.json 也作为一个文档上传（默认不上传）

接口说明（默认路径）
--------------------
  默认：POST {BASE_URL}/api/dataset/{DATASET_ID}/document/_bach
        multipart/form-data: file=@xxx.md
        认证：AUTHORIZATION: {API_KEY}
  这是数据集级单文件上传（服务端自动分段）。若你的版本是 workspace 路径
  （/admin/api/workspace/{ws}/knowledge/{kb}/document/split → batch_create），
  请用 MAXKB_UPLOAD_URL 指向 split 之后的 batch_create 接口，或在下方
  _upload_one() 里按你的版本改两行即可。
"""

import os
import sys
import json
import uuid
import mimetypes
import urllib.request
import urllib.error

# ---------- 定位知识库根目录 ----------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_KB = os.path.join(REPO_ROOT, "knowledge-base")


def _collect_docs(kb_dir, include_index):
    """收集所有待上传文档，返回 [(display_name, abs_path), ...]"""
    docs = []
    # 1) case-studies/*.md（排除 readme）
    cases = os.path.join(kb_dir, "case-studies")
    if os.path.isdir(cases):
        for fn in sorted(os.listdir(cases)):
            if fn.lower().endswith(".md") and fn.lower() != "readme.md":
                docs.append(("案例/" + fn, os.path.join(cases, fn)))
    # 2) agents/*/cases/*.md（排除 readme）
    agents_dir = os.path.join(REPO_ROOT, "agents")
    if os.path.isdir(agents_dir):
        for ag in sorted(os.listdir(agents_dir)):
            ad = os.path.join(agents_dir, ag, "cases")
            if os.path.isdir(ad):
                for fn in sorted(os.listdir(ad)):
                    if fn.lower().endswith(".md") and fn.lower() != "readme.md":
                        docs.append(("智能体/%s/%s" % (ag, fn), os.path.join(ad, fn)))
    # 3) 可选：knowledge-index.json
    if include_index:
        idx = os.path.join(kb_dir, "knowledge-index.json")
        if os.path.isfile(idx):
            docs.append(("索引/knowledge-index.json", idx))
    return docs


def _multipart(body_fields, files):
    """构造 multipart/form-data 请求体。
    body_fields: {name: value}   files: {name: (filename, data_bytes, content_type)}"""
    boundary = "----maxkb" + uuid.uuid4().hex
    chunks = []
    for name, val in body_fields.items():
        chunks.append(("--" + boundary).encode("utf-8"))
        chunks.append(('Content-Disposition: form-data; name="%s"\r\n\r\n%s'
                       % (name, val)).encode("utf-8"))
    for name, (fname, data, ctype) in files.items():
        chunks.append(("--" + boundary).encode("utf-8"))
        chunks.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                       'Content-Type: %s\r\n\r\n' % (name, fname, ctype)).encode("utf-8"))
        chunks.append(data)
    chunks.append(("--" + boundary + "--\r\n").encode("utf-8"))
    body = b"\r\n".join(chunks)
    ctype = "multipart/form-data; boundary=" + boundary
    return body, ctype


def _upload_one(url, api_key, display_name, path, name_prefix):
    with open(path, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    fname = name_prefix + os.path.basename(path)
    body, mt = _multipart({}, {"file": (fname, data, ctype)})
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", mt)
    req.add_header("AUTHORIZATION", api_key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = r.read().decode("utf-8", "ignore")
            try:
                return True, json.loads(resp)
            except Exception:
                return True, {"raw": resp[:200]}
    except urllib.error.HTTPError as e:
        return False, {"error": "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "ignore")[:300])}
    except Exception as e:
        return False, {"error": str(e)}


def main():
    push = "--push" in sys.argv
    kb_dir = os.environ.get("MAXKB_KB_DIR") or DEFAULT_KB
    include_index = os.environ.get("MAXKB_INCLUDE_INDEX") == "1"
    name_prefix = os.environ.get("MAXKB_NAME_PREFIX") or "水云间-"

    docs = _collect_docs(kb_dir, include_index)
    print("知识库根目录: %s" % kb_dir)
    print("将上传文档数: %d%s" % (len(docs), "（含 knowledge-index.json）" if include_index else ""))
    print("-" * 60)
    for disp, p in docs:
        print("  [+] %s  (%d bytes)" % (disp, os.path.getsize(p)))

    if not push:
        print("-" * 60)
        print("预览模式（未实际上传）。确认无误后设置环境变量并执行：")
        print("  MAXKB_BASE_URL=... MAXKB_API_KEY=... MAXKB_DATASET_ID=... \\")
        print("  python tools/maxkb_sync.py --push")
        return

    base = os.environ.get("MAXKB_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("MAXKB_API_KEY", "").strip()
    ds_id = os.environ.get("MAXKB_DATASET_ID", "").strip()
    upload_url = os.environ.get("MAXKB_UPLOAD_URL", "").strip()

    if not api_key:
        print("❌ 缺少 MAXKB_API_KEY（API Key）", file=sys.stderr)
        sys.exit(2)
    if upload_url:
        url = upload_url
    else:
        if not base or not ds_id:
            print("❌ 未设置 MAXKB_UPLOAD_URL，则需同时设置 MAXKB_BASE_URL 与 MAXKB_DATASET_ID",
                  file=sys.stderr)
            sys.exit(2)
        url = "%s/api/dataset/%s/document/_bach" % (base, ds_id)

    print("-" * 60)
    print("上传接口: %s" % url)
    ok = 0
    fail = 0
    for disp, p in docs:
        good, res = _upload_one(url, api_key, disp, p, name_prefix)
        if good:
            ok += 1
            print("  ✅ %s" % disp)
        else:
            fail += 1
            print("  ❌ %s -> %s" % (disp, res.get("error", res)))
    print("-" * 60)
    print("完成：成功 %d / 失败 %d" % (ok, fail))
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
