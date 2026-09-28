#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
借 WorkBuddy 自己的登录态查询余额（走 Chrome DevTools Protocol）。

为什么需要它
------------
实测结论：WorkBuddy 桌面端的登录态**不落地在任何可读位置**——
7 个 cookie 库里 `www.workbuddy.cn` 域下只有一条 `tgw_l7_route`（路由 cookie，非登录态），
localStorage/leveldb 里没有 token，也没有明文凭证文件。
但它界面明明能显示积分，说明鉴权存在于**运行时**。

于是换个思路：不复制它的凭证，而是**让 WorkBuddy 自己去发这个请求**。
给 WorkBuddy 设上环境变量 `WORKBUDDY_REMOTE_DEBUGGING_PORT`（在它的 app.asar 里搜到过这个名字），
渲染进程就会开一个**本地**调试端口。我们连上去，在页面上下文里执行 `fetch`，
cookie / header / 鉴权全都自动带对，而且**不存在过期问题**。

⚠️ 副作用：会在 127.0.0.1 上开一个调试端口。仅本机可连。

只用标准库（手写 websocket），不引入第三方依赖。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.request
from urllib.parse import urlparse

DEFAULT_PORT = int(os.environ.get("WORKBUDDY_REMOTE_DEBUGGING_PORT", "9222"))

POINTS_URL = "https://www.workbuddy.cn/billing/meter/get-user-resource-summary"

# 在页面里执行：调余额接口，把结果回传成字符串
FETCH_JS = """
(async () => {
  try {
    const r = await fetch(%s, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: '{}',
      credentials: 'include'
    });
    const t = await r.text();
    return JSON.stringify({status: r.status, body: t.slice(0, 4000)});
  } catch (e) {
    return JSON.stringify({error: String(e)});
  }
})()
""" % json.dumps(POINTS_URL)


class CdpError(RuntimeError):
    pass


# ---------------------------------------------------------------- websocket


