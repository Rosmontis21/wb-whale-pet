#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wb-whale-pet 的 MCP 宿主（stdio 传输）—— 让桌宠像插件一样跟着 WorkBuddy 启停。

原理
----
把本脚本注册进 ~/.workbuddy/mcp.json 后：
  * WorkBuddy 启动会拉起本进程（stdio 子进程）
  * 本进程启动时把桌宠（wb_pet.py）拉起来
  * WorkBuddy 退出 → 本进程的 stdin 收到 EOF → 收掉桌宠 → 自己退出

所以桌宠的生命周期就绑在 WorkBuddy 上：开 WorkBuddy 她就来，关 WorkBuddy 她就走。

⚠️ 纪律：**stdout 是 MCP 协议通道，绝不能往里打日志**。
   所有日志走 stderr 或 logs/mcp-host.log。

协议：JSON-RPC 2.0，stdio 传输为「一行一条 JSON」（newline-delimited）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PET_SCRIPT = os.path.join(BASE_DIR, "wb_pet.py")
PID_FILE = os.path.join(BASE_DIR, "run", "pet.pid")
LOG_FILE = os.path.join(BASE_DIR, "logs", "mcp-host.log")

SERVER_NAME = "wb-whale-pet"
SERVER_VERSION = "1.0.0"

# 支持的协议版本，按客户端请求回落到其中之一
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_verbose = os.environ.get("WB_PET_MCP_DEBUG") == "1"


