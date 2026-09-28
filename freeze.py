#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
版本固化脚本 —— 把当前工程快照成 out/vN/，并自动追加 CHANGELOG.md。

设计规则（按用户要求）：
  * 每版成品固化到独立目录 out/v1/、out/v2/ ……，**已存在的版本目录拒绝覆盖**。
  * 一个 markdown 变更记录 out/CHANGELOG.md，由本脚本自动追加，不靠人记性。
  * out/versions.json 为机器可读索引，供脚本自身做版本递增与防覆盖。
  * 旧版本永不删除。

用法：
    python freeze.py "修复状态探测" "新增台词气泡"
    python freeze.py -b v1 "基于 v1 调整了大小预设"
    python freeze.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "out")
INDEX_PATH = os.path.join(OUT_DIR, "versions.json")
CHANGELOG_PATH = os.path.join(OUT_DIR, "CHANGELOG.md")

# 不进入版本快照的内容
EXCLUDE_DIRS = {"out", "logs", "run", "tmp", "__pycache__", ".trash", ".git", ".venv"}
EXCLUDE_FILES = {"pet_config.json"}  # 含个人位置偏好，不固化
EXCLUDE_EXT = (".pyc", ".pyo")


def load_index() -> dict:
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"current": 0, "versions": []}


def save_index(idx: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def snapshot(dest: str) -> list[str]:
    """把工程复制到 dest，返回复制进去的相对路径列表。"""
    copied: list[str] = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, BASE_DIR)
        if rel_root == ".":
            rel_root = ""
        for name in files:
            if name in EXCLUDE_FILES or name.endswith(EXCLUDE_EXT):
                continue
            src = os.path.join(root, name)
            rel = os.path.join(rel_root, name) if rel_root else name
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)
    return sorted(copied)


def append_changelog(version: str, based_on: str, notes: list[str],
                     files: list[str]) -> None:
    lines = []
    if not os.path.exists(CHANGELOG_PATH):
        lines.append("# 变更记录\n")
        lines.append("本文件由 `freeze.py` 自动追加，**请勿手改**。\n")
    lines.append("")
    lines.append(f"## {version}")
    lines.append("")
    lines.append(f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 基于：{based_on}")
    lines.append("- 本版改动：")
    for n in notes:
        lines.append(f"  - {n}")
    lines.append(f"- 成品路径：`out/{version}/`")
    lines.append(f"- 包含文件：{len(files)} 个")
    lines.append("")
    with open(CHANGELOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description="工程版本固化")
    ap.add_argument("notes", nargs="*", help="本版改动说明（可多条）")
    ap.add_argument("-b", "--based-on", default="", help="基于哪一版，默认上一版")
    ap.add_argument("--list", action="store_true", help="列出现有版本")
    args = ap.parse_args()

    idx = load_index()

    if args.list:
        print(f"current = v{idx['current']}")
        for v in idx["versions"]:
            print(f"  {v['version']:>4}  {v['time']}  基于 {v['based_on']}")
            for n in v["notes"]:
                print(f"          - {n}")
        return 0

    if not args.notes:
        print("错误：至少要写一条本版改动说明。", file=sys.stderr)
        print('例：python freeze.py "首次可用版本"', file=sys.stderr)
        return 2

    nxt = idx["current"] + 1
    version = f"v{nxt}"
    dest = os.path.join(OUT_DIR, version)

    if os.path.exists(dest):
        print(f"错误：{dest} 已存在，拒绝覆盖旧版本。", file=sys.stderr)
        print("如需新版本请先修正 out/versions.json 的 current。", file=sys.stderr)
        return 1

    based_on = args.based_on or (f"v{idx['current']}" if idx["current"] else "（首版）")

    os.makedirs(dest, exist_ok=True)
    files = snapshot(dest)

    idx["current"] = nxt
    idx["versions"].append({
        "version": version,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "based_on": based_on,
        "notes": args.notes,
        "path": f"out/{version}",
        "files": len(files),
    })
    save_index(idx)
    append_changelog(version, based_on, args.notes, files)

    print(f"已固化 {version} -> out/{version}/  （{len(files)} 个文件）")
    print(f"变更记录：out/CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
