from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    def run(self):
        try:
            self.signals.done.emit(self.function())
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def launch(function, done, failed):
    task = Task(function)
    task.signals.done.connect(done)
    task.signals.failed.connect(failed)
    QThreadPool.globalInstance().start(task)
    return task
