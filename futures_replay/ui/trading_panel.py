from __future__ import annotations

from fractions import Fraction

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_INITIAL_CAPITAL
from ..models import Side
from ..trading import TradingAccount


class TradingPanel(QWidget):
    actionRequested = Signal(str, int)
    depositRequested = Signal(float)
    resetAccountRequested = Signal()
    fractionRequested = Signal(float)
    capitalSizingRequested = Signal(float)
    quantityEdited = Signal()
    cancelOrderRequested = Signal(object)
    cancelAllRequested = Signal()
    ratiosChanged = Signal()
    tableLayoutChanged = Signal()
    positionSymbolClicked = Signal(str)
    tradeRecordSymbolClicked = Signal(str)

    DEFAULT_FRACTIONS = ["1/4", "1/3", "1/2", "4/5", "全仓"]
    DEFAULT_CAPITAL_BUDGET = 1_000_000.0
    CAPITAL_ORDER_TOKEN = "__fixed_capital__"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.account: TradingAccount | None = None
        self.mark = 0.0
        self.current_symbol = ""
        self.symbol_labels: dict[str, str] = {}
        self._fraction_specs = list(self.DEFAULT_FRACTIONS)
        # When a quick-position ratio is selected, keep it active so the order
        # quantity can be recalculated from the latest available funds after
        # opening/closing/reversing positions and as the replay mark changes.
        # A manual edit of the quantity field cancels this automatic ratio mode.
        self._active_fraction_ratio: float | None = None
        self._active_fraction_text = ""
        # The first quick-size button uses a fixed reference capital rather than
        # a percentage of current available funds.  Default: RMB 1,000,000.
        self._capital_budget = float(self.DEFAULT_CAPITAL_BUDGET)
        self._active_capital_sizing = False
        self._capital_button: QPushButton | None = None
        self._fraction_buttons: list[tuple[float, QPushButton]] = []
        # The fixed-capital button participates in the same drag order as the
        # ratio buttons.  Keep the visual order separate from _fraction_specs so
        # editing ratio values does not accidentally turn the capital button into
        # a ratio entry.
        self._quick_button_order: list[str] = [
            self.CAPITAL_ORDER_TOKEN, *self._fraction_specs
        ]
        self._quick_buttons: list[QPushButton] = []
        self._fraction_drag_button: QPushButton | None = None
        self._fraction_drag_start = QPoint()
        self._fraction_dragging = False
        self._updating_qty_programmatically = False
        # Cache record content so K-line playback does not rebuild the record
        # tables on every timer tick. This keeps the vertical scrollbars fully
        # independent from playback and makes them easy to drag while playing.
        self._records_signature = None
        self._restoring_table_layout = False
        # Remember the user's selected holding even when playback refreshes the
        # table.  The Close button can then act on that selected instrument
        # instead of always acting on the currently visible K-line instrument.
        self._selected_position_key = ""

        # Right-side trading panel uses restrained semantic colors so different
        # actions and account figures can be distinguished at a glance.
        self.setObjectName("tradingPanel")
        self.setStyleSheet("""
            QWidget#tradingPanel { background: #f8fafc; }
            QGroupBox {
                font-weight: 600; border: 1px solid #d7dee8; border-radius: 6px;
                margin-top: 8px; padding-top: 8px; background: #ffffff;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; }
            QTableWidget {
                background: #ffffff; alternate-background-color: #f8fafc;
                gridline-color: #e5e7eb; selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QHeaderView::section {
                background: #eef2f7; color: #334155; padding: 4px;
                border: 0px; border-right: 1px solid #d7dee8; border-bottom: 1px solid #d7dee8;
                font-weight: 600;
            }
            QPushButton { min-height: 22px; border-radius: 5px; padding: 2px 7px; }
            QPushButton#depositButton { background: #dbeafe; color: #1d4ed8; border: 1px solid #93c5fd; }
            QPushButton#resetAccountButton { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
            QPushButton#longButton { background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; }
            QPushButton#shortButton { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
            QPushButton#closeButton { background: #e2e8f0; color: #334155; border: 1px solid #cbd5e1; }
            QPushButton#closeAllButton, QPushButton#closeSelectedButton { background: #ffedd5; color: #c2410c; border: 1px solid #fdba74; }
            QPushButton#reverseLongButton { background: #fce7f3; color: #be185d; border: 1px solid #f9a8d4; }
            QPushButton#reverseShortButton { background: #ecfccb; color: #4d7c0f; border: 1px solid #bef264; }
            QPushButton#ratioButton { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
            QPushButton#ratioButton:checked { background: #dbeafe; color: #1d4ed8; border: 1px solid #60a5fa; font-weight: 700; }
            QPushButton#ratioSettingButton { background: #ede9fe; color: #6d28d9; border: 1px solid #c4b5fd; }
            QPushButton#cancelButton { background: #fff7ed; color: #c2410c; border: 1px solid #fdba74; }
        """)

        # Compact horizontal order-entry layout.  When the dock is floated this
        # reads as a wide, low strip instead of a tall right-hand panel.  All
        # existing widgets/attributes are kept so the trading logic and saved
        # table layouts remain backward-compatible.
        self.setMinimumSize(720, 245)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # Account figures form one shallow row across the top.
        money = QGroupBox("账户")
        money.setMaximumHeight(62)
        account_row = QHBoxLayout(money)
        account_row.setContentsMargins(8, 7, 8, 5)
        account_row.setSpacing(8)
        self.lbl_equity = QLabel("--")
        self.lbl_available = QLabel("--")
        # ``lbl_margin`` is intentionally kept as a separate widget for
        # compatibility with the existing refresh logic, but it now lives below
        # the order buttons rather than in the top account strip.
        self.lbl_margin = QLabel("--")
        self.lbl_realized = QLabel("--")
        self.lbl_realized.setToolTip("累计盈亏 = 已实现盈亏 + 当前全部持仓浮动盈亏；括号内收益率 = 累计盈亏 ÷ 初始资金")
        for name, lab in [
            ("总资金", self.lbl_equity), ("可用", self.lbl_available),
            ("累计盈亏", self.lbl_realized),
        ]:
            title = QLabel(name)
            title.setStyleSheet("color:#64748b;")
            account_row.addWidget(title)
            account_row.addWidget(lab)
            account_row.addSpacing(5)
        self.lbl_equity.setStyleSheet("color:#1d4ed8; font-weight:600;")
        self.lbl_available.setStyleSheet("color:#0f766e; font-weight:600;")
        self.lbl_margin.setStyleSheet("color:#b45309; font-weight:700;")
        self.lbl_margin.setToolTip("当前界面正在显示的品种/合约按当前价开1手所需保证金；与是否已有持仓无关")
        account_row.addStretch(1)
        self.btn_deposit = QPushButton("入金")
        self.btn_deposit.setObjectName("depositButton")
        self.btn_deposit.setFixedWidth(56)
        self.btn_deposit.clicked.connect(self._deposit_dialog)
        account_row.addWidget(self.btn_deposit)
        self.btn_reset_account = QPushButton("清账户")
        self.btn_reset_account.setObjectName("resetAccountButton")
        self.btn_reset_account.setFixedWidth(66)
        self.btn_reset_account.setToolTip("清除持仓和挂单，并恢复为初始账户资金；不会删除行情数据和复盘位置")
        self.btn_reset_account.clicked.connect(self.resetAccountRequested.emit)
        account_row.addWidget(self.btn_reset_account)
        root.addWidget(money)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)

        # The wide/low strip follows the user's preferred layout: positions and
        # records occupy the larger LEFT side, while the compact order-entry
        # controls stay on the RIGHT side.
        orderbox = QGroupBox("下单")
        orderbox.setMinimumWidth(300)
        orderbox.setMaximumWidth(390)
        ov = QVBoxLayout(orderbox)
        ov.setContentsMargins(8, 8, 8, 7)
        ov.setSpacing(5)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel("手数"))
        self.qty = QSpinBox()
        self.qty.setRange(1, 1_000_000)
        self.qty.setValue(1)
        self.qty.valueChanged.connect(self._qty_value_changed)
        self.qty.setFixedWidth(88)
        row.addWidget(self.qty)

        self.btn_ratio_capital_setting = QPushButton("比例/资金设置")
        self.btn_ratio_capital_setting.setMinimumWidth(126)
        self.btn_ratio_capital_setting.setToolTip(
            "同时设置固定资金金额和下面的快捷仓位比例"
        )
        self.btn_ratio_capital_setting.clicked.connect(self._edit_fractions)
        row.addWidget(self.btn_ratio_capital_setting)
        row.addStretch(1)
        ov.addLayout(row)

        self._fraction_layout = QHBoxLayout()
        self._fraction_layout.setSpacing(4)
        ov.addLayout(self._fraction_layout)
        self._rebuild_fraction_buttons()

        br = QGridLayout()
        br.setContentsMargins(0, 0, 0, 0)
        br.setHorizontalSpacing(5)
        br.setVerticalSpacing(4)
        # Keep the order area low by using three columns.  The selected-product
        # full-close command closes the whole highlighted holding without touching
        # positions in other products.
        trade_buttons = [
            ("买入开多", "开多", "longButton", 0, 0),
            ("卖出开空", "开空", "shortButton", 0, 1),
            ("平仓", "平仓", "closeButton", 0, 2),
            ("反手做多", "反多", "reverseLongButton", 1, 0),
            ("反手做空", "反空", "reverseShortButton", 1, 1),
            ("全平所选", "全平所选", "closeSelectedButton", 1, 2),
        ]
        for text, action, object_name, grid_row, grid_col in trade_buttons:
            b = QPushButton(text)
            b.setObjectName(object_name)
            if action == "全平所选":
                b.setToolTip("一次性平掉持仓表中当前选中的品种/合约全部手数")
            b.clicked.connect(lambda _=False, a=action: self.actionRequested.emit(a, self.qty.value()))
            br.addWidget(b, grid_row, grid_col)
        close_all = QPushButton("全部平仓")
        close_all.setObjectName("closeAllButton")
        close_all.setToolTip("平掉账户内全部品种的全部持仓")
        close_all.clicked.connect(lambda _=False: self.actionRequested.emit("全部平仓", self.qty.value()))
        br.addWidget(close_all, 2, 0, 1, 3)
        br.setColumnStretch(0, 1)
        br.setColumnStretch(1, 1)
        br.setColumnStretch(2, 1)
        ov.addLayout(br)
        # Keep a visible gap between the order buttons and the instrument summary.
        ov.addSpacing(8)

        # Current-instrument margin belongs with order entry: it tells the user
        # what one new contract of the instrument shown on the main chart would
        # require, independent of any existing position.
        margin_row = QHBoxLayout()
        margin_row.setContentsMargins(2, 1, 2, 0)
        margin_row.setSpacing(5)
        self.lbl_current_product = QLabel("当前品种：--")
        self.lbl_current_product.setStyleSheet("color:#334155; font-weight:600;")
        self.lbl_current_product.setToolTip("主窗口当前正在显示的品种/合约")
        margin_title = QLabel("1手保证金")
        margin_title.setStyleSheet("color:#64748b; font-weight:700;")
        margin_row.addWidget(self.lbl_current_product, 1)
        margin_row.addWidget(margin_title)
        margin_row.addWidget(self.lbl_margin)
        ov.addLayout(margin_row)

        # Show account-wide open lots before the account-wide floating P/L.
        # Both values are emphasized for quick reading; P/L color follows the
        # existing convention (profit red, loss green).
        position_pnl_row = QHBoxLayout()
        position_pnl_row.setContentsMargins(2, 1, 2, 0)
        position_pnl_row.setSpacing(6)
        position_lots_title = QLabel("持仓总手数")
        position_lots_title.setStyleSheet("color:#334155; font-size:10pt; font-weight:700;")
        self.lbl_position_lots = QLabel("0 手")
        self.lbl_position_lots.setStyleSheet("color:#0369a1; font-size:11pt; font-weight:800;")
        self.lbl_position_lots.setToolTip("当前账户全部未平仓持仓的手数合计；按各持仓手数相加")
        position_pnl_title = QLabel("持仓总盈亏")
        position_pnl_title.setStyleSheet("color:#334155; font-size:10pt; font-weight:700;")
        self.lbl_position_pnl = QLabel("--")
        self.lbl_position_pnl.setStyleSheet("color:#64748b; font-size:11pt; font-weight:800;")
        self.lbl_position_pnl.setToolTip("当前账户全部未平仓持仓的浮动盈亏合计，不包含已经平仓的已实现盈亏")
        position_pnl_row.addWidget(position_lots_title)
        position_pnl_row.addWidget(self.lbl_position_lots)
        position_pnl_row.addSpacing(10)
        position_pnl_row.addWidget(position_pnl_title)
        position_pnl_row.addWidget(self.lbl_position_pnl)
        position_pnl_row.addStretch(1)
        ov.addLayout(position_pnl_row)
        ov.addStretch(1)

        # Left side: positions above the record tabs, separated by a draggable
        # horizontal splitter. The user can freely change the height ratio and
        # the splitter position is persisted with the table layout state.
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(6)

        posbox = QGroupBox("持仓")
        posbox.setMinimumHeight(72)
        pv = QVBoxLayout(posbox)
        pv.setContentsMargins(5, 7, 5, 5)
        self.position = QTableWidget(0, 7)
        self.position.setHorizontalHeaderLabels(["品种", "方向", "手数", "均价", "持仓保证金", "浮盈", "可买/持仓"])
        self.position.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.position.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.position.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.position.cellClicked.connect(self._position_cell_clicked)
        self._enable_column_reorder(self.position)
        self.position.verticalHeader().setVisible(False)
        self.position.setMinimumHeight(48)
        pv.addWidget(self.position)
        self.left_splitter.addWidget(posbox)

        self.record_tabs = QTabWidget()
        self.record_tabs.setMinimumHeight(82)

        fills_tab = QWidget()
        fv = QVBoxLayout(fills_tab)
        fv.setContentsMargins(2, 2, 2, 2)
        self.fills = QTableWidget(0, 6)
        self.fills.setHorizontalHeaderLabels(["时间", "品种", "成交说明", "成交价", "手数", "实现盈亏"])
        self.fills.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fills.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fills.cellClicked.connect(self._fill_cell_clicked)
        self.fills.verticalHeader().setVisible(False)
        self._enable_column_reorder(self.fills)
        fv.addWidget(self.fills)
        self.record_tabs.addTab(fills_tab, "成交记录")

        history_tab = QWidget()
        hv = QVBoxLayout(history_tab)
        hv.setContentsMargins(2, 2, 2, 2)
        hv.setSpacing(3)
        self.order_history = QTableWidget(0, 7)
        self.order_history.setHorizontalHeaderLabels(["时间", "品种", "ID", "委托", "价格", "手数", "状态"])
        self.order_history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.order_history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.order_history.verticalHeader().setVisible(False)
        self._enable_column_reorder(self.order_history)
        hv.addWidget(self.order_history)
        qr = QHBoxLayout()
        qr.setContentsMargins(0, 0, 0, 0)
        qr.setSpacing(4)
        cancel = QPushButton("撤销选中")
        cancel_all = QPushButton("全部撤销")
        cancel.setObjectName("cancelButton")
        cancel_all.setObjectName("cancelButton")
        cancel.clicked.connect(self._cancel_selected)
        cancel_all.clicked.connect(self.cancelAllRequested.emit)
        qr.addStretch(1)
        qr.addWidget(cancel)
        qr.addWidget(cancel_all)
        hv.addLayout(qr)
        self.record_tabs.addTab(history_tab, "委托记录")

        self.left_splitter.addWidget(self.record_tabs)
        self.left_splitter.setStretchFactor(0, 2)
        self.left_splitter.setStretchFactor(1, 3)
        self.left_splitter.setSizes([110, 150])
        self.left_splitter.splitterMoved.connect(self._table_layout_changed)

        body.addWidget(self.left_splitter, 1)
        body.addWidget(orderbox, 0)
        root.addLayout(body, 1)

    def _enable_column_reorder(self, table: QTableWidget):
        """Enable draggable/reorderable columns and persist user layout changes.

        * Drag a separator/edge between two headers to resize that column.
        * Drag from the middle of a header cell to move/reorder the whole column.

        Only the visual order changes. Logical column indexes remain stable, so
        trading code can continue to address columns by their original indexes.
        """
        header = table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setSectionsClickable(True)
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        header.setDefaultSectionSize(90)
        header.sectionMoved.connect(self._table_layout_changed)
        header.sectionResized.connect(self._table_layout_changed)

    def _table_layout_changed(self, *_args):
        if not self._restoring_table_layout:
            self.tableLayoutChanged.emit()

    @staticmethod
    def _header_state(table: QTableWidget) -> dict:
        header = table.horizontalHeader()
        count = table.columnCount()
        # visual_order stores logical column indexes from left to right. Widths
        # are keyed by logical index, so a moved column keeps its own width.
        return {
            "visual_order": [int(header.logicalIndex(v)) for v in range(count)],
            "widths": [int(header.sectionSize(logical)) for logical in range(count)],
        }

    @staticmethod
    def _restore_header_state(table: QTableWidget, state) -> None:
        if not isinstance(state, dict):
            return
        header = table.horizontalHeader()
        count = table.columnCount()

        widths = state.get("widths", [])
        if isinstance(widths, (list, tuple)):
            for logical, width in enumerate(widths[:count]):
                try:
                    header.resizeSection(logical, max(header.minimumSectionSize(), int(width)))
                except Exception:
                    pass

        order = state.get("visual_order", [])
        if not isinstance(order, (list, tuple)):
            return
        desired = []
        for value in order:
            try:
                logical = int(value)
            except Exception:
                continue
            if 0 <= logical < count and logical not in desired:
                desired.append(logical)
        desired.extend(logical for logical in range(count) if logical not in desired)
        for target_visual, logical in enumerate(desired):
            current_visual = header.visualIndex(logical)
            if current_visual >= 0 and current_visual != target_visual:
                header.moveSection(current_visual, target_visual)

    def table_layout_state(self) -> dict:
        """Return column widths/order for all right-panel tables."""
        return {
            "positions": self._header_state(self.position),
            "fills": self._header_state(self.fills),
            "orders": self._header_state(self.order_history),
            "left_splitter_sizes": [int(x) for x in self.left_splitter.sizes()],
        }

    def restore_table_layout_state(self, state) -> None:
        """Restore widths/order saved from the previous application session."""
        if not isinstance(state, dict):
            return
        self._restoring_table_layout = True
        try:
            self._restore_header_state(self.position, state.get("positions"))
            self._restore_header_state(self.fills, state.get("fills"))
            self._restore_header_state(self.order_history, state.get("orders"))
            sizes = state.get("left_splitter_sizes")
            if isinstance(sizes, (list, tuple)) and len(sizes) >= 2:
                try:
                    a, b = max(40, int(sizes[0])), max(40, int(sizes[1]))
                    self.left_splitter.setSizes([a, b])
                except Exception:
                    pass
        finally:
            self._restoring_table_layout = False

    # -------------------------- configurable fractions --------------------------
    @staticmethod
    def _parse_fraction(text: str) -> float:
        t = text.strip()
        if t == "全仓":
            return 1.0
        if t.endswith("%"):
            return float(t[:-1]) / 100.0
        if "/" in t:
            return float(Fraction(t))
        value = float(t)
        # Convenience: 20 means 20%, while 0.2 means 20% directly.
        return value / 100.0 if value > 1 else value

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _capital_button_text(self) -> str:
        amount = max(0.0, float(self._capital_budget))
        if amount >= 10_000:
            wan = amount / 10_000.0
            if abs(wan - round(wan)) < 1e-9:
                shown = f"{int(round(wan))}万"
            else:
                shown = f"{wan:.1f}万"
        else:
            shown = f"{amount:,.0f}元"
        return f"{shown}手数"

    def _normalise_quick_button_order(self) -> None:
        """Keep one capital token plus every current ratio exactly once."""
        valid_ratios = list(self._fraction_specs)
        valid_keys = {self.CAPITAL_ORDER_TOKEN, *valid_ratios}
        ordered: list[str] = []
        for key in self._quick_button_order:
            key = str(key)
            if key in valid_keys and key not in ordered:
                ordered.append(key)
        if self.CAPITAL_ORDER_TOKEN not in ordered:
            ordered.insert(0, self.CAPITAL_ORDER_TOKEN)
        for text in valid_ratios:
            if text not in ordered:
                ordered.append(text)
        self._quick_button_order = ordered

    def _make_capital_button(self) -> QPushButton:
        capital = QPushButton(self._capital_button_text())
        capital.setObjectName("ratioButton")
        capital.setCheckable(True)
        capital.setChecked(bool(self._active_capital_sizing))
        capital.setToolTip(
            f"按固定资金 {self._capital_budget:,.0f} 元、当前价格、合约乘数和保证金率计算可开手数；"
            "该基准资金不随账户可用资金变化。可在‘比例/资金设置’中修改。"
            " 按住按钮左右拖动可调整位置。"
        )
        capital.setProperty("quick_order_key", self.CAPITAL_ORDER_TOKEN)
        capital.installEventFilter(self)
        capital.clicked.connect(self._capital_clicked)
        self._capital_button = capital
        return capital

    def _make_fraction_button(self, text: str) -> tuple[float, QPushButton] | None:
        try:
            ratio = min(1.0, self._parse_fraction(text))
        except Exception:
            return None
        if ratio <= 0:
            return None
        b = QPushButton(text)
        b.setObjectName("ratioButton")
        b.setCheckable(True)
        if self._active_fraction_ratio is not None and abs(ratio - self._active_fraction_ratio) < 1e-12:
            b.setChecked(True)
            self._active_fraction_text = text
        b.setProperty("fraction_text", text)
        b.setProperty("quick_order_key", text)
        b.installEventFilter(self)
        b.clicked.connect(lambda _checked=False, r=ratio, t=text: self._fraction_clicked(r, t))
        return ratio, b

    def _rebuild_fraction_buttons(self):
        self._clear_layout(self._fraction_layout)
        self._fraction_buttons = []
        self._quick_buttons = []
        self._capital_button = None
        self._normalise_quick_button_order()

        active_found = False
        for key in self._quick_button_order:
            if key == self.CAPITAL_ORDER_TOKEN:
                button = self._make_capital_button()
                self._quick_buttons.append(button)
                self._fraction_layout.addWidget(button)
                continue

            made = self._make_fraction_button(key)
            if made is None:
                continue
            ratio, button = made
            if (
                self._active_fraction_ratio is not None
                and abs(ratio - self._active_fraction_ratio) < 1e-12
            ):
                active_found = True
            self._fraction_buttons.append((ratio, button))
            self._quick_buttons.append(button)
            self._fraction_layout.addWidget(button)

        if self._active_fraction_ratio is not None and not active_found:
            self._active_fraction_ratio = None
            self._active_fraction_text = ""

    def eventFilter(self, watched, event):
        """Drag fixed-capital/ratio quick buttons left or right and save the order."""
        if isinstance(watched, QPushButton) and watched.property("quick_order_key") is not None:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._fraction_drag_button = watched
                self._fraction_drag_start = event.position().toPoint()
                self._fraction_dragging = False
            elif et == QEvent.Type.MouseMove and self._fraction_drag_button is watched:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    dist = (event.position().toPoint() - self._fraction_drag_start).manhattanLength()
                    if dist >= QApplication.startDragDistance():
                        self._fraction_dragging = True
                        watched.setCursor(Qt.CursorShape.ClosedHandCursor)
            elif et == QEvent.Type.MouseButtonRelease and self._fraction_drag_button is watched:
                was_dragging = self._fraction_dragging
                self._fraction_drag_button = None
                self._fraction_dragging = False
                watched.unsetCursor()
                if was_dragging:
                    source_key = str(watched.property("quick_order_key") or "")
                    if source_key in self._quick_button_order:
                        source_index = self._quick_button_order.index(source_key)
                        global_pos = watched.mapToGlobal(event.position().toPoint())

                        centers: list[tuple[int, str]] = []
                        for btn in self._quick_buttons:
                            key = str(btn.property("quick_order_key") or "")
                            if not key:
                                continue
                            center = btn.mapToGlobal(btn.rect().center())
                            centers.append((center.x(), key))

                        if centers:
                            target_key = min(
                                centers, key=lambda item: abs(item[0] - global_pos.x())
                            )[1]
                            if target_key in self._quick_button_order:
                                target_index = self._quick_button_order.index(target_key)
                                key = self._quick_button_order.pop(source_index)
                                # After removing the source, a target originally
                                # to the right shifts one place left.
                                if source_index < target_index:
                                    target_index -= 1
                                target_index = max(
                                    0, min(target_index, len(self._quick_button_order))
                                )
                                self._quick_button_order.insert(target_index, key)
                                self._rebuild_fraction_buttons()
                                self.ratiosChanged.emit()
                    # A drag must never also trigger the button action.
                    return True
        return super().eventFilter(watched, event)

    def _capital_clicked(self, _checked: bool = False):
        self._active_capital_sizing = True
        self._active_fraction_ratio = None
        self._active_fraction_text = ""
        if self._capital_button is not None:
            self._capital_button.setChecked(True)
        for _ratio, button in self._fraction_buttons:
            button.setChecked(False)
        self.capitalSizingRequested.emit(float(self._capital_budget))

    def _fraction_clicked(self, ratio: float, text: str):
        self._active_capital_sizing = False
        if self._capital_button is not None:
            self._capital_button.setChecked(False)
        self._active_fraction_ratio = float(ratio)
        self._active_fraction_text = str(text)
        for button_ratio, button in self._fraction_buttons:
            button.setChecked(abs(button_ratio - ratio) < 1e-12)
        self.fractionRequested.emit(float(ratio))

    def _qty_value_changed(self, _value: int):
        if self._updating_qty_programmatically:
            return
        # User changed the spin box manually (typing, wheel, or arrow buttons).
        # Stop automatic ratio sizing until a quick ratio is chosen again.
        if self._active_fraction_ratio is not None or self._active_capital_sizing:
            self.clear_fraction_selection()
        self.quantityEdited.emit()

    def clear_fraction_selection(self):
        self._active_fraction_ratio = None
        self._active_fraction_text = ""
        self._active_capital_sizing = False
        if self._capital_button is not None:
            self._capital_button.setChecked(False)
        for _ratio, button in self._fraction_buttons:
            button.setChecked(False)

    def active_fraction_ratio(self) -> float | None:
        return self._active_fraction_ratio

    def is_capital_sizing_active(self) -> bool:
        return bool(self._active_capital_sizing)

    def capital_budget(self) -> float:
        return float(self._capital_budget)

    def set_capital_budget(self, amount: float) -> None:
        try:
            value = float(amount)
        except Exception:
            return
        if value <= 0:
            return
        self._capital_budget = value
        self._rebuild_fraction_buttons()

    def refresh_qty_from_capital(
        self, qty_for_budget: int, per_lot_margin: float, account_max_qty: int
    ) -> None:
        """Set quantity from the fixed reference-capital quick button."""
        if not self._active_capital_sizing:
            return
        qty = max(1, int(qty_for_budget)) if int(qty_for_budget) > 0 else 1
        self._updating_qty_programmatically = True
        try:
            self.qty.setValue(qty)
        finally:
            self._updating_qty_programmatically = False
        self.qty.setToolTip(
            f"固定资金 {self._capital_budget:,.0f} 元；1手保证金约 {float(per_lot_margin):,.2f} 元；"
            f"按固定资金可开 {int(qty_for_budget)} 手；当前账户可用资金最多可开 {int(account_max_qty)} 手"
        )

    def refresh_qty_from_capacity(self, max_open_qty: int):
        """Refresh order quantity from the latest available-funds capacity.

        If a quick ratio is active, quantity is recalculated immediately from
        ``max_open_qty``.  With no active ratio, a manually entered quantity is
        preserved; only the capacity tooltip is refreshed.
        """
        mx = max(0, int(max_open_qty))
        if self._active_capital_sizing:
            return
        if self._active_fraction_ratio is not None:
            qty = max(1, int(mx * self._active_fraction_ratio)) if mx > 0 else 1
            self._updating_qty_programmatically = True
            try:
                self.qty.setValue(qty)
            finally:
                self._updating_qty_programmatically = False
            ratio_text = self._active_fraction_text or f"{self._active_fraction_ratio:.0%}"
            self.qty.setToolTip(f"当前最大可开 {mx} 手；按 {ratio_text} 自动计算，资金变化后会立即更新")
        else:
            self.qty.setToolTip(f"当前最大可开 {mx} 手；手数为手动输入，点击固定资金或仓位比例可启用自动更新")

    def _edit_fractions(self):
        """Edit fixed capital and quick ratios together."""
        dialog = QDialog(self)
        dialog.setWindowTitle("比例/资金设置")
        dialog.setMinimumWidth(430)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        budget_spin = QDoubleSpinBox(dialog)
        budget_spin.setRange(1.0, 1_000_000_000_000.0)
        budget_spin.setDecimals(0)
        budget_spin.setSingleStep(100_000.0)
        budget_spin.setValue(float(self._capital_budget))
        budget_spin.setSuffix(" 元")
        budget_spin.setMinimumWidth(230)
        budget_spin.setToolTip(
            "固定资金快捷按钮按此金额、当前价格、合约乘数和保证金率计算可买手数"
        )
        form.addRow("固定资金：", budget_spin)

        ratios_edit = QLineEdit(",".join(self._fraction_specs), dialog)
        ratios_edit.setMinimumWidth(230)
        ratios_edit.setPlaceholderText("例如：1/4,1/3,1/2,4/5,全仓")
        ratios_edit.setToolTip(
            "支持小数、百分比、分数和“全仓”，多个比例用逗号分隔"
        )
        form.addRow("快捷比例：", ratios_edit)

        layout.addLayout(form)

        hint = QLabel(
            "固定资金和快捷比例都可以修改。\n"
            "比例支持：0.25、25%、1/4、1/2、全仓；多个比例用逗号分隔。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        budget = float(budget_spin.value())
        raw = ratios_edit.text().replace("，", ",")
        specs = [x.strip() for x in raw.split(",") if x.strip()]

        valid: list[str] = []
        for spec in specs[:10]:
            try:
                value = self._parse_fraction(spec)
                if 0 < value <= 1:
                    valid.append(spec)
            except Exception:
                continue

        if not valid:
            QMessageBox.warning(
                self,
                "比例/资金设置",
                "没有识别到有效的快捷比例。请至少保留一个有效比例。",
            )
            return

        self._capital_budget = budget
        self._fraction_specs = valid
        self._normalise_quick_button_order()
        self._rebuild_fraction_buttons()
        self.ratiosChanged.emit()

        if self._active_capital_sizing:
            self.capitalSizingRequested.emit(float(self._capital_budget))

    def fraction_specs(self) -> list[str]:
        return list(self._fraction_specs)

    def quick_button_order(self) -> list[str]:
        self._normalise_quick_button_order()
        return list(self._quick_button_order)

    def set_quick_button_order(self, order) -> None:
        if not isinstance(order, (list, tuple)):
            return
        candidate = [str(x).strip() for x in order if str(x).strip()]
        if not candidate:
            return
        self._quick_button_order = candidate
        self._normalise_quick_button_order()
        self._rebuild_fraction_buttons()

    def set_fraction_specs(self, specs, replace_legacy_first: bool = False):
        if isinstance(specs, (list, tuple)) and specs:
            valid = []
            for x in specs:
                try:
                    s = str(x).strip()
                    v = self._parse_fraction(s)
                    if 0 < v <= 1:
                        valid.append(s)
                except Exception:
                    pass
            # One-time migration from saves created before the fixed-capital
            # button existed: the old first ratio is the button being replaced.
            if replace_legacy_first and valid:
                valid = valid[1:]
            if valid:
                self._fraction_specs = valid[:10]
                self._normalise_quick_button_order()
                self._rebuild_fraction_buttons()

    # -------------------------- daily trade/order history --------------------------
    @staticmethod
    def _clock_time(value) -> str:
        """Return only the real operation clock time, e.g. 09:33:47."""
        text = str(value or "").strip()
        if not text:
            return ""
        # Current CSV format: YYYY-MM-DD HH:MM:SS.  Also accept ISO T separator
        # and legacy rows containing only HH:MM:SS.
        if "T" in text:
            text = text.split("T", 1)[1]
        elif " " in text:
            text = text.rsplit(" ", 1)[-1]
        return text[:8]

    @staticmethod
    def _set_item_color(item: QTableWidgetItem, color: str):
        item.setForeground(QColor(color))

    @staticmethod
    def _pnl_color(value) -> str:
        try:
            x = float(value)
        except Exception:
            return "#475569"
        if x > 0:
            return "#dc2626"   # profit / up: red
        if x < 0:
            return "#16a34a"   # loss / down: green
        return "#475569"

    def set_daily_records(self, trades, orders, trading_day: str = ""):
        """Show the operator's real-day fills and outstanding/order history.

        Playback may call this method very frequently.  The tables are rebuilt
        only when their underlying records actually change, so dragging the
        record scrollbars is not interrupted by the K-line timer.
        """
        raw_trades = list(trades or [])
        raw_orders = list(orders or [])

        # Newest operation first. Completed orders are omitted because the fill
        # already appears in 成交记录.
        trades = list(reversed(raw_trades))
        orders = [
            row for row in reversed(raw_orders)
            if str(row.get("status", "")).strip() != "成交"
        ]

        # Use a lightweight immutable signature. If playback advances but no
        # trade/order record changed, leave both QTableWidgets completely alone.
        def _sig(rows, fields):
            return tuple(tuple(str(row.get(k, "")) for k in fields) for row in rows)

        signature = (
            _sig(trades, ("time", "symbol", "action", "price", "qty", "realized_pnl", "trade_key")),
            _sig(orders, ("time", "symbol", "order_id", "action", "price", "qty", "status")),
        )
        if signature == self._records_signature:
            return
        self._records_signature = signature

        # Preserve the user's current scroll positions when records change.
        fill_bar = self.fills.verticalScrollBar()
        order_bar = self.order_history.verticalScrollBar()
        fill_scroll = fill_bar.value()
        order_scroll = order_bar.value()

        # 成交合计放在第一行；最新成交从第二行开始，仍按最新在前。
        total_qty = 0
        total_realized = 0.0
        for row in trades:
            try:
                total_qty += int(float(row.get("qty", 0) or 0))
            except Exception:
                pass
            try:
                total_realized += float(row.get("realized_pnl", 0) or 0)
            except Exception:
                pass

        self.fills.setRowCount(len(trades) + 1)
        summary_vals = ["", "今日合计", "", "", str(total_qty), f"{total_realized:.2f}"]
        for c, value in enumerate(summary_vals):
            item = QTableWidgetItem(value)
            item.setBackground(QColor("#f1f5f9"))
            if c in (1, 4, 5):
                font = item.font(); font.setBold(True); item.setFont(font)
            if c == 5:
                self._set_item_color(item, self._pnl_color(total_realized))
            self.fills.setItem(0, c, item)

        for r, row in enumerate(trades, start=1):
            vals = [
                self._clock_time(row.get("time", "")),
                row.get("symbol", ""),
                row.get("action", ""),
                row.get("price", ""),
                row.get("qty", ""),
                row.get("realized_pnl", ""),
            ]
            trade_key = str(row.get("trade_key", "") or "").strip()
            for c, value in enumerate(vals):
                item = QTableWidgetItem(str(value))
                # Keep the exact source/product/contract identity attached to the
                # row, even when the user reorders visible columns.
                item.setData(Qt.ItemDataRole.UserRole, trade_key)
                if c == 2:
                    action = str(row.get("action", ""))
                    if any(k in action for k in ("买", "多")):
                        self._set_item_color(item, "#dc2626")
                    elif any(k in action for k in ("卖", "空")):
                        self._set_item_color(item, "#16a34a")
                elif c == 5:
                    self._set_item_color(item, self._pnl_color(value))
                self.fills.setItem(r, c, item)

        self.order_history.setRowCount(len(orders))
        for r, row in enumerate(orders):
            vals = [
                self._clock_time(row.get("time", "")),
                row.get("symbol", ""),
                row.get("order_id", ""),
                row.get("action", ""),
                row.get("price", ""),
                row.get("qty", ""),
                row.get("status", ""),
            ]
            for c, value in enumerate(vals):
                item = QTableWidgetItem(str(value))
                if c == 3:
                    action = str(row.get("action", ""))
                    if any(k in action for k in ("买", "多")):
                        self._set_item_color(item, "#dc2626")
                    elif any(k in action for k in ("卖", "空")):
                        self._set_item_color(item, "#16a34a")
                elif c == 6:
                    status = str(value)
                    if status in ("等待", "挂单", "触及", "未触发"):
                        self._set_item_color(item, "#d97706")
                    elif "撤销" in status or "重置" in status:
                        self._set_item_color(item, "#64748b")
                    elif "不足" in status or "失败" in status or "拒" in status:
                        self._set_item_color(item, "#dc2626")
                self.order_history.setItem(r, c, item)

        # Never force-scroll to the top during playback.  Restore whatever place
        # the user was looking at; the scrollbar is therefore unrelated to K-line
        # playback and remains draggable even while the timer runs.
        fill_bar.setValue(min(fill_scroll, fill_bar.maximum()))
        order_bar.setValue(min(order_scroll, order_bar.maximum()))
        self.record_tabs.setTabText(0, "成交记录")
        self.record_tabs.setTabText(1, "委托记录")

    # -------------------------- account UI --------------------------
    def _deposit_dialog(self):
        box = QMessageBox(self)
        box.setWindowTitle("入金")
        box.setText("请输入入金金额（使用下方输入框）：")
        spin = QDoubleSpinBox(box)
        spin.setRange(1, 1e12)
        spin.setDecimals(2)
        spin.setValue(100000)
        box.layout().addWidget(spin, 1, 1)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if box.exec() == QMessageBox.StandardButton.Ok:
            self.depositRequested.emit(spin.value())

    def _cancel_selected(self):
        """Cancel the selected outstanding order from 委托记录.

        The visible order-history table is newest-first.  Completed executions are
        excluded from it, and TradingAccount.cancel() safely ignores IDs that are no
        longer outstanding (for example an already-cancelled historical row).
        """
        row = self.order_history.currentRow()
        if row < 0:
            return
        item = self.order_history.item(row, 2)  # ID column
        if item:
            text = item.text().strip()
            if not text:
                return
            try:
                value = int(text)
            except ValueError:
                value = text
            self.cancelOrderRequested.emit(value)

    def _fill_cell_clicked(self, row: int, column: int) -> None:
        """Navigate when the user clicks a trade record's 品种 cell.

        Row zero is the non-tradable 今日合计 summary. The column index is the
        logical table column, so this continues to work after header reordering.
        """
        if row <= 0 or int(column) != 1:
            return
        item = self.fills.item(int(row), 1)
        if item is None:
            return
        identity = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not identity:
            identity = item.text().strip()  # legacy CSV rows without trade_key
        if identity:
            self.tradeRecordSymbolClicked.emit(identity)

    def _position_cell_clicked(self, row: int, column: int) -> None:
        """Select a holding and immediately open its product/chart.

        Any cell in a live-position row behaves the same way; the user no longer
        has to aim specifically at the 品种 column.  The fixed 持仓合计 row is
        intentionally non-navigating.
        """
        if row < 0:
            return
        # Every position cell carries the exact trade key, so this remains
        # correct even after the user drags columns into a different order.
        item = self.position.item(int(row), int(column))
        key = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""
        if not key:
            fallback = self.position.item(int(row), 0)
            key = str(fallback.data(Qt.ItemDataRole.UserRole) or "").strip() if fallback is not None else ""
        if not key:
            # Row 0 is the fixed 持仓合计 row; it is not a tradable position.
            self._selected_position_key = ""
            return
        self._selected_position_key = key
        self.positionSymbolClicked.emit(key)

    def selected_position_key(self) -> str:
        """Return the currently selected live position key, if any."""
        key = str(self._selected_position_key or "").strip()
        if key and self.account is not None and self.account.position_for(key) is not None:
            return key
        row = self.position.currentRow()
        if row >= 0:
            item = self.position.item(row, 0)
            key = str(item.data(Qt.ItemDataRole.UserRole) or "").strip() if item is not None else ""
            if key and self.account is not None and self.account.position_for(key) is not None:
                self._selected_position_key = key
                return key
        self._selected_position_key = ""
        return ""

    def preview_current_product(self, label: str) -> None:
        """Update product identity immediately while its market data is loading."""
        text = str(label or "--").strip() or "--"
        self.lbl_current_product.setText(f"当前品种：{text}")
        self.lbl_margin.setText("加载中…")

    def set_account(
        self,
        account: TradingAccount,
        mark: float,
        current_symbol: str = "",
        symbol_labels: dict[str, str] | None = None,
    ):
        self.account = account
        self.mark = float(mark)
        self.current_symbol = str(current_symbol or "")
        self.symbol_labels = dict(symbol_labels or {})
        if self.current_symbol and self.mark > 0:
            account.set_mark(self.current_symbol, self.mark)
        self.refresh()

    def refresh(self):
        a = self.account
        if a is None:
            return

        # Total equity / available funds remain account-wide.  The account-strip
        # margin figure is *not* occupied margin: it is the margin required to
        # open exactly one contract of the instrument currently shown on screen,
        # regardless of whether that instrument is already held.
        equity = a.equity()
        available = a.available()
        one_lot_margin = 0.0
        if self.current_symbol:
            px = float(self.mark or 0.0)
            if px <= 0:
                px = float(a.mark_for(self.current_symbol) or 0.0)
            if px > 0:
                one_lot_margin = px * a.multiplier(self.current_symbol) * float(a.margin_rate)
        fp_total = a.floating_pnl()
        total_position_lots = sum(abs(int(pos.qty)) for pos in a.positions.values())
        margin_total = a.margin_used()
        total_pnl = float(a.realized_pnl) + float(fp_total)
        initial_capital = float(DEFAULT_INITIAL_CAPITAL)
        pnl_pct = (total_pnl / initial_capital * 100.0) if abs(initial_capital) > 1e-12 else 0.0
        self.lbl_equity.setText(f"{equity:,.2f}")
        self.lbl_available.setText(f"{available:,.2f}")
        current_label = self.symbol_labels.get(self.current_symbol, self.current_symbol) if self.current_symbol else "--"
        self.lbl_current_product.setText(f"当前品种：{current_label}")
        self.lbl_margin.setText(f"{one_lot_margin:,.2f}")
        self.lbl_position_lots.setText(f"{total_position_lots:,} 手")
        self.lbl_position_pnl.setText(f"{fp_total:+,.2f}")
        self.lbl_position_pnl.setStyleSheet(
            f"color:{self._pnl_color(fp_total)}; font-size:11pt; font-weight:800;"
        )
        self.lbl_realized.setText(f"{total_pnl:,.2f}  ({pnl_pct:+.2f}%)")
        self.lbl_realized.setStyleSheet(f"color:{self._pnl_color(total_pnl)}; font-weight:700;")

        positions = list(a.positions.values())
        positions.sort(key=lambda p: (0 if p.symbol == self.current_symbol else 1, p.symbol))
        # Keep the portfolio summary permanently in the first row so it remains
        # visible above every held instrument regardless of the current symbol.
        self.position.setRowCount(len(positions) + 1)
        total_vals = ["持仓合计", "", "", "", f"{margin_total:.2f}", f"{fp_total:.2f}", ""]
        for c, value in enumerate(total_vals):
            item = QTableWidgetItem(value)
            item.setBackground(QColor("#f1f5f9"))
            if c in (0, 4, 5):
                font = item.font(); font.setBold(True); item.setFont(font)
            if c == 5:
                self._set_item_color(item, self._pnl_color(fp_total))
            self.position.setItem(0, c, item)

        selected_row = -1
        selected_key = str(self._selected_position_key or "")
        for r, pos in enumerate(positions, start=1):
            fp = a.floating_pnl(symbol=pos.symbol)
            pos_margin = a.margin_used(symbol=pos.symbol)
            label = self.symbol_labels.get(pos.symbol, pos.symbol)
            # Ratio = additional lots affordable with current account-available funds
            # divided by the currently held lots for this exact contract.
            mark = float(a.mark_for(pos.symbol) or pos.avg_price or 0.0)
            affordable_lots = a.max_open_qty(pos.symbol, mark) if mark > 0 else 0
            held_lots = max(1, abs(int(pos.qty)))
            capacity_ratio = affordable_lots / held_lots
            vals = [label, pos.side.value, str(pos.qty), f"{pos.avg_price:.4f}", f"{pos_margin:.2f}", f"{fp:.2f}", f"{capacity_ratio:.1f}倍"]
            for c, value in enumerate(vals):
                item = QTableWidgetItem(str(value))
                # Store the exact trade key on every position cell.  This remains
                # correct even if the user drags table columns into another order.
                item.setData(Qt.ItemDataRole.UserRole, str(pos.symbol))
                if c == 0 and pos.symbol == self.current_symbol:
                    item.setBackground(QColor("#eff6ff"))
                elif c == 1:
                    self._set_item_color(item, "#dc2626" if pos.side == Side.LONG else "#16a34a")
                elif c == 5:
                    self._set_item_color(item, self._pnl_color(fp))
                elif c == 6:
                    item.setToolTip(
                        f"按当前可用资金和 {mark:.4f} 的最新参考价，最多还能开 {affordable_lots} 手；"
                        f"除以当前持仓 {abs(int(pos.qty))} 手 = {capacity_ratio:.1f} 倍。"
                    )
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.position.setItem(r, c, item)
            if str(pos.symbol) == selected_key:
                selected_row = r

        if selected_row >= 0:
            self.position.selectRow(selected_row)
        elif selected_key:
            # The selected holding may have just been fully closed.
            self._selected_position_key = ""
