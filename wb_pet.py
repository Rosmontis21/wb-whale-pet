#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WorkBuddy 鲸鱼桌宠 —— 一只跟着你的 WorkBuddy 工作状态变化的蓝发鲸鱼娘。

工作原理
--------
只读轮询 %USERPROFILE%\\.workbuddy\\workbuddy.db 的 sessions 表：
    status='working'  → 有会话正在跑，切「干活中」
    全部 completed    → 看 last_activity_at 距今多久，依次进入 待机 / 打盹 / 睡着
不写入、不联网、不改动 WorkBuddy 任何文件。

形象素材来源
------------
MeteorNOX/DeepSeek-Balance-Whale-Widget（MIT License）
分支 For–WinDesktop，路径 frontend/assets/images 与 frontend/assets/audio
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request

# ---------------------------------------------------------------- 路径与常量

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
IMAGES_DIR = os.path.join(ASSETS_DIR, "images")
AUDIO_DIR = os.path.join(ASSETS_DIR, "audio")
CONFIG_PATH = os.path.join(BASE_DIR, "pet_config.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")

APP_NAME = "WorkBuddy 鲸鱼桌宠"
VERSION = "1.0.0"

# 立绘原始尺寸
SPRITE_PX = 610

# 窗口内边距：左右下留白给光晕，顶部留白给台词气泡
PAD = 14
PAD_TOP = 52

# 状态 -> 立绘文件
STATE_SPRITE = {
    "working": "half_open_eyes.png",    # 盯着屏幕干活
    "thinking": "half_closed_eyes.png",  # 半眯着思考过渡态
    "idle": "main.png",                  # 常态待机
    "sleepy": "exhausted.png",           # 累了
    "asleep": "close_eyes.png",          # 睡着了
    "done": "stroking.png",              # 任务完成，满足
    "happy": "shy.png",                  # 被摸，害羞
    "angry": "angry.png",                # 被戳烦了
    "sad": "disappointed.png",           # 失落
    "blink": "close_eyes.png",           # 眨眼瞬时帧
}

# 语录库：优先读 quotes.json（用户可自行增删），缺失时用内置兜底
QUOTES_PATH = os.path.join(BASE_DIR, "quotes.json")

DEFAULT_QUOTES = {
    "idle": ["待命中", "随时候着", "哦鲸鲸"],
    "working": ["干活中…", "在写了在写了", "别催，忙着呢"],
    "thinking": ["嗯……让我想想", "这个有点绕"],
    "done": ["搞定！", "收工~", "哦鲸鲸，这轮干完了"],
    "sleepy": ["有点困了…", "你还在吗"],
    "asleep": ["Zzz…", "睡着了，别吵"],
    "happy": ["哎呀别摸头", "嘿嘿…"],
    "click": ["哦鲸鲸！", "哎呀别戳", "嘿嘿…"],
    "angry": ["别戳了！", "烦诶"],
    "sad": ["唔…没过", "出错了…"],
    "points": ["哦鲸鲸，还剩 {points} 积分", "余额 {points}"],
    "points_fail": ["哦鲸鲸，查不到积分…"],
}


