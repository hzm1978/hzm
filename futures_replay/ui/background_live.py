from __future__ import annotations

import importlib.util
import importlib.metadata
import json
from collections import deque
from pathlib import Path
import re
import sys
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                              QLineEdit, QVBoxLayout)
from ..local_login import LocalLoginStore
from .. import __version__
from ..realtime_data import RealtimeTarget, shanghai_time


def background_python(executable):
    path = Path(executable)
    # pythonw on Windows can have no usable standard streams. The worker's
    # JSON protocol requires the console interpreter from the same environment.
    return str(path.with_name("python.exe")) if path.name.lower() == "pythonw.exe" else str(executable)


def redact_diagnostic(value, secrets=()):
    text = str(value)
    for secret in sorted({str(s) for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, "[已隐藏]")
        text = text.replace(json.dumps(secret, ensure_ascii=True)[1:-1], "[已隐藏]")
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1[已隐藏]@", text)
    text = re.sub(r'''(?i)(["']?(?:password|passwd|access_token|refresh_token|token|client_secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r"\1[已隐藏]", text)
    return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[已隐藏]", text)


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
    viewStatusChanged = Signal(object)
    runningChanged = Signal(bool)
    targetsReady = Signal(object)
    connectionTick = Signal()

    def __init__(self, parent=None, *, program=None, arguments=None, connection_timeout_ms=90000):
        super().__init__(parent)
        self.process = None
        self.mode = ""
        self.context = {}
        self._payload = self._pending = None
        self._buffer = b""
        self._last_error = ""
        self.last_status = "后台行情尚未启动"
        self._configured = False
        self._view_request = None
        self._stop_requested = False
        self._resolved_targets = {}
        self._program = background_python(program or sys.executable)
        self._arguments = arguments or ["-X", "utf8", "-u", "-m", "futures_replay.live_service"]
        self._events = deque(maxlen=80)
        self._redact_values = ()
        self._stderr_tail = ""
        self._stderr_buffer = b""
        self._worker_info = {}
        self._service_ready = False
        self._collection_started = False
        self._started_at = self._view_requested_at = 0.
        self._last_preview_info = None
        self._last_view_text = ""
        self._phase_text = "后台行情尚未启动"
        self._connection_guard = QTimer(self); self._connection_guard.setSingleShot(True)
        self._connection_guard.setInterval(connection_timeout_ms)
        self._connection_guard.timeout.connect(self._connection_timeout)
        self._connection_clock = QTimer(self); self._connection_clock.setInterval(1000)
        self._connection_clock.timeout.connect(self.connectionTick.emit)
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
        self._configured = False
        self._view_request = None
        self._stop_requested = False
        # Keep known market identities while a replacement worker connects.
        # Raw-contract requests still re-resolve the actual main in the worker.
        self._redact_values = (payload.get("username", ""), payload.get("password", ""))
        self._stderr_tail = ""
        self._stderr_buffer = b""
        self._worker_info = {}
        self._service_ready = False
        self._collection_started = False
        self._last_preview_info = None
        self._last_view_text = ""
        self._started_at = time.monotonic()
        self._phase_text = "正在启动后台实时行情…"
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        process.started.connect(self._started)
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(self._discard_stderr)
        process.errorOccurred.connect(self._error)
        process.finished.connect(self._finished)
        self._status("正在启动后台实时行情…")
        process.start(self._program, self._arguments)
        self._connection_guard.start()
        self._connection_clock.start()
        self.runningChanged.emit(True)

    @Slot()
    def _started(self):
        # QProcess accepts writes while Starting. A view command sent then
        # used to precede the login/config line and break the child protocol.
        self._configured = True
        self._send(self._payload)
        self._payload = None
        if self._stop_requested:
            self._send({"command": "stop"})
        elif self._view_request is not None:
            self._send(self._view_request)

    def _send(self, value):
        if self.process is not None and self._configured and value is not None:
            self.process.write((json.dumps(value, ensure_ascii=False)+"\n").encode("utf-8"))

    def _status(self, text):
        self.last_status = redact_diagnostic(text, self._redact_values)
        self._event("状态", self.last_status)
        self.statusChanged.emit(self.last_status)
        self.connectionTick.emit()

    def _event(self, kind, text):
        self._events.append(f"{shanghai_time():%H:%M:%S} {kind}：{redact_diagnostic(text, self._redact_values)[:1200]}")

    def connection_summary(self):
        if self._last_error:
            return "实时连接："+self._last_error
        if not self.is_running():
            return self.last_status
        if self._stop_requested:
            return self.last_status
        elapsed = max(0, int(time.monotonic()-self._started_at))
        if not self._service_ready:
            return f"实时连接：{self._phase_text}（已等待{elapsed}秒）"
        if not self._collection_started:
            return f"SDK初始化完成；{self._phase_text}（已等待{elapsed}秒）"
        wanted = self._view_request or {}
        info = self._last_preview_info
        if info and info["symbol"] == wanted.get("tq_symbol") and info["period"] == wanted.get("period") and info["rows"]:
            return f"服务器报价时间：{info['quote_time'] or '尚未返回'}；服务器K线截至：{info['last_bar']}"
        wait = max(0, int(time.monotonic()-self._view_requested_at)) if self._view_requested_at else 0
        return f"订阅已建立；{self._last_view_text or '等待当前合约K线'}（等待{wait}秒）"

    def diagnostic_text(self):
        info = self._worker_info
        lines = [f"期货复盘训练系统 v{__version__}", f"本机北京时间：{shanghai_time()}",
                 f"后台程序：{self._program}", f"后台运行：{self.is_running()}",
                 f"连接状态：{self.connection_summary()}", f"最近错误：{self._last_error or '无'}",
                 f"后台Python：{info.get('python', '尚未回报')}",
                 f"后台组件：Python {info.get('python_version', '—')} / SDK {info.get('sdk', '—')} / pandas {info.get('pandas', '—')}"]
        wanted = self._view_request or {}
        lines += [f"当前订阅：{wanted.get('tq_symbol', '—')} / {wanted.get('period', '—')}", "", "最近连接事件（凭据已脱敏）：", *self._events]
        lines.insert(8, "最后预览摘要："+json.dumps(self._last_preview_info,ensure_ascii=False))
        lines.insert(4, f"后台PID：{self.process.processId() if self.process is not None else 0}")
        return redact_diagnostic("\n".join(lines), self._redact_values)

    def _connection_timeout(self):
        if self.process is None or self._service_ready or self._stop_requested:
            return
        seconds = self._connection_guard.interval()/1000
        self._last_error = f"实时连接超过{seconds:g}秒，停在“{self._phase_text}”。请点重新连接或查看连接详情中的具体错误。"
        self._stop_requested = True
        self._send({"command": "stop"})
        self._guard.start()
        self._status(self._last_error)

    def request_view(self, tq_symbol=None, period="1min", *, enabled=True, target=None):
        # Keep only the latest selection while starting; closing the view
        # must also replace a queued enable rather than open it later.
        self._view_request = {"command": "view", "enabled": enabled,
                              "tq_symbol": tq_symbol, "period": period}
        if target is not None and enabled:
            self._view_request["target"] = dict(root=str(target.root.resolve()), source=target.source,
                symbol=target.symbol, contract=target.contract, tq_symbol=target.tq_symbol)
        self._view_requested_at = time.monotonic()
        self._last_view_text = "正在请求当前合约K线"
        self._send(self._view_request)
        self.connectionTick.emit()

    def resolved_target(self, root, source, symbol):
        info = self._resolved_targets.get((str(Path(root).resolve()), source, symbol))
        return RealtimeTarget(Path(root), **info) if info else None

    def _remember_targets(self, message):
        self.context["targets"] = message.get("targets", [])
        for item in self.context["targets"]:
            root = item.get("root", message.get("root", self.context.get("root")))
            if root is None:
                continue
            info = {key: item[key] for key in ("source", "symbol", "contract", "tq_symbol")}
            self._resolved_targets[(str(Path(root).resolve()), info["source"], info["symbol"])] = info
        self.targetsReady.emit(self.context["targets"])

    def stop(self, *, cancel_pending=True):
        if cancel_pending:
            self._pending = None
        if self.process is not None:
            self._stop_requested = True
            self._send({"command": "stop"})
            self._status("正在停止后台行情…")
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
            if kind == "hello":
                self._worker_info = {key: message.get(key, "—") for key in ("python", "python_version", "sdk", "pandas")}
                self._event("后台", f"已启动：{self._worker_info}")
            elif kind == "saved":
                self.saved.emit(message)
            elif kind in {"started", "targets"}:
                self._remember_targets(message)
                if kind == "started":
                    self._service_ready = True
                    self._collection_started = True
                    self._connection_guard.stop()
                    self._status(f"已连接免费实时行情 · {message.get('count',0)}个品种；勾选“实时跟随”看最新K线")
            elif kind == "preview":
                latest_preview = message
            elif kind in {"view_status", "view_error"}:
                self.viewStatusChanged.emit(message)
                requested = self._view_request or {}
                if message.get("tq_symbol") == requested.get("tq_symbol") and message.get("period") == requested.get("period"):
                    self._last_view_text = redact_diagnostic(message.get("text", ""), self._redact_values)
                    self._event("订阅", self._last_view_text)
                    self.connectionTick.emit()
                if (kind == "view_error" and requested.get("enabled")
                        and message.get("tq_symbol") == requested.get("tq_symbol")
                        and message.get("period") == requested.get("period")):
                    self._status(str(message.get("text", "")))
            elif kind in {"status", "error"}:
                if message.get("stage"):
                    self._phase_text = redact_diagnostic(message.get("text", ""), self._redact_values)
                    if message["stage"] == "api_ready":
                        self._service_ready = True
                        self._connection_guard.stop()
                if kind == "error":
                    self._last_error = redact_diagnostic(message.get("text", ""), self._redact_values)
                self._status(str(message.get("text", "")))
        if len(self._buffer) > 4_000_000:
            self._buffer = b""
        if latest_preview is not None:
            bars = latest_preview.get("bars", [])
            self._last_preview_info = dict(symbol=latest_preview.get("tq_symbol"),period=latest_preview.get("period"),
                quote_time=latest_preview.get("quote_time"),rows=len(bars),last_bar=bars[-1].get("datetime") if bars else "—")
            self.previewReady.emit(latest_preview)
            self.connectionTick.emit()

    @Slot()
    def _discard_stderr(self, flush=False):
        if self.process is not None:
            self._stderr_buffer += bytes(self.process.readAllStandardError())
            while b"\n" in self._stderr_buffer:
                line, self._stderr_buffer = self._stderr_buffer.split(b"\n", 1)
                self._keep_stderr(line.decode("utf-8", errors="replace"))
            if len(self._stderr_buffer) > 65536:
                self._stderr_buffer = b""
                self._event("后台输出", "过长的未结束日志行已省略")
            if flush and self._stderr_buffer:
                self._keep_stderr(self._stderr_buffer.decode("utf-8", errors="replace"))
                self._stderr_buffer = b""

    def _keep_stderr(self, value):
        value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
        value = redact_diagnostic(value, self._redact_values)
        self._stderr_tail = (self._stderr_tail+value+"\n")[-4000:]
        self._event("后台输出", value)

    @Slot(QProcess.ProcessError)
    def _error(self, error):
        self._payload = None
        detail = self.process.errorString() if self.process is not None else "进程不可用"
        self._event("进程", detail)
        if error == QProcess.ProcessError.Crashed:
            # A watchdog kill must not replace the original login timeout.
            if not self._stop_requested:
                self._status("后台进程异常："+detail)
            return
        if not self._last_error:
            self._last_error = redact_diagnostic("后台行情连接未完成："+detail, self._redact_values)
        self._status(self._last_error)
        if error == QProcess.ProcessError.FailedToStart:
            self._finished()

    @Slot()
    def _finished(self, *_):
        process = self.process
        if process is not None:
            self._discard_stderr(flush=True)
            if not self._last_error and not self._stop_requested and process.exitCode() != 0:
                detail = self._stderr_tail.strip().splitlines()
                self._last_error = f"后台异常退出（代码{process.exitCode()}）：" + (detail[-1][:400] if detail else "请查看连接详情")
        self.process = None
        self._payload = None
        self._configured = False
        self._guard.stop()
        self._connection_guard.stop()
        self._connection_clock.stop()
        if process is not None:
            process.deleteLater()
        self.runningChanged.emit(False)
        pending, self._pending = self._pending, None
        if pending is not None:
            self._begin(pending)
        else:
            self._status("后台实时更新已停止" + ("："+self._last_error if self._last_error else ""))
            self._redact_values = ()

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
