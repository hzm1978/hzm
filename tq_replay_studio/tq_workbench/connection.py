"""GUI-side asynchronous process transport, bounded waits and safe diagnostics."""
from collections import deque
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import __version__
from .bars import now_beijing
from .sdk_worker import PREFIX, redact


class Connection(QObject):
    message = Signal(object)
    statusChanged = Signal(str)
    changed = Signal()

    def __init__(self, database, parent=None, *, program=None, arguments=None, timeout_ms=90000, configuration=None):
        super().__init__(parent)
        self.database = str(database)
        program = Path(program or sys.executable)
        self.program = str(program.with_name("python.exe")) if program.name.lower() == "pythonw.exe" else str(program)
        self.arguments = arguments or (["--market-worker"] if getattr(sys, "frozen", False) else
                                       ["-X", "utf8", "-u", "-m", "tq_workbench.sdk_worker"])
        self.process = None
        self.watch = None
        self.configuration = configuration or {}
        self.revision = 0
        self.status = "尚未连接 · 可导入历史或体验演示"
        self.error = ""
        self.events = deque(maxlen=100)
        self._credentials = ()
        self._buffer = b""
        self._stderr = b""
        self._stopping = False
        self._configured = False
        self._pending = None
        self._started = 0.
        self.quote_time = ""
        self._guard = QTimer(self); self._guard.setSingleShot(True); self._guard.setInterval(timeout_ms)
        self._guard.timeout.connect(self._timeout)
        self._kill = QTimer(self); self._kill.setSingleShot(True); self._kill.setInterval(2000)
        self._kill.timeout.connect(lambda:self.process.kill() if self.process is not None else None)
        self._clock = QTimer(self); self._clock.setInterval(1000); self._clock.timeout.connect(self.changed.emit)

    @property
    def running(self):
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def sensitive_values(self):
        return (*self._credentials, *[self.configuration.get(key, "") for key in
                ("broker_account", "broker_password")])

    def set_status(self, text):
        self.status = redact(text, self.sensitive_values())
        self.events.append(f"{now_beijing():%H:%M:%S} {self.status}")
        self.statusChanged.emit(self.status); self.changed.emit()

    def connect_account(self, username, password):
        if not username.strip() or not password:
            raise ValueError("请输入天勤账号及密码")
        if self.process is not None:
            self._pending = (username, password)
            self.stop(cancel_pending=False)
            return
        self._credentials = (username, password)
        self.error = ""; self.quote_time = ""
        self._buffer = self._stderr = b""
        self._stopping = self._configured = False
        self._started = time.monotonic()
        process = QProcess(self); self.process = process
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        process.started.connect(self._on_started)
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self.set_status("正在启动天勤行情进程")
        self._guard.start(); self._clock.start()
        process.start(self.program, self.arguments)

    def _on_started(self):
        self._configured = True
        self._send(dict(username=self._credentials[0], password=self._credentials[1],
                        database=self.database, watch=self.watch, **self.configuration))
        if self._stopping:
            self._send(dict(command="stop"))

    def _send(self, payload):
        if self.process is not None and self._configured:
            self.process.write((json.dumps(payload, ensure_ascii=False)+"\n").encode())

    def select(self, symbol, seconds):
        self.revision += 1
        self.watch = dict(command="watch", symbol=symbol, seconds=sorted(set(seconds)), revision=self.revision)
        self._send(self.watch)
        return self.revision

    def _read(self):
        if self.process is None:
            return
        self._buffer += bytes(self.process.readAllStandardOutput())
        marker = PREFIX.encode()
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.startswith(marker):
                continue
            try:
                message = json.loads(line[len(marker):])
            except (ValueError, UnicodeError):
                continue
            kind = message.get("kind")
            if kind == "complete":
                self._stopping = True
                self._guard.stop()
            if kind in {"status", "error", "view_error"}:
                text = redact(message.get("text", ""), self.sensitive_values())
                message["text"] = text
                if kind == "view_error" and message.get("revision") != self.revision:
                    continue
                if kind == "error":
                    self.error = text
                stage = message.get("stage")
                if stage == "connecting":
                    self._started = time.monotonic(); self._guard.start()
                if stage == "ready":
                    self._guard.stop()
                self.set_status(text)
            elif kind == "hello":
                self.events.append(redact(f"后台：{message}", self.sensitive_values()))
            if kind in {"bars", "heartbeat"}:
                if message.get("revision") != self.revision:
                    continue
                self.quote_time = message.get("quote_time", "")
                self.changed.emit()
            self.message.emit(message)
        if len(self._buffer) > 12_000_000:
            self._buffer = b""
            self.set_status("后台回复过大，已丢弃不完整消息；可重新连接")

    def _read_stderr(self, flush=False):
        if self.process is None:
            return
        self._stderr += bytes(self.process.readAllStandardError())
        while b"\n" in self._stderr:
            line, self._stderr = self._stderr.split(b"\n", 1)
            self.events.append(redact(line.decode("utf-8", "replace"), self.sensitive_values())[:1500])
        if flush and self._stderr:
            self.events.append(redact(self._stderr.decode("utf-8", "replace"), self.sensitive_values())[:1500])
            self._stderr = b""
        if len(self._stderr) > 65536:
            self._stderr = b""

    def _timeout(self):
        self.error = f"连接超过{self._guard.interval()/1000:g}秒：{self.status}。查看连接详情或重新连接。"
        self.set_status(self.error)
        self.stop(keep_status=True)

    def stop(self, *, keep_status=False, cancel_pending=True):
        if cancel_pending:
            self._pending = None
        self._stopping = True
        self._guard.stop()
        if self.process is not None:
            self._send(dict(command="stop"))
            self._kill.start()
            if not keep_status:
                self.set_status("正在断开行情连接；复盘与缓存保留")

    def _process_error(self, error):
        if self.process is not None:
            self.events.append(redact(self.process.errorString(), self.sensitive_values()))
        if error == QProcess.ProcessError.FailedToStart:
            self.error = "后台无法启动："+redact(self.process.errorString(), self.sensitive_values())
            self._finished()

    def _finished(self, *_):
        process = self.process
        if process is None:
            return
        self._read_stderr(flush=True)
        if not self.error and not self._stopping:
            tail = self.events[-1] if self.events else "无错误输出"
            self.error = f"后台退出（代码{process.exitCode()}）：{tail}"
        self.process = None
        self._guard.stop(); self._kill.stop(); self._clock.stop()
        process.deleteLater()
        self.set_status(self.error or "已断开行情 · 缓存及训练可以继续使用")
        pending, self._pending = self._pending, None
        if pending:
            self.connect_account(*pending)

    def summary(self):
        elapsed = max(0, int(time.monotonic()-self._started))
        return self.status+(f" · 已等待{elapsed}秒" if self._guard.isActive() else "")+(
            f" · 服务器报价 {self.quote_time}" if self.quote_time else "")

    def details(self):
        text = "\n".join([f"天勤复盘工作台 {__version__}", f"北京时间：{now_beijing()}",
            f"解释器：{self.program}", f"当前订阅：{self.watch}",
            f"状态：{self.summary()}", f"最近错误：{self.error or '无'}", "", *self.events])
        return redact(text, self.sensitive_values())

    def shutdown(self):
        self._pending = None
        if self.process is not None:
            process = self.process
            self.stop()
            if not process.waitForFinished(1800):
                process.kill(); process.waitForFinished(1500)
        self._credentials = ()
