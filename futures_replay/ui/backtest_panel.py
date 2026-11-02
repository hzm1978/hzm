from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..backtest import BacktestConfig, STRATEGIES, STRATEGY_DESCRIPTIONS, run_backtest
from ..config import PERIODS


SORT_ROLE = int(Qt.ItemDataRole.UserRole)
RESULT_ROLE = SORT_ROLE + 1


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem whose displayed text and sorting value are independent."""

    def __init__(self, text: str, sort_value=None, result_index: int | None = None):
        super().__init__(str(text))
        self.setData(SORT_ROLE, sort_value if sort_value is not None else str(text).casefold())
        if result_index is not None:
            self.setData(RESULT_ROLE, int(result_index))

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            a = self.data(SORT_ROLE)
            b = other.data(SORT_ROLE)
            try:
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    return float(a) < float(b)
                return str(a).casefold() < str(b).casefold()
            except Exception:
                pass
        return super().__lt__(other)


class _BacktestWorker(QObject):
    progress = Signal(int, int, str)
    resultReady = Signal(dict)
    finished = Signal(int, int, int)  # results, failures, skipped
    failed = Signal(str)

    def __init__(self, repo, source: str, scope: str, current_symbol: str,
                 current_contract: str, period: str, strategy_ids: list[str],
                 config: BacktestConfig, all_history: bool,
                 start_date: str | None, end_date: str | None):
        super().__init__()
        self.repo = repo
        self.source = source
        self.scope = scope
        self.current_symbol = current_symbol
        self.current_contract = current_contract
        self.period = period
        self.strategy_ids = strategy_ids
        self.config = config
        self.all_history = all_history
        self.start_date = start_date
        self.end_date = end_date
        self.cancelled = False

    @Slot()
    def cancel(self):
        self.cancelled = True

    def _continuous_contract(self, symbol: str) -> str | None:
        contracts = self.repo.list_contracts(symbol, self.period, self.source)
        if not contracts:
            return None
        preferred = [c for c in contracts if any(k in c.lower() for k in ["主连", "主力", "连续", "main", "dominant", "0"])]
        if preferred:
            return preferred[0]
        best = None
        best_n = -1
        for c in contracts:
            try:
                n = len(self.repo.load_bars(self.source, symbol, self.period, c))
            except Exception:
                n = -1
            if n > best_n:
                best, best_n = c, n
        return best or contracts[0]

    def _contract_for(self, symbol: str) -> str | None:
        if self.scope == "当前品种" and symbol == self.current_symbol and self.current_contract:
            return self.current_contract
        if self.source == "原始行情":
            return self.repo.select_active_contract(symbol, self.period, self.source)
        return self._continuous_contract(symbol)

    @Slot()
    def run(self):
        try:
            items = self.repo.list_instruments(self.source)
            if self.scope == "当前品种":
                items = [x for x in items if x.symbol == self.current_symbol]
            if not items:
                self.finished.emit(0, 0, 1)
                return
            total_jobs = len(items) * len(self.strategy_ids)
            done = results = failures = skipped = 0
            for meta in items:
                if self.cancelled:
                    break
                contract = self._contract_for(meta.symbol)
                if not contract:
                    skipped += len(self.strategy_ids)
                    done += len(self.strategy_ids)
                    self.progress.emit(done, total_jobs, f"{meta.display}：没有 {self.period} 数据")
                    continue
                try:
                    bars = self.repo.load_bars(self.source, meta.symbol, self.period, contract)
                except Exception as exc:
                    failures += len(self.strategy_ids)
                    done += len(self.strategy_ids)
                    self.progress.emit(done, total_jobs, f"{meta.display}：读取失败 {exc}")
                    continue
                for sid in self.strategy_ids:
                    if self.cancelled:
                        break
                    try:
                        result = run_backtest(
                            bars, meta.symbol, meta.display, contract, self.period, sid,
                            self.config,
                            None if self.all_history else self.start_date,
                            None if self.all_history else self.end_date,
                        )
                        payload = result.as_dict()
                        payload["source"] = self.source
                        self.resultReady.emit(payload)
                        results += 1
                    except ValueError:
                        skipped += 1
                    except Exception:
                        failures += 1
                    done += 1
                    self.progress.emit(done, total_jobs, f"{meta.display} / {STRATEGIES[sid]}")
            self.finished.emit(results, failures, skipped)
        except Exception as exc:
            self.failed.emit(str(exc))


class BacktestPanel(QWidget):
    """Backtest console hosted in the right-side panel."""

    # Selecting a result row asks MainWindow to display the corresponding bars
    # and buy/sell points in the left chart.
    resultActivated = Signal(dict)
    settingsChanged = Signal()

    def __init__(self, context_provider: Callable[[], dict], parent=None):
        super().__init__(parent)
        self.context_provider = context_provider
        self._thread: QThread | None = None
        self._worker: _BacktestWorker | None = None
        self._results: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        row1 = QHBoxLayout()
        self.scope = QComboBox(); self.scope.addItems(["当前品种", "全部品种"])
        self.strategy = QComboBox()
        # “开始回测”始终只运行这里选中的一个策略；全部策略由独立按钮执行。
        for sid, name in STRATEGIES.items():
            self.strategy.addItem(name, sid)
        self.period = QComboBox(); self.period.addItems(PERIODS); self.period.setCurrentText("1d")
        for label, widget in [("范围", self.scope), ("策略", self.strategy), ("周期", self.period)]:
            row1.addWidget(QLabel(label)); row1.addWidget(widget)
        row1.addStretch(1)
        root.addLayout(row1)

        # Detailed explanation updates immediately when a strategy is selected.
        self.strategy_desc = QLabel()
        self.strategy_desc.setWordWrap(True)
        self.strategy_desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.strategy_desc.setStyleSheet(
            "QLabel{background:#f8fafc;border:1px solid #d7dee8;border-radius:5px;"
            "padding:6px;color:#334155;}"
        )
        self.strategy_desc.setMinimumHeight(58)
        root.addWidget(self.strategy_desc)
        self.strategy.currentIndexChanged.connect(self._update_strategy_description)
        self._update_strategy_description()

        row2 = QHBoxLayout()
        self.all_history = QCheckBox("全部历史"); self.all_history.setChecked(True)
        self.start = QDateEdit(); self.start.setCalendarPopup(True); self.start.setDate(QDate.currentDate().addYears(-5))
        self.end = QDateEdit(); self.end.setCalendarPopup(True); self.end.setDate(QDate.currentDate())
        self.start.setEnabled(False); self.end.setEnabled(False)
        self.all_history.toggled.connect(lambda on: (self.start.setEnabled(not on), self.end.setEnabled(not on)))
        row2.addWidget(self.all_history); row2.addWidget(QLabel("起")); row2.addWidget(self.start); row2.addWidget(QLabel("止")); row2.addWidget(self.end)
        row2.addStretch(1)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        self.capital = QDoubleSpinBox(); self.capital.setRange(10_000, 1_000_000_000); self.capital.setDecimals(0); self.capital.setValue(1_000_000); self.capital.setSingleStep(100_000)
        self.qty = QSpinBox(); self.qty.setRange(1, 10000); self.qty.setValue(1)
        self.fee = QDoubleSpinBox(); self.fee.setRange(0, 100000); self.fee.setDecimals(2); self.fee.setValue(0); self.fee.setSuffix(" 元/手/边")
        self.slippage = QDoubleSpinBox(); self.slippage.setRange(0, 100000); self.slippage.setDecimals(4); self.slippage.setValue(0); self.slippage.setSuffix(" 价格")
        for label, widget in [("初始资金", self.capital), ("手数", self.qty), ("手续费", self.fee), ("滑点", self.slippage)]:
            row3.addWidget(QLabel(label)); row3.addWidget(widget)
        root.addLayout(row3)

        row4 = QHBoxLayout()
        self.btn_run = QPushButton("开始回测")
        self.btn_run.setStyleSheet("QPushButton{background:#dbeafe;padding:5px 12px;} QPushButton:hover{background:#bfdbfe;}")
        self.btn_current_all = QPushButton("当前品种全部策略")
        self.btn_current_all.setToolTip("不改变上面的范围/策略选择，直接对当前品种运行全部策略")
        self.btn_current_all.setStyleSheet("QPushButton{background:#dcfce7;padding:5px 10px;} QPushButton:hover{background:#bbf7d0;}")
        self.btn_cancel = QPushButton("停止"); self.btn_cancel.setEnabled(False)
        self.btn_clear = QPushButton("清空结果")
        self.btn_export = QPushButton("导出CSV")
        self.status = QLabel("就绪")
        for w in [self.btn_run, self.btn_current_all, self.btn_cancel, self.btn_clear, self.btn_export]:
            row4.addWidget(w)
        row4.addWidget(self.status, 1)
        root.addLayout(row4)

        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        root.addWidget(self.progress)

        cols = ["品种", "合约/序列", "策略", "周期", "起始", "结束", "K线数", "交易次数", "胜率", "净利润", "收益率", "最大回撤", "盈亏比", "平均每笔", "策略综合评分"]
        self.table = QTableWidget(0, len(cols)); self.table.setHorizontalHeaderLabels(cols)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        hdr = self.table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionsClickable(True)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        score_header = self.table.horizontalHeaderItem(14)
        if score_header is not None:
            score_header.setToolTip(
                "0-100分。综合收益率、收益/回撤、盈亏比、胜率和交易样本数；"
                "少于20笔交易会按样本可信度折减，1-2笔最高25分。用于横向比较策略，不代表未来收益。"
            )
        self.table.verticalHeader().setVisible(False)
        # Sorting is enabled for EVERY header. Numeric columns use a hidden numeric
        # sort key, so 95.10% correctly sorts above 11.46%, not lexicographically.
        self.table.setSortingEnabled(True)
        root.addWidget(self.table, 1)

        self.btn_run.clicked.connect(self._start)
        self.btn_current_all.clicked.connect(self._start_current_all)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_export.clicked.connect(self._export)
        self.table.itemSelectionChanged.connect(self._activate_selected_result)

        # Backtest UI state is part of the training session.  Column widths/order,
        # sort indicator, selected strategy and period survive application restart.
        for combo in (self.scope, self.strategy, self.period):
            combo.currentIndexChanged.connect(lambda _i: self.settingsChanged.emit())
        self.all_history.toggled.connect(lambda _v: self.settingsChanged.emit())
        self.start.dateChanged.connect(lambda _d: self.settingsChanged.emit())
        self.end.dateChanged.connect(lambda _d: self.settingsChanged.emit())
        for spin in (self.capital, self.qty, self.fee, self.slippage):
            spin.valueChanged.connect(lambda _v: self.settingsChanged.emit())
        hdr.sectionMoved.connect(lambda *_: self.settingsChanged.emit())
        hdr.sectionResized.connect(lambda *_: self.settingsChanged.emit())
        hdr.sortIndicatorChanged.connect(lambda *_: self.settingsChanged.emit())

    def state(self) -> dict:
        """Return the user-adjustable backtest panel state for session persistence."""
        hdr = self.table.horizontalHeader()
        order = [int(hdr.logicalIndex(v)) for v in range(hdr.count())]
        widths = [int(self.table.columnWidth(i)) for i in range(self.table.columnCount())]
        sort_col = int(hdr.sortIndicatorSection())
        sort_order = "desc" if hdr.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else "asc"
        return {
            "scope": self.scope.currentText(),
            "strategy": str(self.strategy.currentData() or ""),
            "period": self.period.currentText(),
            "all_history": self.all_history.isChecked(),
            "start": self.start.date().toString("yyyy-MM-dd"),
            "end": self.end.date().toString("yyyy-MM-dd"),
            "capital": float(self.capital.value()),
            "qty": int(self.qty.value()),
            "fee": float(self.fee.value()),
            "slippage": float(self.slippage.value()),
            "table": {
                "order": order,
                "widths": widths,
                "sort_column": sort_col,
                "sort_order": sort_order,
            },
        }

    def restore_state(self, state: dict | None):
        if not isinstance(state, dict):
            return
        # Block the panel's aggregate persistence signal while applying the saved state.
        widgets = [self.scope, self.strategy, self.period, self.all_history, self.start, self.end,
                   self.capital, self.qty, self.fee, self.slippage]
        old_blocks = [w.blockSignals(True) for w in widgets]
        try:
            scope = str(state.get("scope", ""))
            if self.scope.findText(scope) >= 0:
                self.scope.setCurrentText(scope)
            sid = str(state.get("strategy", ""))
            idx = self.strategy.findData(sid)
            if idx >= 0:
                self.strategy.setCurrentIndex(idx)
            period = str(state.get("period", ""))
            if self.period.findText(period) >= 0:
                self.period.setCurrentText(period)
            if "all_history" in state:
                self.all_history.setChecked(bool(state.get("all_history")))
            for key, widget in (("start", self.start), ("end", self.end)):
                text = str(state.get(key, ""))
                qd = QDate.fromString(text, "yyyy-MM-dd")
                if qd.isValid():
                    widget.setDate(qd)
            if state.get("capital") is not None:
                self.capital.setValue(float(state.get("capital")))
            if state.get("qty") is not None:
                self.qty.setValue(int(state.get("qty")))
            if state.get("fee") is not None:
                self.fee.setValue(float(state.get("fee")))
            if state.get("slippage") is not None:
                self.slippage.setValue(float(state.get("slippage")))
        finally:
            for w, old in zip(widgets, old_blocks):
                w.blockSignals(old)

        self.start.setEnabled(not self.all_history.isChecked())
        self.end.setEnabled(not self.all_history.isChecked())
        self._update_strategy_description()

        table_state = state.get("table") if isinstance(state.get("table"), dict) else {}
        hdr = self.table.horizontalHeader()
        old_hdr = hdr.blockSignals(True)
        try:
            order = list(table_state.get("order") or [])
            # Backward compatibility: sessions saved before the score column had 14 columns.
            if len(order) == self.table.columnCount() - 1 and sorted(order) == list(range(self.table.columnCount() - 1)):
                order.append(self.table.columnCount() - 1)
            if len(order) == self.table.columnCount() and sorted(order) == list(range(self.table.columnCount())):
                for target_visual, logical in enumerate(order):
                    current_visual = hdr.visualIndex(int(logical))
                    if current_visual != target_visual:
                        hdr.moveSection(current_visual, target_visual)
            widths = list(table_state.get("widths") or [])
            if len(widths) == self.table.columnCount() - 1:
                widths.append(self.table.columnWidth(self.table.columnCount() - 1))
            if len(widths) == self.table.columnCount():
                for logical, width in enumerate(widths):
                    self.table.setColumnWidth(logical, max(24, int(width)))
            col = int(table_state.get("sort_column", hdr.sortIndicatorSection()))
            if not 0 <= col < self.table.columnCount():
                col = 0
            order_enum = (Qt.SortOrder.DescendingOrder
                          if str(table_state.get("sort_order", "asc")).lower() == "desc"
                          else Qt.SortOrder.AscendingOrder)
            hdr.setSortIndicator(col, order_enum)
            self.table.sortItems(col, order_enum)
        finally:
            hdr.blockSignals(old_hdr)

    def _update_strategy_description(self):
        sid = str(self.strategy.currentData() or "")
        self.strategy_desc.setText(STRATEGY_DESCRIPTIONS.get(sid, STRATEGIES.get(sid, "")))

    def _build_config(self) -> BacktestConfig:
        return BacktestConfig(
            initial_capital=float(self.capital.value()), qty=int(self.qty.value()),
            commission_per_side=float(self.fee.value()), slippage=float(self.slippage.value()),
        )

    def _start_current_all(self):
        self._start(scope_override="当前品种", strategy_ids_override=list(STRATEGIES))

    def _start(self, _checked=False, scope_override: str | None = None,
               strategy_ids_override: list[str] | None = None):
        if self._thread is not None:
            return
        ctx = self.context_provider() or {}
        repo = ctx.get("repo")
        if repo is None:
            QMessageBox.information(self, "回测", "请先选择行情数据目录。")
            return
        symbol = str(ctx.get("symbol") or "")
        scope = scope_override or self.scope.currentText()
        if scope == "当前品种" and not symbol:
            QMessageBox.information(self, "回测", "请先选择一个品种。")
            return
        if strategy_ids_override is not None:
            strategy_ids = list(strategy_ids_override)
        else:
            sid = str(self.strategy.currentData() or "")
            if not sid or sid not in STRATEGIES:
                QMessageBox.information(self, "回测", "请选择一个回测策略。")
                return
            # “开始回测”只运行当前选中的一个策略。
            strategy_ids = [sid]
        cfg = self._build_config()
        start = self.start.date().toString("yyyy-MM-dd")
        end = self.end.date().toString("yyyy-MM-dd")
        if not self.all_history.isChecked() and start > end:
            QMessageBox.warning(self, "回测", "开始日期不能晚于结束日期。")
            return

        self.btn_run.setEnabled(False); self.btn_current_all.setEnabled(False)
        self.btn_cancel.setEnabled(True); self.progress.setValue(0)
        self.status.setText("准备回测…")
        thread = QThread(self)
        worker = _BacktestWorker(
            repo, str(ctx.get("source") or "原始行情"), scope, symbol,
            str(ctx.get("contract") or ""), self.period.currentText(), strategy_ids, cfg,
            self.all_history.isChecked(), start, end,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.resultReady.connect(self._append_result)
        worker.progress.connect(self._progress)
        worker.finished.connect(self._finished)
        worker.failed.connect(self._failed)
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater); thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._thread_done)
        self._thread, self._worker = thread, worker
        thread.start()

    def _cancel(self):
        if self._worker is not None:
            self._worker.cancelled = True
            self.status.setText("正在停止…")

    @staticmethod
    def _display_and_sort(col: int, value):
        if col in {6, 7}:
            n = int(value)
            return f"{n:,}", n
        if col in {8, 10, 11}:
            n = float(value)
            return f"{n:.2f}%", n
        if col in {9, 13}:
            n = float(value)
            return f"{n:,.2f}", n
        if col == 12:
            n = float(value)
            return ("∞" if n >= 999 else f"{n:.2f}"), n
        if col == 14:
            n = float(value)
            return f"{n:.2f}", n
        if col == 3:
            text = str(value)
            try:
                return text, PERIODS.index(text)
            except ValueError:
                return text, len(PERIODS) + 1
        return str(value), str(value)

    @Slot(dict)
    def _append_result(self, r: dict):
        result_index = len(self._results)
        self._results.append(dict(r))
        # Keep current sort column/order but temporarily disable sorting while a
        # complete row is inserted. Re-enabling then restores the selected sort.
        self.table.setSortingEnabled(False)
        row = self.table.rowCount(); self.table.insertRow(row)
        vals = [
            r["display_name"], r["contract"], r["strategy"], r["period"], r["start"], r["end"],
            r["bars"], r["trades"], r["win_rate"], r["net_profit"], r["return_pct"],
            r["max_drawdown_pct"], r["profit_factor"], r["avg_trade"], r.get("composite_score", 0.0),
        ]
        numeric_cols = {6, 7, 8, 9, 10, 11, 12, 13, 14}
        for col, val in enumerate(vals):
            text, sort_key = self._display_and_sort(col, val)
            item = _SortableItem(text, sort_key, result_index)
            if col in numeric_cols:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)

    def _activate_selected_result(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return
        row = rows[0].row()
        item = self.table.item(row, 0)
        if item is None:
            # Column 0 may be visually moved, but its logical item still exists;
            # this is only a defensive fallback.
            for c in range(self.table.columnCount()):
                item = self.table.item(row, c)
                if item is not None:
                    break
        if item is None:
            return
        try:
            idx = int(item.data(RESULT_ROLE))
        except Exception:
            return
        if 0 <= idx < len(self._results):
            self.resultActivated.emit(dict(self._results[idx]))

    @Slot(int, int, str)
    def _progress(self, done: int, total: int, text: str):
        self.progress.setValue(int(done * 100 / max(1, total)))
        self.status.setText(f"{done}/{total}  {text}")

    @Slot(int, int, int)
    def _finished(self, results: int, failures: int, skipped: int):
        self.progress.setValue(100)
        self.status.setText(f"完成：结果 {results}，失败 {failures}，跳过 {skipped}")

    @Slot(str)
    def _failed(self, text: str):
        self.status.setText("回测失败")
        QMessageBox.critical(self, "回测失败", text)

    @Slot()
    def _thread_done(self):
        self._thread = None; self._worker = None
        self.btn_run.setEnabled(True); self.btn_current_all.setEnabled(True); self.btn_cancel.setEnabled(False)

    def stop_and_wait(self, timeout_ms: int = 5000) -> bool:
        """Cooperatively stop a running backtest before the application exits."""
        if self._thread is None:
            return True
        if self._worker is not None:
            self._worker.cancelled = True
        self.status.setText("正在停止回测…")
        # Request the worker thread's event loop to exit as soon as run() returns;
        # this is important because the GUI thread may be blocked in wait().
        self._thread.requestInterruption()
        self._thread.quit()
        return bool(self._thread.wait(max(0, int(timeout_ms))))

    def reset_session_state(self):
        """Clear transient results without changing the strategy definitions."""
        if self._thread is not None:
            return
        self._results.clear()
        self.table.setRowCount(0)
        self.progress.setValue(0)
        self.status.setText("就绪")

    def _clear(self):
        self.reset_session_state()

    def _export(self):
        if not self._results:
            QMessageBox.information(self, "导出", "当前没有回测结果。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出回测结果", "回测结果.csv", "CSV (*.csv)")
        if not path:
            return
        # Markers are chart annotations and can be very large; keep them out of
        # the summary CSV. All statistical columns and source remain exportable.
        keys = [k for k in self._results[0].keys() if k != "markers"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader(); writer.writerows(self._results)
        self.status.setText(f"已导出：{Path(path).name}")