def log(msg: str) -> None:
    """日志只写文件与 stderr，绝不碰 stdout。"""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if _verbose:
        print(line, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 桌宠进程管理


def pythonw() -> str:
    """优先用与当前解释器同目录的 pythonw.exe（无控制台窗口）。"""
    d = os.path.dirname(sys.executable or "")
    for name in ("pythonw.exe", "pythonw"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return sys.executable or "pythonw.exe"


def read_pid() -> int | None:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(pid) in (out.stdout or "")
    except Exception:
        return False


def pet_running() -> bool:
    pid = read_pid()
    return bool(pid and pid_alive(pid))


_PET_PROC: "subprocess.Popen | None" = None

# ---------------------------------------------------------------- 拉起时机控制
#
# ⚠️ 血泪教训：**不要在进程一启动就拉桌宠。**
# WorkBuddy 在「检查/探测连接器」时也会 spawn 本 server，clientInfo.name 形如
# `workbuddy-mcp-probe:custom-mcp:wb-whale-pet`：连上、握手、**同一秒断开**。
# 若在 initialize 里直接拉桌宠，用户看到的就是「鲸鱼闪一下就没了」。
#
# 对策两条（双保险）：
#   1) 探针客户端（名字含 probe）直接跳过，不碰桌宠；
#   2) 其余客户端延迟 START_DELAY 秒再拉；这期间若 stdin 提前 EOF，
#      收尾时会取消定时器，桌宠就永远不会被拉起来。
START_DELAY = float(os.environ.get("WB_PET_MCP_DELAY", "2.5"))

_start_timer: "threading.Timer | None" = None
_we_touched_pet = False      # 只有确实安排过拉起，收尾才有资格清理


def schedule_start(client_name: str) -> None:
    """按客户端类型决定何时（是否）拉起桌宠。"""
    global _start_timer, _we_touched_pet
    if "probe" in (client_name or "").lower():
        log(f"探针客户端（{client_name}），跳过拉起桌宠")
        return
    if _start_timer is not None:
        return
    _we_touched_pet = True
    _start_timer = threading.Timer(START_DELAY, start_pet)
    _start_timer.daemon = True
    _start_timer.start()
    log(f"已安排 {START_DELAY}s 后拉起桌宠（client={client_name}）")


def cancel_start() -> None:
    """stdin 提前关闭时，撤销还没执行的拉起。"""
    global _start_timer
    if _start_timer is not None:
        _start_timer.cancel()
        _start_timer = None
        log(f"已取消待执行的桌宠拉起（存活不足 {START_DELAY}s，多半是探针）")


def start_pet() -> bool:
    """拉起桌宠；已在跑就不重复启动。"""
    global _PET_PROC
    if pet_running():
        log("桌宠已在运行，跳过启动")
        return True
    if not os.path.exists(PET_SCRIPT):
        log(f"找不到 {PET_SCRIPT}")
        return False
    try:
        os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
        creation = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation |= subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creation |= subprocess.DETACHED_PROCESS
        # --tie-to-workbuddy：让桌宠自己也盯着 WorkBuddy。
        # 这样即使宿主异常消失（来不及收尾），桌宠也会自己退出，不会变成孤儿。
        _PET_PROC = subprocess.Popen(
            [pythonw(), PET_SCRIPT, "--tie-to-workbuddy"],
            cwd=BASE_DIR,
            creationflags=creation,
            close_fds=True,
        )
        log(f"已拉起桌宠: pid={_PET_PROC.pid}")
        return True
    except Exception as e:
        log(f"拉起桌宠失败: {type(e).__name__}: {e}")
        return False


def stop_pet() -> None:
    """
    结束桌宠。两条路都走，确保不留孤儿：
      1) 本宿主 spawn 的句柄（桌宠启动慢时 PID 文件还没写，只能靠它）
      2) run/pet.pid

    但第 2 条只有在**本实例确实参与过拉起**时才做——否则探针实例收尾时会把
    另一个「真实客户端」拉起来的桌宠误杀掉。
    """
    global _PET_PROC, _we_touched_pet

    if _PET_PROC is not None and _PET_PROC.poll() is None:
        try:
            _PET_PROC.terminate()
            _PET_PROC.wait(timeout=5)
            log(f"已结束桌宠（句柄 pid={_PET_PROC.pid}）")
        except Exception:
            try:
                _PET_PROC.kill()
                log(f"已强杀桌宠（句柄 pid={_PET_PROC.pid}）")
            except Exception as e:
                log(f"强杀桌宠失败: {type(e).__name__}: {e}")
        _PET_PROC = None
        _we_touched_pet = False
        return

    if not _we_touched_pet:
        log("本实例未参与拉起，收尾不触碰桌宠")
        return

    pid = read_pid()
    if pid and pid_alive(pid):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log(f"已结束桌宠 PID={pid}")
        except Exception as e:
            log(f"结束桌宠失败: {type(e).__name__}: {e}")
    elif not pid:
        log("收尾：无 PID 文件")

    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------- 状态查询


def pet_status() -> str:
    """给 agent 看的桌宠状态（也是本 server 唯一的工具）。"""
    import datetime
    pid = read_pid()
    alive = bool(pid and pid_alive(pid))
    if not alive:
        return "桌宠当前未在运行。"
    lines = [f"桌宠正在运行（PID {pid}）。"]

    db = os.path.join(os.path.expanduser("~"), ".workbuddy", "workbuddy.db")
    if os.path.exists(db):
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
            try:
                cur = con.cursor()
                n = cur.execute(
                    "SELECT COUNT(*) FROM sessions "
                    "WHERE deleted_at IS NULL AND status='working'"
                ).fetchone()[0]
                last = cur.execute(
                    "SELECT MAX(COALESCE(updated_at,last_activity_at,created_at)) "
                    "FROM sessions WHERE deleted_at IS NULL"
                ).fetchone()[0]
            finally:
                con.close()
            if last:
                age = time.time() - last / 1000.0
                lines.append(f"WorkBuddy 会话：{n} 个运行中；最后活动 {age:.0f} 秒前。")
                if n > 0:
                    st = "干活中" if age <= 600 else "思考中（可能卡住）"
                elif age < 25:
                    st = "刚完成"
                elif age < 300:
                    st = "待机"
                elif age < 900:
                    st = "犯困"
                else:
                    st = "睡着了"
                lines.append(f"她现在的表情：{st}。")
        except Exception as e:
            lines.append(f"（读取状态失败：{type(e).__name__}）")
    return "\n".join(lines)


TOOLS = [
    {
        "name": "whale_pet_status",
        "description": (
            "查询桌面鲸鱼桌宠的运行状态与当前表情。"
            "桌宠会跟随 WorkBuddy 的工作状态变化（干活中/待机/犯困/睡着了等）。"
            "当用户问起'桌宠在干嘛''鲸鱼现在什么状态'时使用。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
]


# ---------------------------------------------------------------- JSON-RPC


def send(obj: dict) -> None:
    """往 stdout 写一条 JSON-RPC 消息（一行）。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(req_id, result=None, error=None) -> None:
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


def handle(req: dict) -> None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    # 通知（无 id）不需要响应
    is_notification = "id" not in req

    if method == "initialize":
        want = (params.get("protocolVersion") or "").strip()
        proto = want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[-1]
        reply(req_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        info = params.get("clientInfo") or {}
        log(f"initialize ok (client={info}, proto={proto})")
        schedule_start(info.get("name") or "")
        return

    if method in ("notifications/initialized", "initialized"):
        log("client initialized")
        return

    if method == "ping":
        reply(req_id, {})
        return

    if method == "tools/list":
        reply(req_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        if name == "whale_pet_status":
            try:
                text = pet_status()
            except Exception as e:
                text = f"查询失败：{type(e).__name__}: {e}"
            reply(req_id, {"content": [{"type": "text", "text": text}],
                           "isError": False})
        else:
            reply(req_id, {
                "content": [{"type": "text", "text": f"未知工具：{name}"}],
                "isError": True,
            })
        return

    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        reply(req_id, {key: []})
        return

    if is_notification:
        return

    reply(req_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> int:
    log(f"=== MCP 宿主启动 (pid={os.getpid()}, exe={sys.executable}) ===")
    # 不在这里拉桌宠：改为 initialize 之后按客户端类型决定（避开"探针"），见 schedule_start()

    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except Exception:
                log(f"非法 JSON（忽略）: {raw[:200]}")
                continue
            try:
                handle(req)
            except Exception:
                log("处理请求异常:\n" + traceback.format_exc())
                if isinstance(req, dict) and "id" in req:
                    reply(req["id"], error={"code": -32603, "message": "internal error"})
    except Exception:
        log("stdin 读取异常:\n" + traceback.format_exc())
    finally:
        # WorkBuddy 退出（stdin EOF）或宿主被杀 → 收掉桌宠
        log("stdin 关闭，开始收尾")
        cancel_start()
        stop_pet()
        log("=== MCP 宿主退出 ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("致命异常:\n" + traceback.format_exc())
        sys.exit(1)
