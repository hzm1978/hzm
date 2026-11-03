from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from ..trend_report import analysis_html, export_analysis, rules_html
from ..trend_settings import TrendSettings


class TrendIndicatorDialog(QDialog):
    def __init__(self, chart, parent=None):
        super().__init__(parent)
        self.setWindowTitle("趋势交易法第三版 · 设置与判定明细")
        self.resize(1000, 740)
        self.chart = chart
        self.config = TrendSettings.from_dict(chart.trend_settings)
        self.result_settings = self.config.to_dict()
        self.analysis = chart._trend_analysis_cached(chart.current_idx + 1)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        options = QWidget()
        options_layout = QVBoxLayout(options)
        intro = QLabel("以 3.pdf 第三版为依据。图层可分别开关，‘波浪’复选框为独立的旧波浪工具。")
        intro.setWordWrap(True)
        options_layout.addWidget(intro)
        grid = QGridLayout()
        self.toggles = {}
        names = [
            ("show_intervals", "主Q / 子q区间根数"), ("show_navigation", "更多导航区间 / 微转折"),
            ("show_waves", "波浪线与浪号"), ("show_trendline", "趋势线"),
            ("show_subwaves", "独立子浪计数（含反向5 / 7浪）"),
            ("show_pivot_line", "外延线与拐点线"), ("show_boundaries", "主A / 导A分界点"),
            ("show_ratios", "历史回调百分比"), ("show_candles", "关键位置K线反转"),
            ("show_corrections", "调整浪结构标注"), ("show_levels", "40 / 62 / 78.6%与延时线"),
            ("show_targets", "主浪/全趋势目标与角度参考"),
        ]
        for i, (key, text) in enumerate(names):
            box = QCheckBox(text)
            box.setChecked(getattr(self.config, key))
            grid.addWidget(box, i // 2, i % 2)
            self.toggles[key] = box
        options_layout.addLayout(grid)
        form = QFormLayout()
        self.numbers = {}
        for key, text, low, high in [
            ("lookback", "分析范围（已收盘K线根数）", 200, 10000),
            ("confirmation_bars", "微观转折右侧确认根数（程序参数）", 1, 10),
            ("minimum_interval", "最短区间根数（程序参数）", 2, 30),
            ("standard_window", "标准K参考范围（书中40根）", 10, 200),
            ("standard_sample", "标准K大实体样本数（书中5根）", 1, 20),
        ]:
            spin = QSpinBox()
            spin.setRange(low, high)
            spin.setValue(getattr(self.config, key))
            spin.setObjectName("trend_" + key)
            form.addRow(text, spin)
            self.numbers[key] = spin
        options_layout.addLayout(form)
        note = QLabel("实线/正式标注只使用已收盘资料；末端虚线为预览。主结构、子结构证据不足时保留候选。\n"
                      "单根K内高低顺序、消息假高低点和人工结构重划，需更细行情或人工核对。")
        note.setWordWrap(True)
        options_layout.addWidget(note)
        reset = QPushButton("恢复第三版默认参数")
        reset.clicked.connect(self._reset)
        options_layout.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)
        options_layout.addStretch()
        tabs.addTab(options, "显示与参数")
        details = QTextBrowser()
        details.setObjectName("trend_analysis_details")
        details.setHtml(analysis_html(self.analysis, chart.df))
        tabs.addTab(details, "当前判定明细")
        rules = QTextBrowser()
        rules.setOpenExternalLinks(True)
        rules.setHtml(rules_html())
        tabs.addTab(rules, "规则与书页")
        tabs.setCurrentIndex(1 if chart.show_trend else 0)
        actions = QHBoxLayout()
        export = QPushButton("导出当前明细JSON")
        export.clicked.connect(self._export)
        actions.addWidget(export)
        actions.addStretch()
        apply = QPushButton("应用并显示")
        apply.setObjectName("apply_trend_settings")
        apply.clicked.connect(self._apply)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(apply)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _reset(self):
        defaults = TrendSettings()
        for key, box in self.toggles.items():
            box.setChecked(getattr(defaults, key))
        for key, spin in self.numbers.items():
            spin.setValue(getattr(defaults, key))

    def _apply(self):
        values = {key: box.isChecked() for key, box in self.toggles.items()}
        values.update({key: spin.value() for key, spin in self.numbers.items()})
        self.result_settings = TrendSettings.from_dict(values).to_dict()
        self.accept()

    def _export(self):
        name, _ = QFileDialog.getSaveFileName(self, "保存指标判定明细", "趋势交易法第三版_判定明细.json", "JSON (*.json)")
        if not name:
            return
        try:
            Path(name).write_text(export_analysis(self.analysis), encoding="utf-8")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法导出", str(error))