def load_quotes() -> dict:
    """quotes.json 只覆盖它显式提供的分类，其余用内置兜底。"""
    merged = {k: list(v) for k, v in DEFAULT_QUOTES.items()}
    try:
        if os.path.exists(QUOTES_PATH):
            with open(QUOTES_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    if k.startswith("_") or not isinstance(v, list):
                        continue
                    v = [str(x) for x in v if str(x).strip()]
                    if v:
                        merged[k] = v
    except Exception as e:
        log(f"语录读取失败，用内置兜底: {e}")
    return merged

STATE_LABEL = {
    "working": "干活中",
    "thinking": "思考中",
    "idle": "待机",
    "sleepy": "犯困",
    "asleep": "睡着了",
    "done": "刚完成",
    "happy": "开心",
    "angry": "生气",
    "sad": "失落",
    "blink": "眨眼",
}

# 判断阈值（秒）
DONE_WINDOW = 25        # 刚结束多久内算「刚完成」
IDLE_UNTIL = 300        # 5 分钟内算待机
SLEEPY_UNTIL = 900      # 15 分钟内算犯困，再久就睡着
STALE_WORKING = 600     # working 但心跳超 10 分钟，视为卡住

SIZE_PRESETS = {"小": 0.14, "中": 0.18, "大": 0.26}

DEFAULT_CONFIG = {
    "scale": 0.18,             # 610px 立绘 -> 约 110px，屏幕右下角一小只
    "pos": None,               # [x, y]，None 表示右下角
    "always_on_top": True,
    "watch_enabled": True,
    "sound_enabled": True,
    # 事件 -> 音效名（不含扩展名）。留空用 DEFAULT_SOUND_MAP。
    # 可用「选音效.bat」图形化改。
    "sound_map": {},
    "locked": False,
    "wb_db": "",               # 空 = 自动探测
    # 随 WorkBuddy 退出而退出。由 MCP 宿主拉起时打开；手动双击 start.vbs 时保持关闭，
    # 这样你可以单独把桌宠当普通挂件用。
    "tie_to_workbuddy": False,

    # 台词呈现：bubble = 白底漫画气泡（默认）/ clean = 纯文字 + 白描边
    "text_style": "bubble",

    # 工作态身后那圈浅蓝底光。用户说过不喜欢，**默认关**。
    "glow": False,

    # 剩余积分：auto = 先试 http 再回落 estimate
    "points_source": "auto",
    "points_cdp_port": 0,     # 0 = 用默认 9222（WorkBuddy 的调试端口）
    "points_cookie": "",      # http 模式才需要：浏览器里抓的整行 Cookie
    "points_url": "",         # 留空用内置接口地址
    "initial_credit": 0,      # estimate 模式：总额度（填了才估算得出来）
}


def log(msg: str) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with open(os.path.join(LOG_DIR, "pet.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception as e:
        log(f"配置读取失败，用默认值: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"配置保存失败: {e}")


def find_wb_db() -> str:
    """定位 WorkBuddy 会话库。"""
    cand = os.path.join(os.path.expanduser("~"), ".workbuddy", "workbuddy.db")
    return cand if os.path.exists(cand) else ""


# ---------------------------------------------------------------- 音效
#
# 可自由更换：把 wav/mp3 丢进 assets/audio/ 即可被识别（mp3 会用 ffmpeg 自动转 wav，
# 因为本机 PySide6 没有多媒体插件，只能走 winsound）。
# 每个「事件」用哪个音效，由 pet_config.json 的 sound_map 决定；
# 图形化选择用 soundpicker.py（或双击「选音效.bat」）。

SOUND_EVENTS = {
    "press": "按下（开始拖她）",
    "release": "松开",
    "double": "双击",
    "done": "任务完成",
    "fail": "查询失败 / 出错",
}

DEFAULT_SOUND_MAP = {
    "press": "duck-press",
    "release": "duck-release",
    "double": "dingdong-press",
    "done": "dingdong-release",
    "fail": "duck-release",
}


def list_sound_names() -> list[str]:
    """扫描 assets/audio，返回可用音效名（去扩展名）。"""
    names = set()
    if os.path.isdir(AUDIO_DIR):
        for f in os.listdir(AUDIO_DIR):
            stem, ext = os.path.splitext(f)
            if ext.lower() in (".wav", ".mp3") and not stem.endswith(".tmp"):
                names.add(stem)
    return sorted(names)


def ensure_wav(name: str) -> str | None:
    """
    返回可播放的 wav 路径。没有 wav 但有 mp3 时，用 ffmpeg 转一份同名的放在同目录。
    转不了就返回 None（静默降级，不报错）。
    """
    if not name:
        return None
    wav = os.path.join(AUDIO_DIR, f"{name}.wav")
    if os.path.exists(wav):
        return wav
    mp3 = os.path.join(AUDIO_DIR, f"{name}.mp3")
    if not os.path.exists(mp3):
        return None
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    try:
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", mp3,
             "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", wav],
            timeout=30, creationflags=_NO_WINDOW,
            capture_output=True,
        )
        if os.path.exists(wav) and os.path.getsize(wav) > 44:
            log(f"已把 {name}.mp3 转成 wav")
            return wav
    except Exception as e:
        log(f"转码 {name} 失败: {type(e).__name__}: {e}")
    return None


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def workbuddy_running() -> bool:
    """
    WorkBuddy 桌面端主进程是否在跑。
    检测失败时返回 True（保守：宁可继续陪着，也不要误把自己关掉）。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe", "/NH"],
            capture_output=True, text=True, timeout=6,
            creationflags=_NO_WINDOW,
        )
        return "WorkBuddy.exe" in (out.stdout or "")
    except Exception as e:
        log(f"检测 WorkBuddy 进程失败（按仍在运行处理）: {type(e).__name__}")
        return True


# ---------------------------------------------------------------- 剩余积分
#
# 现状：WorkBuddy **没有公开的积分 API，本地也不缓存余额**
# （workbuddy.db 的 session_usage.credit_json 只记「各模型消耗了多少」）。
# 所以给几条路，由 points_source 决定：
#   cdp      —— 借 WorkBuddy 自己的登录态（最可靠，且不会过期；需设调试端口）
#   http     —— 调内部余额接口，需浏览器抓来的 Cookie（会过期）
#   estimate —— 总额度(initial_credit) 减掉本地累计消耗，零配置但偏粗
#   auto     —— 依次尝试 cdp → http → estimate

DEFAULT_POINTS_URL = "https://www.workbuddy.cn/billing/meter/get-user-resource-summary"


def fmt_points(v: float) -> str:
    """积分紧凑显示：过万转「万」——气泡宽度只有一百多像素，长数字会被截断。"""
    if v >= 100_000_000:
        return f"{v / 100_000_000:.2f}亿"
    if v >= 10_000:
        return f"{v / 10_000:.2f}万"
    return f"{v:,.0f}"


def points_from_cdp(cfg: dict):
    """
    走 CDP 借 WorkBuddy **自己的登录态**查余额。

    这是最可靠的一条路：不复制凭证（本地也没有可读的），而是让 WorkBuddy 自己发请求，
    鉴权/cookie/header 全自动正确，而且**不存在 cookie 过期问题**。
    前提是 WorkBuddy 已设 WORKBUDDY_REMOTE_DEBUGGING_PORT 并重启过。
    """
    try:
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)
        import wb_cdp
    except Exception as e:
        log(f"CDP 模块不可用: {type(e).__name__}: {e}")
        return False, ""
    port = int(cfg.get("points_cdp_port") or 0) or wb_cdp.DEFAULT_PORT
    try:
        ok, txt, msg = wb_cdp.query_points(port)
    except Exception as e:
        log(f"CDP 查询失败: {type(e).__name__}: {e}")
        return False, ""
    if not ok:
        log(f"CDP 未取到余额: {msg}")
        return False, ""
    return True, txt


def points_from_http(cfg: dict):
    """调 WorkBuddy 内部余额接口。返回 (是否成功, 数值文本)。"""
    cookie = (cfg.get("points_cookie") or "").strip()
    if not cookie:
        return False, ""
    url = cfg.get("points_url") or DEFAULT_POINTS_URL
    req = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={
            "Content-Type": "application/json",
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pkgs = ((data or {}).get("data") or {}).get("Packages") or []
    total, got = 0.0, False
    for pkg in pkgs:
        v = (pkg or {}).get("CycleRemainCapacity")
        if isinstance(v, (int, float)):
            total += float(v)
            got = True
    return (True, fmt_points(total)) if got else (False, "")


def points_from_estimate(cfg: dict):
    """总额度 - 本地累计消耗（按 session_usage.credit_json 求和）。"""
    db = cfg.get("wb_db") or find_wb_db()
    used = 0.0
    if db and os.path.exists(db):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
            try:
                for (cj,) in con.execute(
                        "SELECT credit_json FROM session_usage "
                        "WHERE credit_json IS NOT NULL"):
                    try:
                        d = json.loads(cj)
                        used += sum(float(v) for v in d.values()
                                    if isinstance(v, (int, float)))
                    except Exception:
                        continue
            finally:
                con.close()
        except Exception as e:
            log(f"估算积分读取失败: {type(e).__name__}: {e}")
    total = float(cfg.get("initial_credit") or 0)
    if total > 0:
        return True, fmt_points(max(0.0, total - used))
    return False, f"{used:,.1f}"


def fetch_points(cfg: dict):
    """按 points_source 选数据源；任何异常都吞掉，绝不拖垮桌宠。"""
    src = (cfg.get("points_source") or "auto").lower()
    order = {"cdp": ["cdp"], "http": ["http"], "estimate": ["estimate"]}.get(
        src, ["cdp", "http", "estimate"])
    used_text = ""
    for which in order:
        try:
            if which == "cdp":
                ok, txt = points_from_cdp(cfg)
            elif which == "http":
                ok, txt = points_from_http(cfg)
            else:
                ok, txt = points_from_estimate(cfg)
            if ok:
                log(f"积分源 {which} 成功：{txt}")
                return True, txt
            if which == "estimate" and txt:
                used_text = txt
        except Exception as e:
            log(f"积分源 {which} 失败: {type(e).__name__}: {e}")
    return False, used_text


# ---------------------------------------------------------------- 状态探测


def probe_workbuddy(db_path: str) -> dict:
    """
    只读探测 WorkBuddy 状态。任何异常都吞掉并返回 available=False，
    保证桌宠本身绝不会因为 WorkBuddy 的问题崩掉。
    """
    out = {"available": False, "working": 0, "last_activity_ms": 0, "sessions": 0}
    if not db_path or not os.path.exists(db_path):
        return out
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM sessions WHERE deleted_at IS NULL")
            out["sessions"] = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM sessions "
                "WHERE deleted_at IS NULL AND status = 'working'"
            )
            out["working"] = cur.fetchone()[0]
            # 注意：实测 last_activity_at 会僵死在会话开始附近，
            # 真正随活动刷新的是 updated_at（session_usage.updated_at 同样灵敏）。
            cur.execute(
                "SELECT MAX(COALESCE(updated_at, last_activity_at, created_at)) "
                "FROM sessions WHERE deleted_at IS NULL"
            )
            row = cur.fetchone()
            out["last_activity_ms"] = row[0] or 0

            try:
                cur.execute("SELECT MAX(updated_at) FROM session_usage")
                row2 = cur.fetchone()
                if row2 and row2[0] and row2[0] > out["last_activity_ms"]:
                    out["last_activity_ms"] = row2[0]
            except Exception:
                pass

            out["available"] = True
        finally:
            con.close()
    except Exception as e:
        log(f"探测 WorkBuddy 失败: {type(e).__name__}: {e}")
    return out


def resolve_state(info: dict, now_ms: int) -> str:
    """把探测结果翻译成状态名。"""
    if not info.get("available"):
        return "idle"

    last = info.get("last_activity_ms") or 0
    age = (now_ms - last) / 1000.0 if last else 1e9

    if info.get("working", 0) > 0:
        # 有会话在跑；但如果心跳太久没动，多半是卡住/挂起了
        return "thinking" if age > STALE_WORKING else "working"

    if age < DONE_WINDOW:
        return "done"
    if age < IDLE_UNTIL:
        return "idle"
    if age < SLEEPY_UNTIL:
        return "sleepy"
    return "asleep"


# ---------------------------------------------------------------- 主程序


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{VERSION}")
    parser.add_argument("--snapshot", metavar="OUT.png",
                        help="不开窗口，把全部状态渲染成一张拼图用于验证")
    parser.add_argument("--probe", action="store_true",
                        help="只打印一次 WorkBuddy 状态探测结果后退出")
    parser.add_argument("--selftest", metavar="OUT.png",
                        help="启动真实窗口，约 3 秒后截取窗口及周边区域再退出（用于验证透明与位置）")
    parser.add_argument("--tie-to-workbuddy", action="store_true",
                        help="随 WorkBuddy 退出而自动退出（MCP 宿主拉起时使用）")
    parser.add_argument("--click-test", action="store_true",
                        help="启动窗口并模拟一次「按下→松开（鼠标未移动）」，验证窗口不会位移")
    parser.add_argument("--points", action="store_true",
                        help="只查一次剩余积分并按当前数据源打印结果后退出")
    args = parser.parse_args()

    cfg = load_config()
    if args.tie_to_workbuddy:
        cfg["tie_to_workbuddy"] = True
    db_path = cfg.get("wb_db") or find_wb_db()

    if args.points:
        print(f"数据源 points_source : {cfg.get('points_source')}")
        print(f"Cookie 是否已配      : {bool((cfg.get('points_cookie') or '').strip())}")
        print(f"总额度 initial_credit: {cfg.get('initial_credit')}")
        ok, txt = fetch_points(cfg)
        print(f"结果                 : {'成功' if ok else '失败'}  {txt or '(空)'}")
        if not ok:
            print("\n提示（三个数据源按这个顺序试）：")
            print("  cdp      —— 双击「开启积分查询.bat」设好调试端口，再重启 WorkBuddy")
            print("  http     —— 配置里填 points_cookie（浏览器抓包，会过期）")
            print("  estimate —— 配置里填 initial_credit（套餐总额度）")
        return 0

    if args.probe:
        info = probe_workbuddy(db_path)
        now_ms = int(time.time() * 1000)
        st = resolve_state(info, now_ms)
        print(f"db        : {db_path or '(未找到)'}")
        print(f"available : {info['available']}")
        print(f"sessions  : {info['sessions']}")
        print(f"working   : {info['working']}")
        la = info["last_activity_ms"]
        if la:
            import datetime
            print("last_act  : " + datetime.datetime.fromtimestamp(la / 1000)
                  .strftime("%Y-%m-%d %H:%M:%S"))
            print(f"age       : {(now_ms - la) / 1000:.1f}s")
        print(f"state     : {st}  ({STATE_LABEL.get(st, st)})")
        return 0

    # 延迟导入，这样 --probe 不需要 GUI 依赖
    from PySide6.QtCore import (Qt, QTimer, QThread, Signal, QPoint, QPointF,
                                QRectF)
    from PySide6.QtGui import (QPixmap, QPainter, QColor, QRadialGradient,
                               QFont, QAction, QIcon, QFontMetrics,
                               QPainterPath, QPen)
    from PySide6.QtWidgets import (QApplication, QWidget, QMenu,
                                   QSystemTrayIcon, QMessageBox)

    if args.snapshot:
        return render_snapshot(args.snapshot, QApplication(sys.argv),
                               QPixmap, QPainter, QColor, QRadialGradient,
                               QFont, Qt)

    # ---------------- 状态监听线程

    class Watcher(QThread):
        updated = Signal(dict)
        parentGone = Signal()

        # 每 ~5 秒查一次 WorkBuddy 进程，连续缺席这么多次才认定它真关了
        MISS_LIMIT = 4

        def __init__(self, path: str, tie: bool = False):
            super().__init__()
            self.path = path
            self.tie = tie
            self._stop = False
            self._miss = 0

        def run(self):
            tick = 0
            while not self._stop:
                try:
                    self.updated.emit(probe_workbuddy(self.path))
                except Exception:
                    pass

                tick += 1
                if self.tie and tick % 5 == 0:
                    if workbuddy_running():
                        self._miss = 0
                    else:
                        self._miss += 1
                        log(f"WorkBuddy 未在运行（第 {self._miss}/{self.MISS_LIMIT} 次）")
                        if self._miss >= self.MISS_LIMIT:
                            log("判定 WorkBuddy 已退出，桌宠跟随退出")
                            self.parentGone.emit()
                            return

                for _ in range(10):          # 分片睡眠，1 秒一轮
                    if self._stop:
                        return
                    self.msleep(100)

        def stop(self):
            self._stop = True

    # ---------------- 积分查询线程

    class PointsFetcher(QThread):
        """查余额可能有网络 IO，必须离开 UI 线程。"""

        done = Signal(bool, str)

        def __init__(self, config: dict):
            super().__init__()
            self.config = dict(config)

        def run(self):
            try:
                ok, txt = fetch_points(self.config)
            except Exception as e:
                log(f"查询积分异常: {type(e).__name__}: {e}")
                ok, txt = False, ""
            self.done.emit(ok, txt)

    # ---------------- 桌宠窗口

    class WhalePet(QWidget):
        def __init__(self, config: dict):
            super().__init__()
            self.cfg = config
            self._press_global = None    # 按下时的鼠标全局坐标
            self._press_win = None       # 按下时的窗口坐标
            self._dragged = False
            self._press_at = 0.0
            self._pressed = False

            self.state = "idle"
            self.state_since = time.time()
            self.quotes = load_quotes()
            self.sound_map = dict(DEFAULT_SOUND_MAP)
            self.sound_map.update(self.cfg.get("sound_map") or {})
            self.override_state = None      # 交互产生的临时状态
            self.override_until = 0.0

            self.phase = 0.0
            self.fade = 1.0                 # 状态切换时的淡入进度
            self.bubble_text = ""
            self.bubble_until = 0.0
            self.blink_at = time.time() + random.uniform(3, 6)

            self._pix_cache: dict[str, QPixmap] = {}
            self._scaled_cache: dict[tuple, QPixmap] = {}

            flags = Qt.FramelessWindowHint | Qt.Tool
            if self.cfg.get("always_on_top", True):
                flags |= Qt.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setWindowTitle(APP_NAME)
            self.setMouseTracking(True)

            self.apply_scale(self.cfg.get("scale", 0.36))
            self.restore_position()

            # 动画主循环 30fps（动作很轻，不需要 40fps）
            self.anim = QTimer(self)
            self.anim.timeout.connect(self.on_tick)
            self.anim.start(33)

            # 音效必须提前建好，否则首次点击时还没解码完 → 听不到
            self.preload_sounds()

            self.watcher = None
            if self.cfg.get("watch_enabled", True) or self.cfg.get("tie_to_workbuddy"):
                self.start_watcher()

        # ---------- 资源

        def sprite(self, name: str) -> QPixmap:
            if name not in self._pix_cache:
                path = os.path.join(IMAGES_DIR, name)
                pm = QPixmap(path)
                if pm.isNull():
                    log(f"立绘缺失: {path}")
                    pm = QPixmap(SPRITE_PX, SPRITE_PX)
                    pm.fill(Qt.transparent)
                self._pix_cache[name] = pm
            return self._pix_cache[name]

        def scaled_sprite(self, name: str) -> QPixmap:
            key = (name, self.sprite_px)
            if key not in self._scaled_cache:
                self._scaled_cache[key] = self.sprite(name).scaled(
                    self.sprite_px, self.sprite_px,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return self._scaled_cache[key]

        def apply_scale(self, scale: float) -> None:
            self.cfg["scale"] = scale
            self.sprite_px = max(80, int(SPRITE_PX * scale))
            self._scaled_cache.clear()
            self.setFixedSize(self.sprite_px + PAD * 2,
                              self.sprite_px + PAD_TOP + PAD)

        def origin(self) -> QPoint:
            """立绘在窗口内的基准左上角。"""
            return QPoint(PAD, PAD_TOP)

        # ---------- 位置

        def restore_position(self) -> None:
            pos = self.cfg.get("pos")
            if pos and isinstance(pos, list) and len(pos) == 2:
                self.move(int(pos[0]), int(pos[1]))
            else:
                self.move_to_corner()

        def move_to_corner(self) -> None:
            scr = QApplication.primaryScreen()
            if not scr:
                return
            g = scr.availableGeometry()
            self.move(g.right() - self.width() - 24,
                      g.bottom() - self.height() - 12)

        def remember_position(self) -> None:
            self.cfg["pos"] = [self.x(), self.y()]
            save_config(self.cfg)

        # ---------- 状态

        def start_watcher(self) -> None:
            if self.watcher:
                return
            tie = bool(self.cfg.get("tie_to_workbuddy"))
            self.watcher = Watcher(db_path, tie=tie)
            self.watcher.updated.connect(self.on_wb_update)
            if tie:
                self.watcher.parentGone.connect(self.on_parent_gone)
            self.watcher.start()

        def on_parent_gone(self) -> None:
            """WorkBuddy 已退出 —— 打个招呼就走。"""
            self.say("WorkBuddy 关了，我也撤啦~", 1200)
            QTimer.singleShot(1300, self.quit_app)

        def stop_watcher(self) -> None:
            if self.watcher:
                self.watcher.stop()
                self.watcher.wait(1500)
                self.watcher = None

        def on_wb_update(self, info: dict) -> None:
            if not self.cfg.get("watch_enabled", True):
                return
            new = resolve_state(info, int(time.time() * 1000))
            if new != self.state:
                log(f"状态 {self.state} -> {new}")
                self.set_state(new)

        def set_state(self, state: str) -> None:
            prev = self.state
            self.state = state
            self.state_since = time.time()
            self.fade = 0.35                # 换立绘时轻微淡入
            # 进入新状态时冒一句台词
            if state in self.quotes and state != prev:
                if state in ("done", "happy", "angry", "sad") or random.random() < 0.35:
                    self.say(random.choice(self.quotes[state]), 4000)
                else:
                    self.say(random.choice(self.quotes[state]), 2200)
            if state == "done" and prev != "done":
                self.play_sound("done")

        def poke_state(self, state: str, hold: float = 1.6) -> None:
            """交互产生的临时状态。"""
            self.override_state = state
            self.override_until = time.time() + hold
            self.fade = 0.35

        def effective_state(self) -> str:
            if self.override_state and time.time() < self.override_until:
                return self.override_state
            if self.override_state:
                self.override_state = None
            # 眨眼插帧
            if time.time() >= self.blink_at:
                self.blink_at = time.time() + random.uniform(3.0, 7.0)
                self.blink_until = time.time() + 0.16
            if getattr(self, "blink_until", 0) > time.time():
                return "blink"
            return self.state

        def say(self, text: str, hold: float = 2.5) -> None:
            self.bubble_text = text
            self.bubble_until = time.time() + hold

        # ---------- 音效

        def preload_sounds(self) -> None:
            """
            按 sound_map 把要用的音效预热成 wav。
            mp3 会在这一步被自动转码（首次启动稍慢，之后就快了）。
            """
            self._wav_cache = {}
            ready = 0
            for _ev, name in self.sound_map.items():
                if not name:
                    continue
                w = ensure_wav(name)
                self._wav_cache[name] = w or ""
                if w:
                    ready += 1
            log(f"音效就绪：{ready} 个事件可用音效；"
                f"目录内共 {len(list_sound_names())} 个可选音效")

        def play_sound(self, event: str) -> None:
            """event 取值见 SOUND_EVENTS：press / release / double / done / fail。"""
            if not self.cfg.get("sound_enabled", True):
                return
            name = self.sound_map.get(event)
            if not name:
                return
            wav = getattr(self, "_wav_cache", {}).get(name)
            if wav is None:
                wav = ensure_wav(name) or ""
                self._wav_cache[name] = wav
            if not wav:
                return
            try:
                import winsound
                winsound.PlaySound(
                    wav,
                    winsound.SND_FILENAME | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT)
            except Exception as e:
                log(f"播放音效失败({event}/{name}): {type(e).__name__}: {e}")

        # ---------- 动画

        def on_tick(self) -> None:
            # 33ms 一跳，phase 以「秒」为单位推进，动画周期才准
            self.phase += 0.033
            if self.fade < 1.0:
                self.fade = min(1.0, self.fade + 0.11)
            self.update()

        def bob(self) -> QPointF:
            """
            只保留极轻微的上下呼吸，**横向恒为 0**。

            以前是纵向 ±3px(3.4s) + 横向 ±4px(6s)，干活时横向还放大到 ±6px，
            在实际桌面上非常吵（150% 缩放下等于 ±9 物理像素，每秒都在左右蹭）。
            现在：不横移；纵向 ±1.2px 起步，靠「节奏」而不是「幅度」区分状态。
            返回值用 QPointF，走亚像素绘制，避免取整成 1px 台阶产生的抖动。
            """
            st = self.effective_state()
            if st == "asleep":
                amp, period = 2.4, 5.6      # 睡着：慢而深一点的起伏
            elif st == "working":
                amp, period = 1.2, 3.0      # 干活：节奏快些，幅度不变
            else:
                amp, period = 1.2, 4.5      # 常态：几乎察觉不到的呼吸
            dy = math.sin(2 * math.pi * self.phase / period) * amp
            return QPointF(0.0, dy)

        def paintEvent(self, _event) -> None:
            st = self.effective_state()
            pm = self.scaled_sprite(STATE_SPRITE.get(st, "main.png"))
            p = QPainter(self)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.setRenderHint(QPainter.Antialiasing, True)

            base = self.origin()
            off = self.bob()
            org = QPointF(base.x() + off.x(), base.y() + off.y())
            p.setOpacity(max(0.0, min(1.0, self.fade)))

            # 工作态光晕（默认关闭：用户明确说过不喜欢这层浅蓝底光）
            if self.cfg.get("glow", False) and st in ("working", "thinking"):
                cx = org.x() + self.sprite_px / 2
                cy = org.y() + self.sprite_px * 0.55
                r = self.sprite_px * 0.70
                grad = QRadialGradient(cx, cy, r)
                pulse = 0.45 + 0.25 * (1 + math.sin(self.phase * 1.5)) / 2
                grad.setColorAt(0.0, QColor(120, 180, 255, int(150 * pulse)))
                grad.setColorAt(0.55, QColor(110, 165, 245, int(70 * pulse)))
                grad.setColorAt(1.0, QColor(110, 165, 245, 0))
                p.setBrush(grad)
                p.setPen(Qt.NoPen)
                p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

            p.drawPixmap(org, pm)
            p.setOpacity(1.0)

            # 台词气泡
            if self.bubble_text and time.time() < self.bubble_until:
                self.draw_bubble(p)

            p.end()

        def draw_bubble(self, p: QPainter) -> None:
            """台词。bubble = 白底漫画气泡（默认）/ clean = 纯文字 + 白描边。"""
            if self.cfg.get("text_style", "bubble") == "clean":
                self._draw_text_clean(p)
            else:
                self._draw_bubble_box(p)

        def _draw_text_clean(self, p: QPainter) -> None:
            """
            纯文字 + 白色描边，**完全不画底色**。

            原来用的是半透明白气泡（alpha 232），工作态身后那圈淡蓝光晕会从底下透上来，
            整个气泡就发蓝发脏。现在去掉色块，靠白描边保证在任何壁纸上都读得清。
            """
            fm = QFontMetrics(self.font())
            show = fm.elidedText(self.bubble_text, Qt.ElideRight, self.width() - 6)
            w = fm.horizontalAdvance(show)
            x = (self.width() - w) / 2.0
            baseline = 6.0 + fm.ascent()

            path = QPainterPath()
            path.addText(x, baseline, self.font(), show)

            outline = QPen(QColor(255, 255, 255, 245))
            outline.setWidthF(3.2)
            outline.setJoinStyle(Qt.RoundJoin)
            p.setPen(outline)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(36, 50, 82))
            p.drawPath(path)

        def _draw_bubble_box(self, p: QPainter) -> None:
            """
            白底漫画气泡：**不透明**白底 + 细描边 + 指向她的小尾巴。

            关键在"不透明"——以前那版是半透明白（alpha 232），会让身后的工作态
            淡蓝光晕透上来，整块发蓝。现在完全遮住，颜色就干净了。
            """
            fm = QFontMetrics(self.font())
            text = fm.elidedText(self.bubble_text, Qt.ElideRight, self.width() - 22)
            tw = fm.horizontalAdvance(text)
            bw = min(float(self.width() - 6), tw + 22.0)
            bh = float(fm.height() + 13)
            bx = (self.width() - bw) / 2.0
            by = 7.0

            body = QPainterPath()
            body.addRoundedRect(QRectF(bx, by, bw, bh), 9.0, 9.0)

            # 小尾巴指向她头顶
            cx = self.width() / 2.0
            tail = QPainterPath()
            tail.moveTo(cx - 5.0, by + bh - 1.0)
            tail.lineTo(cx, by + bh + 7.0)
            tail.lineTo(cx + 5.0, by + bh - 1.0)
            tail.closeSubpath()

            shape = body.united(tail)
            p.setPen(QPen(QColor(72, 94, 142), 1.4))
            p.setBrush(QColor(255, 255, 255))       # ← 完全不透明，杜绝透底色
            p.drawPath(shape)

            p.setPen(QColor(44, 60, 98))
            p.drawText(QRectF(bx, by, bw, bh), Qt.AlignCenter, text)

        # ---------- 交互

        def mousePressEvent(self, e) -> None:
            if e.button() == Qt.LeftButton:
                if self.cfg.get("locked"):
                    return
                self._pressed = True
                self._press_at = time.time()
                # 只记「按下时的鼠标全局坐标」和「按下时的窗口坐标」，
                # 之后按两者**差值**移动窗口。
                #
                # ⚠️ 不要再用 frameGeometry().topLeft() 反算鼠标偏移：
                # 无边框 + 透明窗口在 150% DPI 下会算错，结果只是"按一下"（鼠标没动）
                # 窗口就跳走，还会记住错误位置。实测把用户窗口一路推到屏幕右下角。
                self._press_global = e.globalPosition().toPoint()
                self._press_win = self.pos()
                self._dragged = False
                self.play_sound("press")
                e.accept()

        def mouseMoveEvent(self, e) -> None:
            if not self._pressed or self.cfg.get("locked"):
                return
            if self._press_global is None or self._press_win is None:
                return
            delta = e.globalPosition().toPoint() - self._press_global
            if not self._dragged:
                if delta.manhattanLength() < 4:      # 手抖不算拖拽
                    return
                self._dragged = True
            self.move(self._press_win + delta)

        def mouseReleaseEvent(self, e) -> None:
            if e.button() != Qt.LeftButton:
                return
            if not self._pressed:
                return
            self._pressed = False
            moved = self._dragged
            self._dragged = False
            self._press_global = None
            self._press_win = None
            self.play_sound("release")

            if not moved:
                # 当点击处理
                if time.time() - getattr(self, "_last_click", 0) < 0.35:
                    # 连击 → 生气
                    self.poke_state("angry", 2.0)
                    self.say(random.choice(self.quotes.get("angry") or [""]), 2000)
                else:
                    self.poke_state("happy", 1.8)
                    self.say(random.choice(self.quotes.get("click") or [""]), 2200)
                self._last_click = time.time()
                self.remember_position()
            else:
                self.remember_position()

        def mouseDoubleClickEvent(self, e) -> None:
            """双击 → 报一句剩余积分。"""
            if e.button() == Qt.LeftButton:
                self.play_sound("double")
                self.show_points()

        # ---------- 剩余积分

        def show_points(self) -> None:
            """后台查询，别卡住 UI。"""
            if getattr(self, "_fetching", False):
                return
            self._fetching = True
            self.say("哦鲸鲸，我看看…", 2000)
            self._pf = PointsFetcher(self.cfg)
            self._pf.done.connect(self.on_points_done)
            self._pf.start()

        def on_points_done(self, ok: bool, txt: str) -> None:
            self._fetching = False
            key = "points" if ok else "points_fail"
            pool = self.quotes.get(key) or [""]
            line = random.choice(pool).replace("{points}", txt or "?")
            self.say(line, 5200)
            if ok:
                self.poke_state("done", 1.8)
            self.play_sound("done" if ok else "fail")

        def contextMenuEvent(self, e) -> None:
            self.build_menu().exec(e.globalPos())

        # ---------- 菜单

        def build_menu(self) -> QMenu:
            m = QMenu(self)
            m.setStyleSheet(
                "QMenu{background:#ffffff;border:1px solid #d8e0ee;padding:4px;}"
                "QMenu::item{padding:5px 22px 5px 12px;border-radius:5px;color:#2b3550;}"
                "QMenu::item:selected{background:#e8f0ff;}"
                "QMenu::separator{height:1px;background:#e6ebf5;margin:4px 6px;}"
            )

            head = m.addAction(f"🐋 {STATE_LABEL.get(self.effective_state(), '')}"
                               f" · {APP_NAME.split(' ')[0]}")
            head.setEnabled(False)
            m.addSeparator()

            size_menu = m.addMenu("大小")
            for label, sc in SIZE_PRESETS.items():
                a = QAction(f"{label}（{int(SPRITE_PX * sc)}px）", self)
                a.setCheckable(True)
                a.setChecked(abs(self.cfg.get("scale", 0.36) - sc) < 0.01)
                a.triggered.connect(lambda _c, s=sc: self.change_size(s))
                size_menu.addAction(a)

            a = QAction("置顶", self)
            a.setCheckable(True)
            a.setChecked(self.cfg.get("always_on_top", True))
            a.triggered.connect(self.toggle_top)
            m.addAction(a)

            a = QAction("锁定位置", self)
            a.setCheckable(True)
            a.setChecked(self.cfg.get("locked", False))
            a.triggered.connect(self.toggle_locked)
            m.addAction(a)

            a = QAction("跟随 WorkBuddy 状态", self)
            a.setCheckable(True)
            a.setChecked(self.cfg.get("watch_enabled", True))
            a.triggered.connect(self.toggle_watch)
            m.addAction(a)

            a = QAction("音效", self)
            a.setCheckable(True)
            a.setChecked(self.cfg.get("sound_enabled", True))
            a.triggered.connect(self.toggle_sound)
            m.addAction(a)
            m.addAction("音效设置…", self.open_sound_picker)

            style_menu = m.addMenu("文字样式")
            for label, val in (("纯文字（推荐）", "clean"), ("白底气泡", "bubble")):
                a = QAction(label, self)
                a.setCheckable(True)
                a.setChecked(self.cfg.get("text_style", "clean") == val)
                a.triggered.connect(lambda _c, v=val: self.set_text_style(v))
                style_menu.addAction(a)

            m.addSeparator()
            m.addAction("看看剩余积分", self.show_points)
            m.addSeparator()
            m.addAction("回默认位置", self.move_to_corner_and_save)
            m.addAction("关于", self.show_about)
            m.addSeparator()
            m.addAction("退出", self.quit_app)
            return m

        def set_text_style(self, val: str) -> None:
            self.cfg["text_style"] = val
            save_config(self.cfg)
            self.say("换成纯文字啦" if val == "clean" else "换回气泡啦", 2000)

        def change_size(self, sc: float) -> None:
            self.apply_scale(sc)
            save_config(self.cfg)

        def toggle_top(self, checked: bool) -> None:
            self.cfg["always_on_top"] = checked
            flags = self.windowFlags()
            if checked:
                flags |= Qt.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowStaysOnTopHint
            self.setWindowFlags(flags)
            self.show()
            save_config(self.cfg)

        def toggle_locked(self, checked: bool) -> None:
            self.cfg["locked"] = checked
            save_config(self.cfg)
            self.say("锁住啦，拖不动咯" if checked else "解锁了", 1800)

        def toggle_watch(self, checked: bool) -> None:
            self.cfg["watch_enabled"] = checked
            save_config(self.cfg)
            if checked:
                self.start_watcher()
                self.say("开始盯着 WorkBuddy 了", 1800)
            else:
                self.stop_watcher()
                self.set_state("idle")
                self.say("先不看了，我休息会儿", 1800)

        def toggle_sound(self, checked: bool) -> None:
            self.cfg["sound_enabled"] = checked
            save_config(self.cfg)

        def open_sound_picker(self) -> None:
            """打开图形化音效选择器（独立窗口）。"""
            picker = os.path.join(BASE_DIR, "soundpicker.py")
            if not os.path.exists(picker):
                log("找不到 soundpicker.py")
                return
            exe = sys.executable or "pythonw.exe"
            if exe.lower().endswith("python.exe"):
                exe = exe[:-len("python.exe")] + "pythonw.exe"
            try:
                subprocess.Popen([exe, picker], cwd=BASE_DIR)
            except Exception as e:
                log(f"打开音效设置失败: {type(e).__name__}: {e}")

        def move_to_corner_and_save(self) -> None:
            self.move_to_corner()
            self.remember_position()

        def show_about(self) -> None:
            QMessageBox.information(
                self, "关于",
                f"<b>{APP_NAME}</b> v{VERSION}<br><br>"
                f"状态来源：<code>{db_path or '未找到 workbuddy.db'}</code><br>"
                f"只读、不联网、不改动 WorkBuddy 文件。<br><br>"
                f"形象素材：MeteorNOX/DeepSeek-Balance-Whale-Widget（MIT）"
                f"<br>For–WinDesktop 分支")

        def quit_app(self) -> None:
            self.remember_position()
            self.stop_watcher()
            QApplication.quit()

    # ---------------- 启动

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    pet = WhalePet(cfg)
    pet.show()

    # 托盘
    tray = QSystemTrayIcon(QIcon(pet.sprite("main.png").scaled(
        64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)), app)
    tray.setToolTip(f"{APP_NAME} v{VERSION}")
    tray.setContextMenu(pet.build_menu())
    tray.activated.connect(
        lambda reason: pet.show() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()

    if not db_path:
        pet.say("没找到 workbuddy.db，先待机咯", 4000)
    else:
        pet.say(random.choice(pet.quotes.get("idle") or [""]), 2400)

    log(f"启动 {APP_NAME} v{VERSION}, db={db_path or '无'}")

    if args.click_test:
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        def _click_test():
            before = (pet.x(), pet.y())
            lp = QPointF(60.0, 80.0)
            gp = QPointF(pet.x() + 60.0, pet.y() + 80.0)

            pet.mousePressEvent(QMouseEvent(
                QEvent.MouseButtonPress, lp, gp,
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            # 鼠标原地不动，但 Qt 仍会派发 move 事件——这正是以前会误移动的场景
            pet.mouseMoveEvent(QMouseEvent(
                QEvent.MouseMove, lp, gp,
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            pet.mouseReleaseEvent(QMouseEvent(
                QEvent.MouseButtonRelease, lp, gp,
                Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

            after = (pet.x(), pet.y())
            ok = before == after
            print(f"click-test  before={before}  after={after}  -> "
                  f"{'未位移 OK' if ok else '**发生位移，失败**'}")

            # 顺便验证：真的拖动时窗口应该跟着走
            w0 = (pet.x(), pet.y())
            pet.mousePressEvent(QMouseEvent(
                QEvent.MouseButtonPress, lp, gp,
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            pet.mouseMoveEvent(QMouseEvent(
                QEvent.MouseMove, QPointF(160.0, 180.0), QPointF(gp.x() + 100, gp.y() + 100),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            pet.mouseReleaseEvent(QMouseEvent(
                QEvent.MouseButtonRelease, QPointF(160.0, 180.0), QPointF(gp.x() + 100, gp.y() + 100),
                Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
            w1 = (pet.x(), pet.y())
            drag_ok = (w1[0] - w0[0], w1[1] - w0[1]) == (100, 100)
            print(f"drag-test   before={w0}  after={w1}  -> "
                  f"{'位移正确(+100,+100) OK' if drag_ok else '**位移不对**'}")

            pet.stop_watcher()
            app.quit()

        QTimer.singleShot(2200, _click_test)

    if args.selftest:
        out = os.path.abspath(args.selftest)

        def _shot():
            try:
                scr = QApplication.primaryScreen()
                g = pet.geometry()
                m = 70
                pm = scr.grabWindow(0, g.x() - m, g.y() - m,
                                    g.width() + 2 * m, g.height() + 2 * m)
                ok = pm.save(out)
                print(f"selftest -> {out} ({'ok' if ok else 'failed'}) "
                      f"state={pet.effective_state()} geo={g.x()},{g.y()} "
                      f"{g.width()}x{g.height()}")
                log(f"selftest ok={ok} state={pet.effective_state()} geo={g}")
            except Exception as e:
                print("selftest 失败:", e)
                log("selftest 失败: " + traceback.format_exc())
            finally:
                pet.stop_watcher()
                app.quit()

        QTimer.singleShot(2800, _shot)

    # PID 文件：方便 stop 脚本精确结束本进程（避免误杀其它 python）
    pid_path = os.path.join(BASE_DIR, "run", "pet.pid")
    try:
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pid_path = ""

    try:
        return app.exec()
    finally:
        pet.stop_watcher()
        if pid_path and os.path.exists(pid_path):
            try:
                os.remove(pid_path)
            except Exception:
                pass


# ---------------------------------------------------------------- 快照渲染


def render_snapshot(out_path, app, QPixmap, QPainter, QColor, QRadialGradient,
                    QFont, Qt):
    """把全部状态横向拼成一张 PNG，用于快速验证立绘与映射。"""
    from PySide6.QtGui import QImage

    order = ["working", "thinking", "idle", "sleepy", "asleep",
             "done", "happy", "angry", "sad"]
    cell = 200
    pad = 18
    label_h = 26
    cols = len(order)
    W = cols * cell + (cols + 1) * pad
    H = cell + label_h + pad * 2

    canvas = QImage(W, H, QImage.Format_ARGB32)
    canvas.fill(QColor(250, 251, 254, 255))

    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    font = QFont("Microsoft YaHei UI", 9)
    p.setFont(font)

    for i, st in enumerate(order):
        fname = STATE_SPRITE.get(st)
        pm = QPixmap(os.path.join(IMAGES_DIR, fname))
        x = pad + i * (cell + pad)
        y = pad
        if not pm.isNull():
            p.drawPixmap(x, y, cell, cell,
                         pm.scaled(cell, cell, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation))
        p.setPen(QColor(43, 53, 80))
        p.drawText(x, y + cell + 4, cell, label_h,
                   Qt.AlignCenter, f"{st} · {STATE_LABEL.get(st, '')}")
    p.end()

    ok = canvas.save(out_path)
    print(f"snapshot -> {out_path} ({'ok' if ok else 'failed'})")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("崩溃:\n" + traceback.format_exc())
        raise