class WebSocket:
    """够用就好的 websocket 客户端：握手 + 发文本帧 + 收帧。"""

    def __init__(self, url: str, timeout: float = 12.0):
        u = urlparse(url)
        if u.scheme not in ("ws", "wss"):
            raise CdpError(f"不支持的 websocket 地址: {url}")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {u.hostname}:{u.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("websocket 握手时连接被关闭")
            buf += chunk
        head = buf.split(b"\r\n", 1)[0].decode("latin1", "ignore")
        if "101" not in head:
            raise CdpError(f"websocket 握手失败: {head}")

    def _recv_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise CdpError("连接已关闭")
            out += chunk
        return out

    def send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        header = bytearray([0x81])                    # FIN + opcode=text
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(
            b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_text(self) -> str | None:
        """收一条完整文本消息；返回 None 表示连接正常关闭。"""
        payload = b""
        while True:
            h = self._recv_exact(2)
            opcode = h[0] & 0x0F
            masked = bool(h[1] & 0x80)
            n = h[1] & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            data = self._recv_exact(n) if n else b""
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

            if opcode == 0x8:            # close
                return None
            if opcode == 0x9:            # ping -> pong（简化：忽略）
                continue
            if opcode in (0x1, 0x0):     # text / continuation
                payload += data
                return payload.decode("utf-8", "ignore")

    def call(self, method: str, params: dict, msg_id: int = 1) -> dict:
        self.send_text(json.dumps(
            {"id": msg_id, "method": method, "params": params}))
        while True:
            raw = self.recv_text()
            if raw is None:
                raise CdpError("等待 CDP 响应时连接关闭")
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") == msg_id:
                return msg

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------- CDP 入口


def list_targets(port: int = DEFAULT_PORT, timeout: float = 4.0) -> list[dict]:
    """列出可调试的目标。端口没开时抛 CdpError。"""
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise CdpError(f"连不上调试端口 {port}（WorkBuddy 是否已开启调试端口并重启？）: "
                       f"{type(e).__name__}") from e


def pick_target(targets: list[dict]) -> dict | None:
    """优先选 workbuddy 域的页面。"""
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    for t in pages:
        if "workbuddy" in (t.get("url") or "").lower():
            return t
    return pages[0] if pages else None


def evaluate(js: str, port: int = DEFAULT_PORT) -> str:
    """在页面上下文里执行 JS，返回其字符串结果。"""
    target = pick_target(list_targets(port))
    if not target:
        raise CdpError("没有找到可用的页面目标")
    ws = WebSocket(target["webSocketDebuggerUrl"])
    try:
        resp = ws.call("Runtime.evaluate", {
            "expression": js,
            "awaitPromise": True,
            "returnByValue": True,
        })
    finally:
        ws.close()

    if "error" in resp:
        raise CdpError(f"CDP 报错: {resp['error']}")
    result = (resp.get("result") or {}).get("result") or {}
    value = result.get("value")
    if value is None:
        desc = (resp.get("result") or {}).get("exceptionDetails")
        raise CdpError(f"页面执行无返回: {str(desc)[:200]}")
    return str(value)


ACCOUNT_JS = """
(() => {
  const svc = window.__genieAccountService;
  const a = svc && svc.account;
  if (!a) return JSON.stringify({ok: false, reason: '页面里没有 __genieAccountService.account'});
  const left = a.usageLeft;
  return JSON.stringify({
    ok: (left !== undefined && left !== null && left !== ''),
    left: left, total: a.usageTotal, used: a.usageUsed,
    isPro: a.isPro, edition: a.editionType, expireAt: a.expireAt
  });
})()
"""


def _fmt(v: float) -> str:
    if v >= 100_000_000:
        return f"{v / 100_000_000:.2f}亿"
    if v >= 10_000:
        return f"{v / 10_000:.2f}万"
    return f"{v:,.0f}"


def query_points(port: int = DEFAULT_PORT) -> tuple[bool, str, str]:
    """
    返回 (成功, 数值文本, 说明)。

    **主路径**：读 `window.__genieAccountService.account.usageLeft`。
    这才是正解 —— WorkBuddy 的界面是 `file://` 本地页面，
    从它这儿 fetch workbuddy.cn 的接口**必然 401**（origin 是 file://，不会带那边的 cookie）。
    它自己走的是 Electron 主进程 IPC + 运行时账号对象，所以直接读那个对象最靠谱。

    老路（在页面里 fetch 接口）保留为备选，兼容可能的变化。
    """
    reason = ""
    try:
        d = json.loads(evaluate(ACCOUNT_JS, port=port))
        if d.get("ok"):
            left = float(d["left"])
            note = (f"total={d.get('total')} used={d.get('used')} "
                    f"edition={d.get('edition')} isPro={d.get('isPro')}")
            return True, _fmt(left), note
        reason = str(d.get("reason") or "account 里没有 usageLeft")
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"

    # 备选：直接 fetch（本机实测 401，留着以防将来页面改成远程加载）
    try:
        payload = json.loads(evaluate(FETCH_JS, port=port))
        if not payload.get("error") and payload.get("status") == 200:
            data = json.loads(payload.get("body") or "{}")
            pkgs = ((data or {}).get("data") or {}).get("Packages") or []
            total, got = 0.0, False
            for pkg in pkgs:
                v = (pkg or {}).get("CycleRemainCapacity")
                if isinstance(v, (int, float)):
                    total += float(v)
                    got = True
            if got:
                return True, _fmt(total), "via fetch"
    except Exception:
        pass

    return False, "", reason or "取不到余额"


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    print(f"调试端口: {port}")
    try:
        ts = list_targets(port)
        print(f"找到 {len(ts)} 个目标：")
        for t in ts[:8]:
            print(f"  [{t.get('type')}] {(t.get('url') or '')[:90]}")
        t = pick_target(ts)
        print(f"\n选中: {(t or {}).get('url', '(无)')[:90]}")
        ok, txt, msg = query_points(port)
        print(f"\n结果: {'成功' if ok else '失败'}  {txt}  {msg}")
    except CdpError as e:
        print(f"[失败] {e}")
        sys.exit(1)
