from __future__ import annotations

import importlib.util
import importlib.metadata
import json
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                              QLineEdit, QVBoxLayout)
from ..local_login import LocalLoginStore


def component_issue():
    if any(importlib.util.find_spec(name) is None for name in ("tqsdk","pyarrow")):
        return "请先退出程序，运行包内“安装或更新依赖.bat”，完成后重启。"
    try:
        from packaging.version import Version
        sdk = Version(importlib.metadata.version("tqsdk"))
        pandas = Version(importlib.metadata.version("pandas"))
        if not Version("3.10.4") <= sdk < Version("4") or pandas >= Version("3.1"):
            return "实时组件需要更新。请退出程序，运行“安装或更新依赖.bat”后重启。"
    except importlib.metadata.PackageNotFoundError:
        return "实时组件未安装完整，请运行“安装或更新依赖.bat”后重启。"
    return ""


class TqLoginDialog(QDialog):
    def __init__(self, parent=None, *, setup_only=False):
        super().__init__(parent)
        self.setWindowTitle("免费实时行情登录")
        layout = QVBoxLayout(self)
        label = QLabel('使用普通天勤／快期账号。<a href="https://account.shinnytech.com/">注册免费账号</a>')
        label.setOpenExternalLinks(True); layout.addWidget(label)
        form = QFormLayout()
        self.username, self.password = QLineEdit(), QLineEdit()
        self.username.setObjectName("tq_username"); self.password.setObjectName("tq_password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("天勤账号", self.username); form.addRow("密码", self.password)
        layout.addLayout(form)
        self.store = LocalLoginStore()
        self.remember = QCheckBox("记住登录（仅当前Windows用户加密保存）")
        self.remember.setEnabled(self.store.available); self.remember.setChecked(self.store.available)
        self.remember.setToolTip("保存到本机用户目录，不随源码、行情或便携存档分发。取消勾选可清除已保存登录。")
        layout.addWidget(self.remember)
        saved = self.store.load()
        if saved:
            self.username.setText(saved[0]);self.password.setText(saved[1])
        limit = QLabel("免费按每周期最近最多10000根订阅，并非固定一年；可用长度受上市时间限制。")
        limit.setWordWrap(True);layout.addWidget(limit)
        self.message = QLabel("普通账号用于实时行情，历史更新仍使用原入口。" if self.store.available else
                              "本系统不保存密码；Windows版本可加密记住登录。")
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存设置" if setup_only else "开始后台更新")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if not self.username.text().strip() or not self.password.text():
            self.message.setText("请输入天勤账号和密码。")
            return
        try:
            if self.remember.isChecked():
                self.store.save(self.username.text().strip(),self.password.text())
            elif self.store.available:
                self.store.clear()
        except OSError:
            self.message.setText("本机加密保存未完成，可取消“记住登录”后继续。")
            return
        super().accept()


class BackgroundLiveController(QObject):
    statusChanged = Signal(str)
    saved = Signal(object)
    previewReady = Signal(object)
    runningChanged = Signal(bool)
    targetsReady = Signal(object)

    def __init__(self, parent=None, *, program=None, arguments=None):
        super().__init__(parent)
        self.process = None
        self.mode = ""
        self.context = {}
        self._payload = self._pending = None
        self._buffer = b""
        self._last_error = ""
        self._program = program or sys.executable
        self._arguments = arguments or ["-X", "utf8", "-u", "-m", "futures_replay.live_service"]
        self._guard = QTimer(self); self._guard.setSingleShot(True); self._guard.setInterval(8000)
        self._guard.timeout.connect(self._force_stop)

    def is_running(self):
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, config, username, password):
        payload = {**config, "username": username, "password": password}
        if self.process is not None:
            self._pending = payload
            self.stop(cancel_pending=False)
            return
        self._begin(payload)

    def _begin(self, payload):
        self._payload = payload
        self.context = {key: value for key, value in payload.items() if key not in {"username", "password"}}
        self.mode = payload["mode"]
        self._buffer = b""
        self._last_error = ""
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        process.started.connect(self._started)
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(self._discard_stderr)
        process.errorOccurred.connect(self._error)
        process.finished.connect(self._finished)
        process.start(self._program, self._arguments)
        self.runningChanged.emit(True)

    @Slot()
    def _started(self):
        self._send(self._payload)
        self._payload = None

    def _send(self, value):
        if self.process is not None:
            self.process.write((json.dumps(value, ensure_ascii=False)+"\n").encode("utf-8"))

    def request_view(self, tq_symbol=None, period="1min", *, enabled=True):
        self._send({"command": "view", "enabled": enabled, "tq_symbol": tq_symbol, "period": period})

    def stop(self, *, cancel_pending=True):
        if cancel_pending:
            self._pending = None
        if self.process is not None:
            self._send({"command": "stop"})
            self.statusChanged.emit("正在停止后台行情…")
            self._guard.start()

    @Slot()
    def _read(self):
        if self.process is None:
            return
        self._buffer += bytes(self.process.readAllStandardOutput())
        latest_preview = None
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.startswith(b"FRLIVE "):
                continue
            try:
                message = json.loads(line[7:].decode("utf-8"))
            except (ValueError, UnicodeError):
                continue
            kind = message.get("kind")
            if kind == "saved":
                self.saved.emit(message)
            elif kind == "started":
                self.context["targets"] = message.get("targets",[])
                self.targetsReady.emit(self.context["targets"])
                self.statusChanged.emit(f"已连接免费实时行情 · {message.get('count',0)}个品种；勾选“实时跟随”看最新K线")
            elif kind == "preview":
                latest_preview = message
            elif kind in {"status", "error", "view_error"}:
                if kind == "error":
                    self._last_error = str(message.get("text", ""))
                self.statusChanged.emit(str(message.get("text", "")))
        if len(self._buffer) > 4_000_000:
            self._buffer = b""
        if latest_preview is not None:
            self.previewReady.emit(latest_preview)

    @Slot()
    def _discard_stderr(self):
        if self.process is not None:
            self.process.readAllStandardError()

    @Slot(QProcess.ProcessError)
    def _error(self, error):
        self._payload = None
        self._last_error = "后台行情连接未完成，请检查组件、账号或网络后重试。"
        self.statusChanged.emit(self._last_error)
        if error == QProcess.ProcessError.FailedToStart:
            self._finished()

    @Slot()
    def _finished(self, *_):
        process = self.process
        self.process = None
        self._payload = None
        self._guard.stop()
        if process is not None:
            process.deleteLater()
        self.runningChanged.emit(False)
        pending, self._pending = self._pending, None
        if pending is not None:
            self._begin(pending)
        else:
            self.statusChanged.emit("后台实时更新已停止" + ("："+self._last_error if self._last_error else ""))

    @Slot()
    def _force_stop(self):
        if self.process is not None:
            self.process.kill()

    def shutdown(self):
        self._pending = None
        if self.process is None:
            return
        process = self.process
        self.stop()
        if not process.waitForFinished(2500):
            process.kill(); process.waitForFinished(1500)
