#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
鲸鱼桌宠 · 音效设置

给每个动作挑一个音效，可以试听。

用自己的音频
------------
把 wav 或 mp3 丢进 `assets/audio/`，回到本窗口点「重新扫描」即可出现在列表里。
mp3 会在选中/试听时自动用 ffmpeg 转成 wav（本机 PySide6 缺多媒体插件，
播放统一走 winsound，所以必须是 wav——这一步是自动的，你不用管）。

保存后重启桌宠生效（双击桌面「重启鲸鱼桌宠.bat」）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import winsound

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from wb_pet import (AUDIO_DIR, CONFIG_PATH, DEFAULT_SOUND_MAP, SOUND_EVENTS,
                    ensure_wav, list_sound_names, load_config, save_config)


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f


class SoundPicker(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.rows: dict[str, QComboBox] = {}
        self.setWindowTitle("鲸鱼桌宠 · 音效设置")
        self.setMinimumWidth(560)
        self._build()
        self.refresh()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(9)

        tip = QLabel(
            "给每个动作挑一个音效。\n"
            "想用自己的音频：把 wav / mp3 丢进音效文件夹，再点「重新扫描」。"
            "（mp3 会自动转码）")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#3a4a6b;")
        v.addWidget(tip)
        v.addWidget(hline())

        for ev, label in SOUND_EVENTS.items():
            h = QHBoxLayout()
            lb = QLabel(label)
            lb.setMinimumWidth(150)
            h.addWidget(lb)

            cb = QComboBox()
            cb.setMinimumWidth(230)
            h.addWidget(cb, 1)

            btn = QPushButton("试听")
            btn.setFixedWidth(64)
            btn.clicked.connect(lambda _checked=False, e=ev: self.preview(e))
            h.addWidget(btn)

            self.rows[ev] = cb
            v.addLayout(h)

        v.addWidget(hline())

        h2 = QHBoxLayout()
        b_dir = QPushButton("打开音效文件夹")
        b_dir.clicked.connect(self.open_folder)
        b_scan = QPushButton("重新扫描")
        b_scan.clicked.connect(self.refresh)
        b_save = QPushButton("保存")
        b_save.setDefault(True)
        b_save.clicked.connect(self.save)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.close)
        for b in (b_dir, b_scan, b_save, b_close):
            h2.addWidget(b)
        v.addLayout(h2)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#5a6b8c; font-size:12px;")
        v.addWidget(self.status)

        self.setStyleSheet("""
            QWidget { background:#fbfcfe; color:#26314a; }
            QLabel { background:transparent; }
            QComboBox, QPushButton {
                background:#ffffff; border:1px solid #cfd8ea;
                border-radius:6px; padding:5px 9px; color:#26314a;
            }
            QComboBox:hover, QPushButton:hover { border-color:#8fa6d4; }
            QPushButton:pressed { background:#eef3fd; }
        """)

    # ---------- 数据

    def refresh(self) -> None:
        names = list_sound_names()
        cur = dict(DEFAULT_SOUND_MAP)
        cur.update(self.cfg.get("sound_map") or {})
        for ev, cb in self.rows.items():
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("（不播放）", "")
            for n in names:
                cb.addItem(n, n)
            want = cur.get(ev, "")
            idx = cb.findData(want)
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
        self.status.setText(f"音效目录：{AUDIO_DIR}　|　共 {len(names)} 个音效")

    def open_folder(self) -> None:
        try:
            os.startfile(AUDIO_DIR)      # noqa: S606  (Windows only)
        except Exception as e:
            QMessageBox.warning(self, "打不开", f"{type(e).__name__}: {e}")

    def preview(self, ev: str) -> None:
        name = self.rows[ev].currentData()
        if not name:
            QMessageBox.information(self, "试听", "这一项设成了「不播放」。")
            return
        wav = ensure_wav(name)
        if not wav:
            QMessageBox.warning(
                self, "试听失败",
                f"找不到可播放的音效：{name}\n\n"
                f"确认文件在：{AUDIO_DIR}\n"
                "mp3 需要系统里有 ffmpeg 才能自动转码。")
            return
        try:
            winsound.PlaySound(
                wav, winsound.SND_FILENAME | winsound.SND_ASYNC
                | winsound.SND_NODEFAULT)
        except Exception as e:
            QMessageBox.warning(self, "试听失败", f"{type(e).__name__}: {e}")

    def save(self) -> None:
        chosen = {ev: (cb.currentData() or "") for ev, cb in self.rows.items()}
        self.cfg["sound_map"] = {k: v for k, v in chosen.items() if v}
        try:
            save_config(self.cfg)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"{type(e).__name__}: {e}")
            return
        QMessageBox.information(
            self, "已保存",
            "音效设置已保存。\n\n"
            "重启桌宠后生效 —— 双击桌面上的「重启鲸鱼桌宠.bat」。")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("鲸鱼桌宠 · 音效设置")
    from PySide6.QtGui import QFont
    app.setFont(QFont("Microsoft YaHei UI", 9))
    w = SoundPicker()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
