from __future__ import annotations

from math import ceil, floor, hypot, isfinite, log10
from typing import Iterable

import numpy as np
import pandas as pd
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QBrush, QWheelEvent, QPolygonF
from PySide6.QtWidgets import QWidget

from ..config import DEFAULT_VISIBLE_BARS, MAX_VISIBLE_BARS, MIN_VISIBLE_BARS
from ..indicators import bollinger, cci, kdj, macd, rsi, sma, volume_ma, williams_r
from ..models import Drawing
from ..replay import interpolate_partial, minute_visual_keyframes
from ..wave_analysis import (
    analyze_waves,
    calculate_wave_ruler_levels,
    last_completed_leg_fibonacci_levels,
)
from ..trend_analysis import analyze_trend
from ..trend_settings import TrendSettings


class CandleChart(QWidget):
    clickedPrice = Signal(float)
    activated = Signal()
    drawingChanged = Signal()
    tradeLineChanged = Signal(str, float)
    tradeLineDeleteRequested = Signal(str)
    followLatestChanged = Signal(bool)
    layoutChanged = Signal()
    volumeOverlayChanged = Signal(bool, bool, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.df = pd.DataFrame()
        self.current_idx = -1
        self.partial: dict[str, float] | None = None
        self._visual_partial: dict[str, float] | None = None
        self._anim_from: dict[str, float] | None = None
        self._anim_to: dict[str, float] | None = None
        self._anim_queue: list[dict[str, float]] = []
        self._anim_segment_ms = 80
        self._anim_clock = QElapsedTimer()
        self._anim_timer = QTimer(self)
        # 8 ms animation ticks make the forming candle visibly smoother while
        # remaining independent from the replay/data timer.
        self._anim_timer.setInterval(8)
        self._anim_timer.timeout.connect(self._advance_animation)
        self.transition_ms = 140

        self.visible_bars = DEFAULT_VISIBLE_BARS
        self.end_offset = 0
        # Fractional horizontal translation measured in candle slots.  Keeping the
        # fractional part makes mouse panning follow the cursor continuously rather
        # than jumping one whole candle at a time.
        self._pan_shift_slots = 0.0
        self.follow_latest = True
        self.ma_periods: tuple[int, ...] = (5, 10, 20, 60)
        self.show_ma = True
        self.show_volume = True
        self.show_volume_vma5 = True
        self.show_volume_vma10 = True
        self.show_open_interest_line = True
        self.show_boll = False
        self.show_wave = False
        self.show_trend = False
        self.trend_settings = TrendSettings().to_dict()
        self.indicator = "无"
        self._wave_cache_key = None
        self._wave_cache = None
        self._trend_cache_key = None
        self._trend_cache = None
        self.drawings: list[Drawing] = []
        self.tool = "光标"
        self._draft: tuple[float, float] | None = None
        # Three-click wave ruler draft: A, B, then C.
        self._wave_ruler_draft: list[tuple[float, float]] = []
        self._mouse = QPointF(-1, -1)
        self._drag_pan_x: float | None = None
        self._drag_pan_y: float | None = None
        self._drag_pan_start_x: float | None = None
        self._drag_pan_start_y: float | None = None
        self._drag_pan_mode: str | None = None  # horizontal, vertical, or both
        # Vertical chart translation measured in fractions of the autoscaled
        # price range. Positive values move candles downward; negative values
        # move them upward. This changes only the viewport, never market data.
        self._price_pan_fraction = 0.0
        # Fibonacci editing in cursor mode:
        # - drag the visible top/bottom boundary line to resize the whole grid;
        # - drag an inner Fibonacci level to translate the whole grid vertically.
        self._drag_fib_index: int | None = None
        self._drag_fib_mode: str | None = None   # "resize_y1", "resize_y2", "move"
        self._drag_fib_start_price: float | None = None
        self._drag_fib_original: tuple[float, float] | None = None
        # Generic drawing editing (cursor tool).  Trend/ray endpoints can be
        # dragged to rotate/resize; rectangle corners can be resized; dragging
        # a drawing body translates it without changing its shape.
        self._drag_drawing_index: int | None = None
        self._drag_drawing_mode: str | None = None
        self._drag_drawing_start: tuple[float, float] | None = None
        self._drag_drawing_original: tuple[float, float, float, float] | None = None
        self._drag_drawing_original_c: tuple[float, float] | None = None
        self._trade_lines: dict[str, float | None] = {"成本": None, "止盈": None, "止损": None}
        # Position context is supplied by MainWindow so TP/SL labels can show the
        # gross P/L that would be realized if the whole current position were
        # closed at that line.  direction: +1 long, -1 short.
        self._trade_direction: float | None = None
        self._trade_qty: int = 0
        self._trade_multiplier: float = 0.0
        self._drag_trade_line: str | None = None
        self._trade_drag_preview: float | None = None
        self._limit_lines: list[tuple[str, float]] = []
        # Optional backtest execution markers. They are independent of live
        # trading lines and are drawn only when a backtest result is selected.
        self._backtest_markers: list[dict] = []
        self._backtest_marker_indices: list[tuple[int, dict]] = []
        # Automatic dominant-contract rollover separators for continuous charts.
        self._roll_markers: list[dict] = []
        self._roll_marker_indices: list[tuple[int, dict]] = []
        self.synthetic = False
        self.intraday_mode = False

        self.crosshair_enabled = True
        self._plot_rect = QRectF()
        self._panel_rects: list[QRectF] = []
        self._panel_kinds: list[str] = []
        self._separator_ys: list[float] = []
        self._drag_separator_index: int | None = None
        self._panel_weights: dict[int, list[float]] = {
            1: [1.0],
            2: [0.74, 0.26],
            3: [0.62, 0.19, 0.19],
        }
        self._price_min = 0.0
        self._price_max = 1.0
        self._start = 0
        self._end = 0
        self._left_pad_slots = 0.0
        self._paint_partial: dict[str, float] | None = None
        # Bottom edge of the MA legend in the main chart. Crosshair information
        # is placed below it so the two overlays never cover one another.
        self._ma_legend_bottom = 0.0
        # Clickable VMA5/VMA10/open-interest switches are painted inside the
        # volume pane itself.  Rectangles are refreshed on every paint so hit
        # testing always matches the current panel geometry.
        self._volume_toggle_rects: dict[str, QRectF] = {}

    # ----------------------------- data / options -----------------------------
    def set_series(
        self,
        df: pd.DataFrame,
        current_idx: int,
        partial: dict[str, float] | None = None,
        synthetic: bool = False,
        source_bar: dict[str, float] | None = None,
        animate_minute: bool = False,
    ):
        old_idx = self.current_idx
        old_visual = None if self._visual_partial is None else dict(self._visual_partial)
        old_partial = None if self.partial is None else dict(self.partial)
        self.df = df
        self._resolve_backtest_markers()
        self._resolve_roll_markers()
        self.current_idx = int(current_idx)
        self.partial = None if partial is None else dict(partial)
        self.synthetic = synthetic

        # A restored viewport may have been loaded before the data series was
        # available.  Clamp it only now, when current_idx is known.
        self.end_offset = max(0, min(int(self.end_offset), max(0, self.current_idx)))
        limit = 0.45 * self.visible_bars
        self._pan_shift_slots = max(-limit, min(limit, float(self._pan_shift_slots)))

        if self.follow_latest and self.current_idx != old_idx:
            self.end_offset = 0
            self._pan_shift_slots = 0.0

        if self.partial is None:
            self._stop_animation(None)
        else:
            target = dict(self.partial)
            if not animate_minute:
                # Strict data-fidelity mode: the displayed candle is the exact
                # real replay state.  Do not interpolate or fabricate intra-minute
                # O/H/L/C values between two source records.
                self._stop_animation(target)
            elif source_bar and not synthetic:
                if old_idx == self.current_idx and old_partial is not None:
                    seed = old_visual or old_partial
                elif old_idx >= 0 and self.current_idx == old_idx + 1:
                    o = float(target["open"])
                    seed = dict(target)
                    seed.update({"open": o, "high": o, "low": o, "close": o, "volume": 0.0, "amount": 0.0})
                else:
                    seed = old_visual or target
                self._start_animation(seed, target)
            else:
                self._stop_animation(target)
        self.update()

    def set_roll_markers(self, markers: Iterable[dict] = ()):
        """Set automatic dominant-contract rollover markers.

        Markers use ``trading_day`` and optional ``from``/``to`` contract names.
        They are resolved to the first visible bar belonging to (or ending after)
        that trading day, so the same switch list works for minute through monthly
        charts.
        """
        new_markers = [dict(x) for x in markers if isinstance(x, dict)]
        if new_markers == self._roll_markers:
            return
        self._roll_markers = new_markers
        self._resolve_roll_markers()
        self.update()

    def _resolve_roll_markers(self):
        self._roll_marker_indices = []
        if not self._roll_markers or self.df is None or self.df.empty:
            return
        try:
            if "trading_day" in self.df.columns:
                bar_days = pd.to_datetime(self.df["trading_day"], errors="coerce").dt.normalize()
            elif "datetime" in self.df.columns:
                bar_days = pd.to_datetime(self.df["datetime"], errors="coerce").dt.normalize()
            else:
                return
            day_values = bar_days.to_numpy(dtype="datetime64[ns]")
            valid = ~pd.isna(bar_days).to_numpy()
            for marker in self._roll_markers:
                day = pd.to_datetime(marker.get("trading_day"), errors="coerce")
                if pd.isna(day):
                    continue
                target = np.datetime64(pd.Timestamp(day).normalize().to_datetime64())
                hits = np.flatnonzero(valid & (day_values >= target))
                if hits.size:
                    self._roll_marker_indices.append((int(hits[0]), marker))
        except Exception:
            self._roll_marker_indices = []

    def _draw_roll_markers(self, p: QPainter):
        if not self._roll_marker_indices or self._plot_rect.isEmpty():
            return
        pen = QPen(QColor(190, 195, 205, 190), 1.0, Qt.PenStyle.DashLine)
        p.setPen(pen)
        for idx, _marker in self._roll_marker_indices:
            # Never reveal a future rollover before the replay has reached it.
            if idx > self.current_idx or idx < self._start or idx >= self._end:
                continue
            # Draw at the boundary immediately before the first bar of the new
            # dominant contract, rather than through the centre of that candle.
            x = self._index_x(idx) - self._slot_step() * 0.5
            if x < self._plot_rect.left() or x > self._plot_rect.right():
                continue
            p.drawLine(
                QPointF(x, self._plot_rect.top()),
                QPointF(x, self._plot_rect.bottom()),
            )

    def set_backtest_markers(self, markers: Iterable[dict] = ()):
        """Show buy/sell points from a selected backtest result."""
        self._backtest_markers = [dict(x) for x in markers if isinstance(x, dict)]
        self._resolve_backtest_markers()
        self.update()

    def clear_backtest_markers(self):
        self._backtest_markers = []
        self._backtest_marker_indices = []
        self.update()

    def _resolve_backtest_markers(self):
        self._backtest_marker_indices = []
        if not self._backtest_markers or self.df is None or self.df.empty:
            return
        col = "datetime" if "datetime" in self.df.columns else ("trading_day" if "trading_day" in self.df.columns else None)
        if col is None:
            return
        try:
            times = pd.to_datetime(self.df[col], errors="coerce")
            lookup: dict[int, int] = {}
            for i, ts in enumerate(times):
                if pd.isna(ts):
                    continue
                lookup.setdefault(int(pd.Timestamp(ts).value), i)
            for marker in self._backtest_markers:
                try:
                    key = int(pd.Timestamp(marker.get("datetime")).value)
                except Exception:
                    continue
                idx = lookup.get(key)
                if idx is not None:
                    self._backtest_marker_indices.append((idx, marker))
        except Exception:
            self._backtest_marker_indices = []

    def set_intraday_mode(self, on: bool):
        on = bool(on)
        if on == self.intraday_mode:
            return
        self.intraday_mode = on
        # A time-sharing chart always follows the currently forming trading day.
        # Reset manual horizontal offsets when entering/leaving this mode so all
        # minutes of the day fit naturally in the plot.
        self.end_offset = 0
        self._pan_shift_slots = 0.0
        self.update()

    def set_follow_latest(self, on: bool):
        on = bool(on)
        changed = on != self.follow_latest
        self.follow_latest = on
        if self.follow_latest:
            self.end_offset = 0
            self._pan_shift_slots = 0.0
        if changed:
            self.followLatestChanged.emit(self.follow_latest)
        self.update()

    def set_transition_ms(self, milliseconds: int):
        ms = int(milliseconds)
        self.transition_ms = 0 if ms <= 0 else max(12, min(ms, 520))

    def set_trade_lines(
        self,
        cost=None,
        take=None,
        stop=None,
        limits: Iterable[tuple[str, float]] = (),
        *,
        direction: float | None = None,
        qty: int = 0,
        multiplier: float = 0.0,
    ):
        self._trade_lines = {"成本": cost, "止盈": take, "止损": stop}
        self._limit_lines = list(limits)
        self._trade_direction = None if direction is None else (1.0 if float(direction) >= 0 else -1.0)
        self._trade_qty = max(0, int(qty or 0))
        self._trade_multiplier = max(0.0, float(multiplier or 0.0))
        self.update()

    def _trade_line_pnl(self, target_price: float) -> float | None:
        """Gross P/L for closing the current whole position at *target_price*."""
        cost = self._trade_lines.get("成本")
        if (
            cost is None
            or self._trade_direction is None
            or self._trade_qty <= 0
            or self._trade_multiplier <= 0
        ):
            return None
        return (
            (float(target_price) - float(cost))
            * float(self._trade_direction)
            * self._trade_qty
            * self._trade_multiplier
        )

    @staticmethod
    def _format_trade_pnl(value: float | None) -> str:
        if value is None:
            return ""
        return f"盈亏 {float(value):+,.2f}"

    def set_tool(self, tool: str):
        self.tool = tool
        self._draft = None
        self._wave_ruler_draft = []
        # Cursor mode is the normal inspection mode.  Make sure the crosshair
        # and candle information are visible when the user explicitly returns
        # to it; a subsequent double-click can still hide them if desired.
        if tool == "光标" and not self.crosshair_enabled:
            self.crosshair_enabled = True
            self.update()

    def set_ma(self, periods: tuple[int, ...], visible: bool = True):
        periods_new = tuple(int(x) for x in periods if int(x) > 0)
        visible_new = bool(visible) and bool(periods_new)
        if periods_new == self.ma_periods and visible_new == self.show_ma:
            return
        self.ma_periods = periods_new
        self.show_ma = visible_new
        self.update()

    def set_indicator(self, name: str):
        # VOLMA is retained only as a legacy saved value; volume MAs now live in the volume pane.
        name_new = name if name not in ("BOLL", "VOLMA") else "无"
        if name_new == self.indicator:
            return
        self.indicator = name_new
        self.update()

    def set_wave_visible(self, on: bool):
        on_new = bool(on)
        if on_new == self.show_wave:
            return
        self.show_wave = on_new
        self.update()

    def set_trend_visible(self, on: bool):
        on_new = bool(on)
        if on_new == self.show_trend:
            return
        self.show_trend = on_new
        self.update()

    def set_boll_visible(self, on: bool):
        on_new = bool(on)
        if on_new == self.show_boll:
            return
        self.show_boll = on_new
        self.update()

    def set_volume_visible(self, on: bool):
        on_new = bool(on)
        if on_new == self.show_volume:
            return
        self.show_volume = on_new
        if not on_new:
            self._volume_toggle_rects = {}
        self.update()

    def set_volume_overlays(self, vma5: bool = True, vma10: bool = True, open_interest: bool = True):
        """Independently control VMA5, VMA10 and the white open-interest line.

        During replay this method is called frequently by the panel refresh path.
        Avoid redundant repaints when the state did not actually change so mouse
        clicks on the pane-local switches remain responsive under fast playback.
        """
        state_new = (bool(vma5), bool(vma10), bool(open_interest))
        state_old = (self.show_volume_vma5, self.show_volume_vma10, self.show_open_interest_line)
        if state_new == state_old:
            return
        self.show_volume_vma5, self.show_volume_vma10, self.show_open_interest_line = state_new
        self.update()

    def _hit_volume_toggle(self, pos: QPointF) -> str | None:
        for key, rect in self._volume_toggle_rects.items():
            if rect.contains(pos):
                return key
        return None

    def _toggle_volume_overlay(self, key: str):
        if key == "vma5":
            self.show_volume_vma5 = not self.show_volume_vma5
        elif key == "vma10":
            self.show_volume_vma10 = not self.show_volume_vma10
        elif key == "open_interest":
            self.show_open_interest_line = not self.show_open_interest_line
        else:
            return
        self.volumeOverlayChanged.emit(
            self.show_volume_vma5,
            self.show_volume_vma10,
            self.show_open_interest_line,
        )
        self.update()

    def set_crosshair_enabled(self, on: bool):
        self.crosshair_enabled = bool(on)
        self.update()

    def panel_layout_state(self) -> dict:
        # Persist indicator-panel heights, zoom, horizontal history position,
        # and vertical price viewport so the application can restore the same view.
        return {
            "weights": {str(k): list(v) for k, v in self._panel_weights.items()},
            "crosshair": self.crosshair_enabled,
            "visible_bars": int(self.visible_bars),
            "end_offset": int(self.end_offset),
            "pan_shift_slots": float(self._pan_shift_slots),
            "price_pan_fraction": float(self._price_pan_fraction),
        }

    def restore_panel_layout_state(self, state: dict | None):
        if not state:
            return
        try:
            weights = state.get("weights", {})
            for k, vals in weights.items():
                n = int(k)
                vals = [max(0.05, float(x)) for x in vals]
                if n in (1, 2, 3) and len(vals) == n and sum(vals) > 0:
                    s = sum(vals)
                    self._panel_weights[n] = [x / s for x in vals]
            self.crosshair_enabled = bool(state.get("crosshair", True))
            self.visible_bars = max(MIN_VISIBLE_BARS, min(MAX_VISIBLE_BARS, int(state.get("visible_bars", self.visible_bars))))
            self.end_offset = max(0, int(state.get("end_offset", self.end_offset)))
            limit = 0.45 * self.visible_bars
            self._pan_shift_slots = max(-limit, min(limit, float(state.get("pan_shift_slots", self._pan_shift_slots))))
            self._price_pan_fraction = max(-5.0, min(5.0, float(state.get("price_pan_fraction", self._price_pan_fraction))))
        except Exception:
            pass
        self.update()

    # ----------------------------- animation -----------------------------
    def _start_animation(self, start: dict[str, float], target: dict[str, float]):
        self._anim_queue = []
        if self.transition_ms <= 0 or start == target:
            self._stop_animation(target)
            return
        self._anim_segment_ms = max(1, self.transition_ms)
        self._anim_from = dict(start)
        self._anim_to = dict(target)
        self._visual_partial = dict(start)
        self._anim_clock.restart()
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _start_keyframe_animation(self, start: dict[str, float], frames: list[dict[str, float]]):
        frames = [dict(x) for x in frames]
        if self.transition_ms <= 0 or not frames:
            self._stop_animation(frames[-1] if frames else start)
            return
        self._anim_queue = frames
        self._anim_segment_ms = max(4, int(self.transition_ms / max(1, len(frames))))
        self._visual_partial = dict(start)
        self._begin_next_keyframe()

    def _begin_next_keyframe(self):
        if not self._anim_queue:
            self._stop_animation(self._visual_partial)
            return
        target = self._anim_queue.pop(0)
        self._anim_from = dict(self._visual_partial or target)
        self._anim_to = dict(target)
        self._anim_clock.restart()
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _stop_animation(self, final: dict[str, float] | None):
        self._anim_timer.stop()
        self._anim_from = None
        self._anim_to = None
        self._anim_queue = []
        self._visual_partial = None if final is None else dict(final)

    def _advance_animation(self):
        if self._anim_from is None or self._anim_to is None:
            self._anim_timer.stop()
            return
        t = self._anim_clock.elapsed() / max(1, self._anim_segment_ms)
        if t >= 1.0:
            self._visual_partial = dict(self._anim_to)
            if self._anim_queue:
                self._begin_next_keyframe()
            else:
                self._stop_animation(self._visual_partial)
        else:
            u = t * t * (3.0 - 2.0 * t)
            self._visual_partial = interpolate_partial(self._anim_from, self._anim_to, u)
        self.update()

    # ----------------------------- geometry -----------------------------
    def _source_until_current(self) -> pd.DataFrame:
        end = min(self.current_idx + 1, len(self.df))
        source = self.df.iloc[:end].copy()
        if self.partial and 0 <= self.current_idx < len(source):
            for k, v in self.partial.items():
                if k in source.columns:
                    source.loc[self.current_idx, k] = v
        return source

    def _window(self) -> tuple[int, int]:
        if self.df.empty or self.current_idx < 0:
            return 0, 0
        if self.intraday_mode:
            end = min(self.current_idx + 1, len(self.df))
            end = max(1, end)
            cur = end - 1
            if "trading_day" in self.df.columns:
                day = str(self.df.iloc[cur].get("trading_day", ""))[:10]
                start = cur
                while start > 0 and str(self.df.iloc[start - 1].get("trading_day", ""))[:10] == day:
                    start -= 1
                return start, end
            if "datetime" in self.df.columns:
                try:
                    day = pd.Timestamp(self.df.iloc[cur]["datetime"]).date()
                    start = cur
                    while start > 0 and pd.Timestamp(self.df.iloc[start - 1]["datetime"]).date() == day:
                        start -= 1
                    return start, end
                except Exception:
                    pass
        end = min(self.current_idx + 1 - self.end_offset, len(self.df))
        end = max(1, end)
        start = max(0, end - self.visible_bars)
        return start, end

    def _active_subpanels(self) -> list[str]:
        out: list[str] = []
        # BOLL is controlled independently as a main-chart overlay.
        if self.indicator != "无":
            out.append(f"IND:{self.indicator}")
        if self.show_volume:
            out.append("VOLUME")
        return out[:2]

    def _compute_panel_rects(self) -> tuple[list[QRectF], list[str]]:
        kinds = ["MAIN"] + self._active_subpanels()
        count = len(kinds)
        left = 55.0
        right_margin = 20.0
        top = 12.0
        bottom_margin = 28.0
        sep = 6.0
        total_h = max(60.0, self.height() - top - bottom_margin)
        usable_h = max(40.0, total_h - sep * (count - 1))
        weights = list(self._panel_weights.get(count, [1.0 / count] * count))
        if len(weights) != count or sum(weights) <= 0:
            weights = [1.0 / count] * count
        s = sum(weights)
        weights = [w / s for w in weights]
        self._panel_weights[count] = weights

        x = left
        width = max(10.0, self.width() - left - right_margin)
        y = top
        rects: list[QRectF] = []
        separators: list[float] = []
        for i, w in enumerate(weights):
            h = usable_h * w
            rects.append(QRectF(x, y, width, h))
            y += h
            if i < count - 1:
                separators.append(y + sep / 2)
                y += sep
        self._separator_ys = separators
        self._panel_rects = rects
        self._panel_kinds = kinds
        return rects, kinds

    def _price_y(self, p: float) -> float:
        if self._price_max <= self._price_min:
            return self._plot_rect.center().y()
        r = (float(p) - self._price_min) / (self._price_max - self._price_min)
        return self._plot_rect.bottom() - r * self._plot_rect.height()

    def _y_price(self, y: float) -> float:
        if self._plot_rect.height() <= 0:
            return 0.0
        r = (self._plot_rect.bottom() - y) / self._plot_rect.height()
        return self._price_min + r * (self._price_max - self._price_min)

    def _slot_step(self) -> float:
        slots = max(1, self._end - self._start) if self.intraday_mode else max(1, self.visible_bars)
        return self._plot_rect.width() / slots

    def _index_x(self, idx: float) -> float:
        slot = self._left_pad_slots + (idx - self._start) + 0.5 + self._pan_shift_slots
        return self._plot_rect.left() + slot * self._slot_step()

    def _x_index(self, x: float) -> float:
        slot = (x - self._plot_rect.left()) / max(1e-6, self._slot_step())
        return self._start + slot - self._left_pad_slots - self._pan_shift_slots - 0.5

    # ----------------------------- painting -----------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 22, 26))
        if self.df.empty or self.current_idx < 0:
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "请选择数据")
            return

        start, end = self._window()
        self._start, self._end = start, end
        data = self.df.iloc[start:end].copy()
        if data.empty:
            return

        display_partial = self._visual_partial or self.partial
        self._paint_partial = None if display_partial is None else dict(display_partial)
        if display_partial and self.current_idx < len(self.df) and start <= self.current_idx < end:
            local = self.current_idx - start
            for k, v in display_partial.items():
                if k in data.columns:
                    data.iloc[local, data.columns.get_loc(k)] = v

        # Preserve the last valid button hit rectangles while repainting. Clearing
        # them at the start of every frame creates tiny dead intervals during fast
        # replay in which mouse clicks can miss the buttons.
        rects, kinds = self._compute_panel_rects()
        if "VOLUME" not in kinds:
            self._volume_toggle_rects = {}
        self._plot_rect = rects[0]
        # Fixed bar width regardless of timeframe/data count.  Before enough bars
        # have accumulated, keep the current bar at the right edge like a live chart.
        n = end - start
        self._left_pad_slots = 0.0 if self.intraday_mode else (max(0.0, float(self.visible_bars - n)) if self.follow_latest and self.end_offset == 0 else 0.0)

        lows = data["low"].to_numpy(float)
        highs = data["high"].to_numpy(float)
        pmin, pmax = float(np.nanmin(lows)), float(np.nanmax(highs))

        # Display-only autoscaling for the latest C-origin wave ruler.  Include
        # targets through 127.2%; farther projections must not squash the candles.
        if self.show_wave and not self.intraday_mode:
            wave_scale_analysis = self._wave_analysis_cached(end)
            wave_scale_projection = (
                None if wave_scale_analysis is None
                else wave_scale_analysis.projection
            )
            if (
                wave_scale_projection is not None
                and wave_scale_projection.start is not None
                and wave_scale_projection.end is not None
                and wave_scale_projection.anchor is not None
            ):
                wave_targets = calculate_wave_ruler_levels(
                    wave_scale_projection.start.price,
                    wave_scale_projection.end.price,
                    wave_scale_projection.anchor.price,
                )
                visible_targets = [
                    price for ratio, price in wave_targets if ratio <= 127.2
                ]
                if visible_targets:
                    pmin = min(pmin, min(visible_targets))
                    pmax = max(pmax, max(visible_targets))

        # Only enabled trend layers affect price scaling. Old/broken projected
        # lines may be far outside the visible candle range; keep those clipped
        # instead of flattening the entire price chart to fit an obsolete line.
        if self.show_trend and not self.intraday_mode:
            trend_scale = self._trend_analysis_cached(end)
            if trend_scale is not None:
                settings = trend_scale.settings
                child = trend_scale.subwaves[0] if settings.show_subwaves and trend_scale.subwaves else None
                span = max(pmax - pmin, abs(pmax) * .005, 1e-9)
                visible_values = []
                if settings.show_trendline and trend_scale.trendline:
                    a, b = trend_scale.trendline
                    visible_values.extend(self._trend_line_price(a, b, x)
                                          for x in (max(start, a.index), end - 1))
                if settings.show_pivot_line and trend_scale.external_line:
                    a, b = trend_scale.external_line
                    for x in (max(start, a.index), end - 1):
                        price = self._trend_line_price(a, b, x)
                        visible_values.extend((price, price + float(trend_scale.pivot_line_offset or 0)))
                if settings.show_boundaries:
                    visible_values.extend(x.price for x in trend_scale.boundaries[-4:]
                                          if x.degree == "main" or settings.show_navigation)
                if settings.show_levels:
                    visible_values.extend(x.price for x in trend_scale.levels
                                          if x.kind != "target" and (not child or x.kind != "retracement"))
                    if child:
                        visible_values.extend(x.price for x in child.levels)
                margin = span * (.25 if child else 1)
                bounded = [x for x in visible_values if np.isfinite(x) and pmin - margin <= x <= pmax + margin]
                if settings.show_targets:
                    bounded.extend(x.price for x in trend_scale.levels if x.kind == "target" and np.isfinite(x.price))
                if bounded:
                    pmin, pmax = min(pmin, min(bounded)), max(pmax, max(bounded))

        # Main-chart overlays must participate in autoscaling; otherwise a BOLL
        # upper/lower band or a long MA can be clipped at the top/bottom edge.
        overlay_source = self._source_until_current()
        if self.show_ma:
            for period in self.ma_periods:
                vals = sma(overlay_source["close"], period).iloc[start:end].to_numpy(float)
                vals = vals[np.isfinite(vals)]
                if vals.size:
                    pmin = min(pmin, float(vals.min()))
                    pmax = max(pmax, float(vals.max()))
        if self.show_boll:
            bb = bollinger(overlay_source["close"]).iloc[start:end]
            if not bb.empty:
                vals = bb[["UPPER", "LOWER"]].to_numpy(float).ravel()
                vals = vals[np.isfinite(vals)]
                if vals.size:
                    pmin = min(pmin, float(vals.min()))
                    pmax = max(pmax, float(vals.max()))

        overlays = [float(x) for x in self._trade_lines.values() if x is not None]
        overlays.extend(float(value) for _label, value in self._limit_lines)
        if self._trade_drag_preview is not None:
            overlays.append(float(self._trade_drag_preview))
        if overlays:
            pmin = min(pmin, min(overlays))
            pmax = max(pmax, max(overlays))

        # A manually placed wave ruler is a price overlay too.  Include its
        # projected levels in autoscaling so the target ratios are not clipped.
        for drawing in self.drawings:
            if getattr(drawing, "kind", "") != "波浪尺":
                continue
            cx, cy = getattr(drawing, "c_x", None), getattr(drawing, "c_y", None)
            if cx is None or cy is None:
                continue
            if self._index_x(float(cx)) > self._plot_rect.right():
                continue
            projected = calculate_wave_ruler_levels(
                float(drawing.y1), float(drawing.y2), float(cy)
            )
            if projected:
                values = [price for _ratio, price in projected]
                pmin = min(pmin, min(values))
                pmax = max(pmax, max(values))

        pad = (pmax - pmin) * 0.06 or max(abs(pmax) * 0.01, 1.0)
        base_min, base_max = pmin - pad, pmax + pad
        if self.show_trend and not self.intraday_mode and trend_scale is not None:
            # Reserve actual screen space for the trend legend. Without it,
            # the local wave-0 high/low can sit directly under three text rows.
            child = trend_scale.subwaves[0] if trend_scale.settings.show_subwaves and trend_scale.subwaves else None
            rows = 3 if child and self._plot_rect.width()-16 >= 760 else 2
            legend_pixels = rows*18 + 8 + (18 if self.show_ma or self.show_boll else 0)
            fraction = min(.4, legend_pixels/max(1, self._plot_rect.height()))
            base_max += (base_max-base_min)*fraction/(1-fraction)
        base_span = max(base_max - base_min, 1e-9)
        # Offset the complete price viewport after autoscaling so a vertical
        # mouse drag moves candles and all price overlays together without
        # altering any OHLC/indicator values.
        vertical_shift = self._price_pan_fraction * base_span
        self._price_min, self._price_max = base_min + vertical_shift, base_max + vertical_shift

        self._draw_main_grid(p)
        if self.intraday_mode:
            self._draw_intraday_line(p, data, start)
        else:
            step = self._slot_step()
            body_w = max(1.0, min(12.0, step * 0.66))
            for j, (_, row) in enumerate(data.iterrows()):
                idx = start + j
                x = self._index_x(idx)
                o, h, l, c = map(float, (row.open, row.high, row.low, row.close))
                up = c >= o
                color = QColor(230, 70, 75) if up else QColor(40, 190, 125)
                p.setPen(QPen(color, 1))
                p.drawLine(QPointF(x, self._price_y(h)), QPointF(x, self._price_y(l)))
                y1, y2 = self._price_y(o), self._price_y(c)
                top, bottom = min(y1, y2), max(y1, y2)
                if bottom - top < 1:
                    p.drawLine(QPointF(x - body_w / 2, top), QPointF(x + body_w / 2, top))
                else:
                    p.fillRect(QRectF(x - body_w / 2, top, body_w, bottom - top), color)

        self._ma_legend_bottom = self._plot_rect.top()
        if not self.intraday_mode:
            self._draw_ma(p, start)
            self._draw_ma_legend(p)
            self._draw_boll(p, start)
            self._draw_boll_legend(p)
        if self.show_trend and not self.intraday_mode:
            self._draw_trend_overlay(p, start, end)
        if self.show_wave and not self.intraday_mode:
            self._draw_wave_overlay(p, start, end)
        self._draw_roll_markers(p)
        self._draw_backtest_markers(p)
        self._draw_drawings(p)
        self._draw_trade_lines(p)

        full = self._source_until_current()
        for rect, kind in zip(rects[1:], kinds[1:]):
            if kind == "VOLUME":
                self._draw_volume_panel(p, rect, data, start)
            elif kind.startswith("IND:"):
                self._draw_indicator_panel(p, rect, full, start, kind.split(":", 1)[1])
        self._draw_separators(p)
        self._draw_time_axis(p, start, end)
        self._draw_crosshair(p)

        if self.synthetic:
            p.setPen(QColor(255, 185, 50))
            warn_y = max(self._plot_rect.top() + 18, self._ma_legend_bottom + 18)
            if self.show_trend and not self.intraday_mode:
                # Leave distinct rows for the trend legend and break status.
                warn_y += 36
                analysis = self._trend_analysis_cached(end)
                if (analysis and analysis.settings.show_subwaves and analysis.subwaves
                        and self._plot_rect.width()-16 >= 760):
                    warn_y += 18
            p.drawText(int(self._plot_rect.left() + 8), int(warn_y), "分钟数据缺失：按可用K线汇总显示，非分钟行情")

    def _wave_analysis_cached(self, visible_end: int):
        # A forming bar contains only the partial OHLC supplied by ReplayEngine.
        # Exclude its stored final OHLC from wave detection to prevent look-ahead;
        # use the live partial close only for a dashed, unconfirmed preview tip.
        end = max(0, min(int(visible_end), len(self.df)))
        forming = end == self.current_idx + 1 and self.partial is not None
        analysis_end = max(0, end - 1) if forming else end
        window_start = max(0, analysis_end - 5000)
        source = self.df.iloc[window_start:analysis_end].copy()
        if source.empty:
            return None
        source["_wave_position"] = np.arange(window_start, analysis_end, dtype=int)
        try:
            last = source.iloc[-1]
            signature = tuple(float(last.get(k, 0.0)) for k in ("open", "high", "low", "close"))
        except Exception:
            signature = ()
        key = (id(self.df), analysis_end, window_start, signature)
        if key != self._wave_cache_key:
            self._wave_cache = analyze_waves(source)
            self._wave_cache_key = key
        analysis = self._wave_cache
        if forming:
            try:
                analysis.preview = (int(self.current_idx), float(self.partial["close"]))
            except Exception:
                pass
        return analysis

    def _trend_analysis_cached(self, visible_end: int):
        """Cache causal trend analysis for the currently displayed replay prefix."""
        end = max(0, min(int(visible_end), self.current_idx + 1, len(self.df)))
        forming = end == self.current_idx + 1 and self.partial is not None
        analysis_end = max(0, end - 1) if forming else end
        settings = TrendSettings.from_dict(self.trend_settings)
        window_start = max(0, analysis_end - settings.lookback)
        source = self.df.iloc[window_start:analysis_end].copy()
        if source.empty:
            return None
        source["_trend_position"] = np.arange(window_start, analysis_end, dtype=int)
        try:
            last = source.iloc[-1]
            signature = tuple(
                float(last.get(k, 0.0))
                for k in ("open", "high", "low", "close")
            )
        except Exception:
            signature = ()
        key = (id(self.df), analysis_end, window_start, signature, settings)
        if key != self._trend_cache_key:
            self._trend_cache = analyze_trend(source, lookback=settings.lookback, settings=settings)
            self._trend_cache_key = key
        return self._trend_cache

    def set_trend_settings(self, settings):
        values = TrendSettings.from_dict(settings).to_dict()
        if values != self.trend_settings:
            self.trend_settings = values
            self._trend_cache_key = None
            self.update()

    @staticmethod
    def _trend_line_price(a, b, index: int) -> float:
        dx = int(b.index) - int(a.index)
        if dx == 0:
            return float(b.price)
        return float(a.price) + (
            float(b.price) - float(a.price)
        ) * (int(index) - int(a.index)) / dx

    def _draw_trend_overlay(self, p: QPainter, start: int, end: int):
        from .trend_chart import paint_trend_overlay
        paint_trend_overlay(self, p, start, end)

    def _draw_wave_overlay(self, p: QPainter, start: int, end: int):
        analysis = self._wave_analysis_cached(end)
        if analysis is None:
            return

        old_font = p.font()
        font = p.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        p.setFont(font)

        # A shared collision map gives historical ratios and the latest right-edge
        # Fibonacci ruler priority over lower-priority structure/candle labels.
        occupied_tags: list[QRectF] = []
        pivots = analysis.pivots

        p.save()
        p.setClipRect(self._plot_rect)

        # Confirmed ZigZag swing structure.
        if len(pivots) >= 2:
            p.setPen(QPen(QColor(255, 190, 65, 220), 1.7))
            for left, right in zip(pivots[:-1], pivots[1:]):
                if right.index < start or left.index >= end:
                    continue
                p.drawLine(
                    QPointF(self._index_x(left.index), self._price_y(left.price)),
                    QPointF(self._index_x(right.index), self._price_y(right.price)),
                )

        # Draw geometric correction boundaries before text labels.
        pattern_colors = {
            "expanding_triangle": QColor(210, 145, 255, 185),
            "triangle": QColor(105, 190, 255, 185),
            "triangle_ascending": QColor(105, 190, 255, 185),
            "triangle_descending": QColor(105, 190, 255, 185),
            "rising_wedge": QColor(255, 145, 105, 190),
            "falling_wedge": QColor(105, 220, 175, 190),
            "flag": QColor(255, 205, 95, 190),
            "box": QColor(180, 185, 195, 175),
            "123": QColor(245, 205, 100, 210),
            "double_top": QColor(255, 105, 125, 220),
            "double_bottom": QColor(85, 220, 155, 220),
            "v_bottom": QColor(70, 220, 155, 225),
            "v_top": QColor(255, 115, 130, 225),
            "rounded_bottom": QColor(80, 205, 175, 225),
            "rounded_top": QColor(245, 135, 170, 225),
            "cup": QColor(100, 190, 255, 235),
            "two_b_top": QColor(255, 105, 115, 235),
            "two_b_bottom": QColor(75, 220, 165, 235),
            "simple_correction": QColor(90, 205, 225, 215),
            "irregular_12345": QColor(225, 165, 255, 220),
            "trend_channel_up": QColor(80, 190, 145, 190),
            "trend_channel_down": QColor(235, 145, 95, 190),
            "trendline_up": QColor(70, 225, 180, 220),
            "trendline_down": QColor(245, 145, 105, 220),
        }
        for structure in analysis.chart_patterns:
            if structure.end_index < start or structure.start_index >= end:
                continue
            color = pattern_colors.get(structure.kind, QColor(180, 180, 180, 170))
            if structure.kind in {"trendline_up", "trendline_down"}:
                style = Qt.PenStyle.SolidLine
            elif structure.kind in {"trend_channel_up", "trend_channel_down"}:
                style = Qt.PenStyle.DashLine
            elif structure.kind in {"123", "irregular_12345"}:
                style = Qt.PenStyle.DotLine
            elif structure.kind in {
                "v_bottom", "v_top", "rounded_bottom", "rounded_top",
                "cup", "two_b_top", "two_b_bottom",
            }:
                style = Qt.PenStyle.SolidLine
            else:
                style = Qt.PenStyle.DashLine
            for line_no, ((x1, y1), (x2, y2)) in enumerate(structure.lines):
                if max(x1, x2) < start or min(x1, x2) >= end:
                    continue
                if structure.kind in {"double_top", "double_bottom"}:
                    # Extreme connector, neckline and measured-move target.
                    line_style = (
                        Qt.PenStyle.SolidLine if line_no == 0
                        else Qt.PenStyle.DashLine if line_no == 1
                        else Qt.PenStyle.DotLine
                    )
                elif structure.kind in {"trend_channel_up", "trend_channel_down"}:
                    line_style = Qt.PenStyle.DashLine
                elif structure.kind in {"simple_correction", "irregular_12345"}:
                    line_style = Qt.PenStyle.SolidLine
                else:
                    line_style = style
                p.setPen(QPen(color, 1.35 if line_no == 0 else 1.0, line_style))
                p.drawLine(
                    QPointF(self._index_x(x1), self._price_y(y1)),
                    QPointF(self._index_x(x2), self._price_y(y2)),
                )

        # The current unfinished candle is preview-only and never becomes a
        # confirmed pivot or a completed Fibonacci ratio.
        if analysis.preview is not None and pivots:
            preview_idx, preview_price = analysis.preview
            last = pivots[-1]
            if preview_idx > last.index and preview_idx >= start and last.index < end:
                p.setPen(QPen(QColor(255, 190, 65, 175), 1.2, Qt.PenStyle.DashLine))
                p.drawLine(
                    QPointF(self._index_x(last.index), self._price_y(last.price)),
                    QPointF(self._index_x(preview_idx), self._price_y(preview_price)),
                )

        # Render every historical N-wave ratio.  A turning point can legitimately
        # be the D endpoint of one N and the C retracement endpoint of the next.
        ratio_items = list(analysis.ratio_annotations)
        ratio_set = set(ratio_items)
        for idx, label in analysis.ratios.items():
            if (idx, label) not in ratio_set:
                ratio_items.append((idx, label))
        ratio_groups: dict[int, list[str]] = {}
        for idx, label in ratio_items:
            if start <= idx < end:
                ratio_groups.setdefault(int(idx), []).append(str(label))
        pivot_by_index = {v.index: v for v in pivots}
        for idx, labels in ratio_groups.items():
            pivot = pivot_by_index.get(idx)
            if pivot is None:
                continue
            x0 = self._index_x(idx)
            y0 = self._price_y(pivot.price)
            for offset, label in enumerate(labels[:3]):
                y = y0 + (offset * 13.0 if pivot.kind == "L" else -offset * 13.0)
                self._draw_wave_tag(
                    p, x0, y, label, QColor(255, 224, 112),
                    above=(pivot.kind == "H"), anchor_is_y=True,
                    occupied=occupied_tags,
                )

        # Final Fibonacci display: use the last COMPLETE confirmed swing leg
        # (the final two pivots), not the earlier A-B leg of the current A-B-C.
        # 0% is the lower endpoint and 100% is the higher endpoint, matching
        # the user's reference chart for both rising and falling completed legs.
        fib_pivots = pivots[-2:] if len(pivots) >= 2 else []
        if len(fib_pivots) == 2:
            fib_first, fib_last = fib_pivots
            fx1 = self._index_x(float(fib_first.index))
            fx2 = self._index_x(float(fib_last.index))
            x_left = max(self._plot_rect.left(), min(fx1, fx2))
            x_right = min(self._plot_rect.right() - 2.0, max(fx1, fx2))
            if x_right > x_left + 2.0:
                fib_color = QColor(255, 194, 55, 255)
                fib_levels = last_completed_leg_fibonacci_levels(pivots)

                # Draw readable percentage/price labels directly on the line,
                # with a solid dark backing.  The right-aligned label lane keeps
                # the numbers visible and avoids the old collision-shift that
                # could move a label away from its own Fibonacci level.
                old_label_font = p.font()
                label_font = p.font()
                label_font.setPointSize(max(9, old_label_font.pointSize()))
                label_font.setBold(True)
                p.setFont(label_font)
                label_fm = p.fontMetrics()
                label_height = label_fm.height() + 2.0
                label_right = self._plot_rect.right() - 2.0

                for ratio, price in fib_levels:
                    y = self._price_y(float(price))
                    if y < self._plot_rect.top() + 2 or y > self._plot_rect.bottom() - 2:
                        continue
                    tag = f"{ratio:.1f}%  {float(price):.2f}"
                    tag_width = label_fm.horizontalAdvance(tag) + 8.0
                    # Put the label immediately to the right of the fib
                    # segment, on the same horizontal level.  If the remaining
                    # chart width is too small, clamp it inside the plot area.
                    label_left = min(
                        x_right + 3.0,
                        label_right - tag_width,
                    )
                    label_left = max(self._plot_rect.left() + 2.0, label_left)
                    label_rect = QRectF(
                        label_left,
                        y - label_height / 2.0,
                        tag_width,
                        label_height,
                    )
                    # The label sits on the same horizontal level immediately
                    # after the measured segment; its dark backing keeps it legible.
                    p.setPen(QPen(fib_color, 1.0, Qt.PenStyle.SolidLine))
                    p.drawLine(
                        QPointF(x_left, y),
                        QPointF(x_right, y),
                    )
                    p.fillRect(label_rect, QColor(16, 18, 22, 245))
                    p.setPen(fib_color)
                    p.drawText(
                        label_rect.adjusted(3.0, 0.0, -3.0, 0.0),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        tag,
                    )
                    occupied_tags.append(label_rect.adjusted(-1.0, -1.0, 1.0, 1.0))

                p.setFont(old_label_font)

        # Final wave ruler: keep the existing A-B-C analysis as its measuring
        # structure, use AB as the unit length, and project from C toward the
        # current D direction. Only the latest ruler is added; all historical
        # wave ratios and pattern/candle annotations above and below are intact.
        projection = analysis.projection
        if (
            projection is not None
            and projection.start is not None
            and projection.end is not None
            and projection.anchor is not None
        ):
            c_x = self._index_x(float(projection.anchor.index))
            ruler_left = max(self._plot_rect.left(), c_x)
            ruler_right = self._plot_rect.right() - 2.0
            if ruler_right > ruler_left + 2.0:
                ruler_color = QColor(255, 166, 48, 235)
                p.setPen(QPen(ruler_color, 1.0, Qt.PenStyle.DotLine))
                ruler_levels = calculate_wave_ruler_levels(
                    projection.start.price,
                    projection.end.price,
                    projection.anchor.price,
                )
                for ratio, price in ruler_levels:
                    y = self._price_y(float(price))
                    if y < self._plot_rect.top() + 2 or y > self._plot_rect.bottom() - 2:
                        continue
                    p.drawLine(
                        QPointF(ruler_left, y),
                        QPointF(ruler_right, y),
                    )
                    self._draw_wave_tag(
                        p,
                        self._plot_rect.right() - 24.0,
                        y,
                        f"{ratio:.1f}%",
                        ruler_color,
                        above=True,
                        anchor_is_y=True,
                        occupied=occupied_tags,
                    )

        # Numbered impulse/ABC candidates are shown only when the Elliott rules
        # pass; otherwise the swing structure remains unnumbered.
        visible_pivots = [v for v in pivots if start <= v.index < end]
        for pivot in visible_pivots:
            label = analysis.labels.get(pivot.index)
            if not label:
                continue
            x0 = self._index_x(pivot.index)
            y0 = self._price_y(pivot.price)
            color = QColor(255, 196, 70) if pivot.kind == "H" else QColor(90, 205, 220)
            p.setPen(QPen(color, 1.4))
            p.setBrush(QBrush(color))
            p.drawEllipse(QPointF(x0, y0), 2.6, 2.6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            wave_text = f"{label}浪" if str(label).isdigit() else str(label)
            self._draw_wave_tag(
                p, x0, y0, wave_text, color,
                above=(pivot.kind == "H"), occupied=occupied_tags,
            )

        # Draw structure names and 1-2-3 point numbers after the ruler labels.
        # Pattern names have lower priority than Fibonacci ratios.
        for structure in analysis.chart_patterns:
            if structure.end_index < start or structure.start_index >= end:
                continue
            color = pattern_colors.get(structure.kind, QColor(190, 190, 190))
            for n, marker in enumerate(structure.markers, start=1):
                if start <= marker.index < end:
                    marker_text = (
                        structure.marker_labels[n - 1]
                        if n - 1 < len(structure.marker_labels) else str(n)
                    )
                    self._draw_wave_tag(
                        p, self._index_x(marker.index), self._price_y(marker.price),
                        marker_text, color, above=(marker.kind == "H"),
                        occupied=occupied_tags,
                    )
            if start <= structure.label_index < end:
                self._draw_wave_tag(
                    p, self._index_x(structure.label_index),
                    self._price_y(structure.label_price), structure.label,
                    color, above=(structure.direction <= 0),
                    occupied=occupied_tags,
                )
            if (
                structure.kind in {"double_top", "double_bottom"}
                and structure.target_price is not None
                and start <= structure.end_index < end
            ):
                target_y = self._price_y(float(structure.target_price))
                if self._plot_rect.top() <= target_y <= self._plot_rect.bottom():
                    self._draw_wave_tag(
                        p, self._plot_rect.right() - 65.0, target_y,
                        f"量度目标 {float(structure.target_price):.2f}",
                        color, above=True, anchor_is_y=True,
                        occupied=occupied_tags,
                    )

        if analysis.preview is not None and pivots:
            idx, price = analysis.preview
            if start <= idx < end:
                self._draw_wave_tag(
                    p, self._index_x(idx), self._price_y(price), "进行中",
                    QColor(255, 215, 120), above=(pivots[-1].kind == "L"),
                    occupied=occupied_tags,
                )

        # Candle reversal names remain restricted to confirmed swing turns.
        grouped: dict[int, list[tuple[str, int]]] = {}
        for idx, label, direction in analysis.patterns:
            if start <= idx < end:
                grouped.setdefault(idx, []).append((label, direction))
        for idx, entries in grouped.items():
            if idx >= len(self.df):
                continue
            row = self.df.iloc[idx]
            x0 = self._index_x(idx)
            for offset, (label, direction) in enumerate(entries[:3]):
                if direction > 0:
                    anchor = self._price_y(float(row["low"])) + 12.0 + offset * 14.0
                    color = QColor(95, 220, 155)
                elif direction < 0:
                    anchor = self._price_y(float(row["high"])) - 10.0 - offset * 14.0
                    color = QColor(255, 115, 115)
                else:
                    anchor = self._price_y((float(row["high"]) + float(row["low"])) / 2.0) + 4.0 + offset * 14.0
                    color = QColor(235, 205, 120)
                self._draw_wave_tag(
                    p, x0, anchor, label, color,
                    above=(direction < 0), anchor_is_y=True,
                    occupied=occupied_tags,
                )

        p.restore()
        p.setFont(old_font)

    def _draw_wave_tag(
        self, p: QPainter, x: float, y: float, text: str, color: QColor,
        above: bool = True, anchor_is_y: bool = False,
        occupied: list[QRectF] | None = None,
    ):
        fm = p.fontMetrics()
        pad_x, pad_y = 3.0, 1.5
        height = fm.height() + pad_y * 2
        width = fm.horizontalAdvance(text) + pad_x * 2
        plot_width = max(24.0, self._plot_rect.width() - 2.0)
        width = min(width, plot_width)

        if anchor_is_y:
            base_top = y - height if above else y
        else:
            base_top = y - height - 5.0 if above else y + 5.0

        left_base = max(
            self._plot_rect.left() + 1.0,
            min(x - width / 2.0, self._plot_rect.right() - width - 1.0),
        )
        top_min = self._plot_rect.top() + 1.0
        top_max = self._plot_rect.bottom() - height - 1.0
        step = height + 3.0
        preferred_sign = -1.0 if above else 1.0

        # Try the preferred side first, then the opposite side.  Small horizontal
        # shifts are a final fallback for several adjacent candles with labels.
        vertical_offsets = [0.0]
        for n in range(1, 13):
            vertical_offsets.extend((preferred_sign * step * n, -preferred_sign * step * n))
        horizontal_offsets = [0.0, -(width + 3.0), width + 3.0, -2.0 * (width + 3.0), 2.0 * (width + 3.0)]

        chosen: QRectF | None = None
        for dx in horizontal_offsets:
            left = max(
                self._plot_rect.left() + 1.0,
                min(left_base + dx, self._plot_rect.right() - width - 1.0),
            )
            for dy in vertical_offsets:
                top = max(top_min, min(base_top + dy, top_max))
                candidate = QRectF(left, top, width, height)
                padded = candidate.adjusted(-2.0, -1.0, 2.0, 1.0)
                if occupied is None or not any(padded.intersects(old) for old in occupied):
                    chosen = candidate
                    break
            if chosen is not None:
                break

        # Never let a lower-priority label cover an already placed percentage.
        if chosen is None:
            return

        if anchor_is_y:
            target_x = max(chosen.left(), min(x, chosen.right()))
            target_y = max(chosen.top(), min(y, chosen.bottom()))
            if abs(target_x-x) + abs(target_y-y) > height:
                leader_color = QColor(color)
                leader_color.setAlpha(100)
                p.setPen(QPen(leader_color, .7, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(x, y), QPointF(target_x, target_y))
        p.fillRect(chosen, QColor(16, 18, 22, 215))
        p.setPen(color)
        p.drawText(chosen, Qt.AlignmentFlag.AlignCenter, text)
        if occupied is not None:
            occupied.append(chosen.adjusted(-2.0, -1.0, 2.0, 1.0))
        return chosen

    def _draw_intraday_line(self, p: QPainter, data: pd.DataFrame, start: int):
        """Draw a classic Chinese-market time-sharing view from real 1-minute data.

        The pale line is the minute close path; the yellow line is a cumulative
        volume-weighted typical price.  The previous trading day's last close is
        shown as a dotted reference line when available.
        """
        if data.empty:
            return

        # Previous settlement/close reference.  The 1-minute dataset does not
        # always carry a dedicated pre-close field, so use the last minute close
        # from the preceding trading day when it exists.
        if start > 0:
            try:
                prev_close = float(self.df.iloc[start - 1]["close"])
                if isfinite(prev_close):
                    y = self._price_y(prev_close)
                    p.setPen(QPen(QColor(95, 100, 108), 1, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(self._plot_rect.left(), y), QPointF(self._plot_rect.right(), y))
                    p.setPen(QColor(145, 150, 158))
                    p.drawText(int(self._plot_rect.right() - 78), int(y - 3), f"昨收 {prev_close:.2f}")
            except Exception:
                pass

        # Minute-price path.
        closes = pd.to_numeric(data["close"], errors="coerce").to_numpy(float)
        p.setPen(QPen(QColor(120, 205, 255), 1.5))
        prev = None
        for j, val in enumerate(closes):
            if not isfinite(float(val)):
                prev = None
                continue
            pt = QPointF(self._index_x(start + j), self._price_y(float(val)))
            if prev is not None:
                p.drawLine(prev, pt)
            prev = pt

        # Cumulative intraday average.  A volume-weighted typical price is used
        # because it remains meaningful even when turnover units differ between
        # futures contracts.
        try:
            vols = pd.to_numeric(data.get("volume", pd.Series(index=data.index, dtype=float)), errors="coerce").fillna(0.0).to_numpy(float)
            highs = pd.to_numeric(data["high"], errors="coerce").to_numpy(float)
            lows = pd.to_numeric(data["low"], errors="coerce").to_numpy(float)
            typical = (highs + lows + closes) / 3.0
            valid_vol = np.where(np.isfinite(vols) & (vols > 0), vols, 0.0)
            cum_v = np.cumsum(valid_vol)
            cum_pv = np.cumsum(np.where(np.isfinite(typical), typical, 0.0) * valid_vol)
            fallback = pd.Series(closes).expanding().mean().to_numpy(float)
            avg = np.divide(cum_pv, cum_v, out=fallback.copy(), where=cum_v > 0)
            p.setPen(QPen(QColor(238, 200, 70), 1.2))
            prev = None
            for j, val in enumerate(avg):
                if not isfinite(float(val)):
                    prev = None
                    continue
                pt = QPointF(self._index_x(start + j), self._price_y(float(val)))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt
        except Exception:
            pass

        p.setPen(QColor(120, 205, 255))
        p.drawText(int(self._plot_rect.left() + 8), int(self._plot_rect.top() + 18), "分时")
        p.setPen(QColor(238, 200, 70))
        p.drawText(int(self._plot_rect.left() + 45), int(self._plot_rect.top() + 18), "均价")

    def _nice_price_ticks(self) -> tuple[list[float], float]:
        """Return human-friendly price ticks for the left axis.

        The previous grid divided the visible range into five arbitrary pieces,
        producing labels such as 7137.81 and 6521.41.  Trading terminals are much
        easier to scan when the axis uses round 1/2/2.5/5-style increments.
        """
        span = float(self._price_max - self._price_min)
        if not isfinite(span) or span <= 0:
            return [], 1.0
        raw = span / 5.0
        if raw <= 0 or not isfinite(raw):
            return [], 1.0
        decade = 10.0 ** floor(log10(raw))
        candidates = [x * decade for x in (1.0, 2.0, 2.5, 5.0, 10.0)]
        step = min(candidates, key=lambda x: abs(log10(x / raw)))
        first = ceil(self._price_min / step - 1e-10) * step
        last = floor(self._price_max / step + 1e-10) * step
        if last < first:
            return [], step
        count = int(round((last - first) / step)) + 1
        ticks = [first + i * step for i in range(max(0, min(count, 20)))]
        return ticks, step

    @staticmethod
    def _format_axis_price(value: float, step: float) -> str:
        # Integer prices are shown without trailing .0/.00.  Very small-price
        # contracts retain only the precision genuinely required by the tick step.
        if abs(value - round(value)) < 1e-8:
            return f"{int(round(value))}"
        if step >= 1:
            decimals = 1
        elif step >= 0.1:
            decimals = 2
        elif step >= 0.01:
            decimals = 3
        else:
            decimals = 4
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".")

    def _draw_main_grid(self, p: QPainter):
        ticks, step = self._nice_price_ticks()
        fm = p.fontMetrics()
        axis_w = max(48.0, self._plot_rect.left() - 6.0)
        for price in ticks:
            y = self._price_y(price)
            if y < self._plot_rect.top() - 1 or y > self._plot_rect.bottom() + 1:
                continue
            p.setPen(QPen(QColor(50, 54, 60), 1))
            p.drawLine(self._plot_rect.left(), y, self._plot_rect.right(), y)
            p.setPen(QColor(140, 145, 150))
            label = self._format_axis_price(price, step)
            label_rect = QRectF(2.0, y - fm.height() / 2 - 1.0, axis_w - 4.0, fm.height() + 2.0)
            p.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

    def _visible_datetimes(self, start: int, end: int) -> pd.Series:
        if end <= start or self.df.empty:
            return pd.Series(dtype="datetime64[ns]")
        view = self.df.iloc[start:end]
        if "datetime" in view.columns:
            return pd.to_datetime(view["datetime"], errors="coerce").reset_index(drop=True)
        if "trading_day" in view.columns:
            return pd.to_datetime(view["trading_day"], errors="coerce").reset_index(drop=True)
        try:
            return pd.Series(pd.to_datetime(view.index, errors="coerce")).reset_index(drop=True)
        except Exception:
            return pd.Series(dtype="datetime64[ns]")

    def _draw_time_axis(self, p: QPainter, start: int, end: int):
        """Draw a compact, semantic time scale below every K-line window.

        Short intraday windows use HH:MM, multi-day windows use dates, ordinary
        daily charts use month boundaries (first label YYYY/MM, then MM), and
        long history uses years. Labels are thinned by pixel distance so zooming
        never creates an unreadable wall of dates.
        """
        if not self._panel_rects or end <= start:
            return
        times = self._visible_datetimes(start, end)
        valid = times.dropna()
        if valid.empty:
            return

        first_dt = pd.Timestamp(valid.iloc[0])
        last_dt = pd.Timestamp(valid.iloc[-1])
        span = last_dt - first_dt
        n = len(times)
        if n <= 0:
            return

        # Build semantically meaningful candidates before applying pixel thinning.
        candidates: list[tuple[int, str]] = []
        if span <= pd.Timedelta(days=2):
            target_count = max(2, min(8, int(self._plot_rect.width() // 95)))
            indices = sorted(set(int(round(x)) for x in np.linspace(0, n - 1, target_count)))
            multi_day = first_dt.date() != last_dt.date()
            for j in indices:
                if j >= n or pd.isna(times.iloc[j]):
                    continue
                dt = pd.Timestamp(times.iloc[j])
                label = dt.strftime("%m/%d %H:%M") if multi_day else dt.strftime("%H:%M")
                candidates.append((j, label))
        elif span <= pd.Timedelta(days=60):
            prev_day = None
            for j, raw in enumerate(times):
                if pd.isna(raw):
                    continue
                dt = pd.Timestamp(raw)
                day = dt.date()
                if day != prev_day:
                    label = dt.strftime("%Y/%m/%d") if not candidates else dt.strftime("%m/%d")
                    candidates.append((j, label))
                    prev_day = day
        elif span <= pd.Timedelta(days=900):
            prev_month = None
            prev_year = None
            for j, raw in enumerate(times):
                if pd.isna(raw):
                    continue
                dt = pd.Timestamp(raw)
                month_key = (dt.year, dt.month)
                if month_key != prev_month:
                    if not candidates or dt.year != prev_year:
                        label = dt.strftime("%Y/%m")
                    else:
                        label = dt.strftime("%m")
                    candidates.append((j, label))
                    prev_month = month_key
                    prev_year = dt.year
        else:
            prev_year = None
            for j, raw in enumerate(times):
                if pd.isna(raw):
                    continue
                dt = pd.Timestamp(raw)
                if dt.year != prev_year:
                    candidates.append((j, dt.strftime("%Y")))
                    prev_year = dt.year

        if not candidates:
            return

        fm = p.fontMetrics()
        axis_y = self._panel_rects[-1].bottom()
        text_y = axis_y + 4.0
        p.setPen(QPen(QColor(58, 62, 68), 1))
        p.drawLine(QPointF(self._plot_rect.left(), axis_y), QPointF(self._plot_rect.right(), axis_y))

        # Minimum visual distance between labels.  Wider labels automatically
        # demand more room; this keeps month/day labels legible while zooming.
        last_right = -1e9
        for local_index, label in candidates:
            idx = start + local_index
            x = self._index_x(idx)
            if x < self._plot_rect.left() - 2 or x > self._plot_rect.right() + 2:
                continue
            text_w = float(fm.horizontalAdvance(label) + 8)
            left = max(self._plot_rect.left(), min(x - text_w / 2, self._plot_rect.right() - text_w))
            if left < last_right + 10.0:
                continue
            p.setPen(QPen(QColor(82, 86, 94), 1))
            p.drawLine(QPointF(x, axis_y), QPointF(x, axis_y + 4.0))
            p.setPen(QColor(170, 174, 182))
            box = QRectF(left, text_y, text_w, fm.height() + 2.0)
            p.drawText(box, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)
            last_right = left + text_w

    def _draw_last_price(self, p: QPainter, display_partial: dict[str, float] | None):
        if not display_partial or not self.follow_latest or self.end_offset != 0:
            return
        price = float(display_partial.get("close", 0.0))
        if not isfinite(price):
            return
        y = self._price_y(price)
        o = float(display_partial.get("open", price))
        color = QColor(230, 70, 75) if price >= o else QColor(40, 190, 125)
        p.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(self._plot_rect.left(), y), QPointF(self._plot_rect.right(), y))
        text = f" {price:.2f} "
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 4
        h = fm.height() + 2
        # Show the live-price badge on the far-left price axis rather than
        # on the right edge of the chart.  This intentionally overlays the
        # ordinary y-axis tick label at the current price, like a trading
        # terminal price marker.
        box = QRectF(0, y - h / 2, w, h)
        p.fillRect(box, color)
        p.setPen(QColor(255, 255, 255))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _ma_palette(self) -> list[QColor]:
        # Keep this palette in one place so the legend color always matches the
        # actual moving-average line color.
        return [
            QColor(240, 200, 50),   # yellow
            QColor(80, 170, 255),   # blue
            QColor(230, 90, 220),   # magenta
            QColor(225, 225, 225),  # light grey
            QColor(255, 140, 80),   # orange
            QColor(120, 220, 220),  # cyan
        ]

    def _draw_ma(self, p: QPainter, start: int):
        if not self.show_ma:
            return
        palette = self._ma_palette()
        source = self._source_until_current()
        for k, period in enumerate(self.ma_periods):
            s = sma(source["close"], period).iloc[start:self._end]
            p.setPen(QPen(palette[k % len(palette)], 1))
            prev = None
            for j, val in enumerate(s):
                if not isfinite(float(val)):
                    continue
                pt = QPointF(self._index_x(start + j), self._price_y(float(val)))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt

    def _draw_ma_legend(self, p: QPainter):
        """Draw enabled MA periods and their latest values at the upper-left.

        Every label uses exactly the same color as its corresponding MA line,
        e.g. ``MA5 3250.40`` in yellow.  Labels wrap automatically on narrow
        charts and the crosshair information box is shifted below them.
        """
        if not self.show_ma or self.intraday_mode or not self.ma_periods:
            self._ma_legend_bottom = self._plot_rect.top()
            return

        source = self._source_until_current()
        if source.empty or "close" not in source.columns:
            self._ma_legend_bottom = self._plot_rect.top()
            return

        palette = self._ma_palette()
        fm = p.fontMetrics()
        left = self._plot_rect.left() + 8.0
        right = self._plot_rect.right() - 8.0
        x = left
        baseline = self._plot_rect.top() + fm.ascent() + 6.0
        line_h = fm.height() + 3.0
        max_bottom = baseline + fm.descent()

        selected_idx = self._selected_info_index(len(source))
        for k, period in enumerate(self.ma_periods):
            series = sma(source["close"], period)
            value = float(series.iloc[selected_idx]) if len(series) and 0 <= selected_idx < len(series) else float("nan")
            # MA values are periods rather than literal calendar days on
            # intraday charts, so show both the familiar MA name and an explicit
            # “N周期” explanation.  The short swatch and text use exactly the
            # same color as the corresponding MA line.
            text = (
                f"MA{period}（{period}周期） {value:.2f}"
                if isfinite(value)
                else f"MA{period}（{period}周期） --"
            )
            swatch_w = 16.0
            gap = 5.0
            width = swatch_w + gap + fm.horizontalAdvance(text) + 18
            if x > left and x + width > right:
                x = left
                baseline += line_h
            color = palette[k % len(palette)]
            y_line = baseline - fm.ascent() * 0.38
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(x, y_line), QPointF(x + swatch_w, y_line))
            p.setPen(color)
            p.drawText(QPointF(x + swatch_w + gap, baseline), text)
            x += width
            max_bottom = max(max_bottom, baseline + fm.descent())

        self._ma_legend_bottom = max_bottom

    def _draw_boll_legend(self, p: QPainter):
        """Show BOLL parameters and the selected bar's values on the main chart."""
        if not self.show_boll or self.intraday_mode:
            return
        source = self._source_until_current()
        if source.empty or "close" not in source.columns:
            return
        bands = bollinger(source["close"])
        idx = self._selected_info_index(len(bands))
        if idx < 0 or idx >= len(bands):
            return
        row = bands.iloc[idx]
        fm = p.fontMetrics()
        # Place the BOLL legend below the MA legend; the OHLC hover box is
        # placed below both legends using _ma_legend_bottom.
        top = max(self._plot_rect.top() + 3.0, self._ma_legend_bottom + 2.0)
        baseline = top + fm.ascent()
        segments = [
            ("BOLL(20,2)  ", QColor(230, 210, 80)),
            (f"中轨 {float(row['BOLL']):.2f}  " if isfinite(float(row['BOLL'])) else "中轨 --  ", QColor(230, 210, 80)),
            (f"上轨 {float(row['UPPER']):.2f}  " if isfinite(float(row['UPPER'])) else "上轨 --  ", QColor(150, 150, 255)),
            (f"下轨 {float(row['LOWER']):.2f}" if isfinite(float(row['LOWER'])) else "下轨 --", QColor(150, 150, 255)),
        ]
        total_w = sum(fm.horizontalAdvance(text) + 3 for text, _color in segments) + 8
        p.fillRect(QRectF(self._plot_rect.left() + 4, top - 1, min(total_w, self._plot_rect.width() - 8), fm.height() + 3), QColor(14, 16, 20, 210))
        x = self._plot_rect.left() + 8
        for text, color in segments:
            p.setPen(color)
            p.drawText(QPointF(x, baseline), text)
            x += fm.horizontalAdvance(text) + 3
        self._ma_legend_bottom = max(self._ma_legend_bottom, baseline + fm.descent() + 2)

    def _draw_boll(self, p: QPainter, start: int):
        if not self.show_boll:
            return
        source = self._source_until_current()
        b = bollinger(source["close"]).iloc[start:self._end]
        if b.empty:
            return

        # Semi-transparent band between UPPER and LOWER, matching the original
        # requirement that BOLL is a main-chart overlay rather than a副图.
        upper_pts: list[QPointF] = []
        lower_pts: list[QPointF] = []
        for j, (up, lo) in enumerate(zip(b["UPPER"], b["LOWER"])):
            if not (isfinite(float(up)) and isfinite(float(lo))):
                continue
            x = self._index_x(start + j)
            upper_pts.append(QPointF(x, self._price_y(float(up))))
            lower_pts.append(QPointF(x, self._price_y(float(lo))))
        if len(upper_pts) >= 2 and len(lower_pts) >= 2:
            polygon = QPolygonF(upper_pts + list(reversed(lower_pts)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(110, 130, 210, 34)))
            p.drawPolygon(polygon)
            p.setBrush(Qt.BrushStyle.NoBrush)

        for name, color in [
            ("BOLL", QColor(230, 210, 80)),
            ("UPPER", QColor(150, 150, 255)),
            ("LOWER", QColor(150, 150, 255)),
        ]:
            p.setPen(QPen(color, 1))
            prev = None
            for j, val in enumerate(b[name]):
                if not isfinite(float(val)):
                    prev = None
                    continue
                pt = QPointF(self._index_x(start + j), self._price_y(float(val)))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt

    def _draw_volume_panel(self, p: QPainter, rect: QRectF, data: pd.DataFrame, start: int):
        """Draw volume bars plus three independently switchable overlay lines.

        The VMA5, VMA10 and open-interest switches live in the upper-right corner
        of this pane, so the controls remain visually tied to the content they
        affect instead of occupying a separate global toolbar row.
        """
        p.setPen(QPen(QColor(50, 54, 60), 1))
        p.drawRect(rect)

        def numeric_series(frame: pd.DataFrame, key: str) -> pd.Series:
            raw = frame[key] if key in frame.columns else pd.Series(index=frame.index, dtype=float)
            return pd.to_numeric(raw, errors="coerce")

        source = self._source_until_current()
        volume_source = numeric_series(source, "volume").fillna(0.0).copy()
        if (
            0 <= self.current_idx < len(volume_source)
            and self._paint_partial
            and "volume" in self._paint_partial
        ):
            try:
                volume_source.iloc[self.current_idx] = float(self._paint_partial["volume"])
            except (TypeError, ValueError, IndexError):
                pass

        vma_full = volume_ma(volume_source)
        vma_colors = {"VMA5": QColor(240, 200, 50), "VMA10": QColor(80, 170, 255)}
        vma_visible = vma_full.iloc[start:self._end]

        volume_values = numeric_series(data, "volume").fillna(0.0).to_numpy(float)
        oi_values = numeric_series(data, "open_interest").to_numpy(float)
        finite_oi = oi_values[np.isfinite(oi_values)]
        oi_available = bool(finite_oi.size)

        selected_idx = self._selected_info_index(len(self.df))
        if selected_idx < 0:
            selected_idx = self.current_idx
        if start <= selected_idx < start + len(data):
            info_row = data.iloc[selected_idx - start]
        else:
            info_row = self._row_for_index(selected_idx)

        def safe_number(row, key: str) -> float:
            try:
                value = row.get(key, float("nan")) if row is not None else float("nan")
                return float(value) if value is not None and not pd.isna(value) else float("nan")
            except (TypeError, ValueError, AttributeError):
                return float("nan")

        selected_volume = safe_number(info_row, "volume")
        volume_text = f"成交量 {selected_volume:,.0f}" if isfinite(selected_volume) else "成交量 --"
        selected_oi = safe_number(info_row, "open_interest")
        oi_text = f"持仓量 {selected_oi:,.0f}" if isfinite(selected_oi) else "持仓量 --"

        enabled_vmas: list[str] = []
        if self.show_volume_vma5:
            enabled_vmas.append("VMA5")
        if self.show_volume_vma10:
            enabled_vmas.append("VMA10")

        value_items: list[tuple[str, QColor, bool]] = []
        for column in enabled_vmas:
            if column not in vma_full.columns:
                continue
            try:
                value = float(vma_full.iloc[selected_idx][column]) if 0 <= selected_idx < len(vma_full) else float("nan")
            except (TypeError, ValueError, IndexError, KeyError):
                value = float("nan")
            value_text = f"{value:,.0f}" if isfinite(value) else "--"
            value_items.append((f"{column} {value_text}", vma_colors[column], True))
        if oi_available and self.show_open_interest_line:
            value_items.append((oi_text, QColor(245, 245, 245), True))

        # Header row: current volume value on the left, three compact toggle
        # buttons on the right.  Each button is clickable directly in the pane.
        fm = p.fontMetrics()
        line_h = fm.height() + 3.0
        button_h = max(18.0, float(fm.height() + 4))
        button_gap = 4.0
        button_specs = [
            ("vma5", "VMA5", self.show_volume_vma5, vma_colors["VMA5"]),
            ("vma10", "VMA10", self.show_volume_vma10, vma_colors["VMA10"]),
            ("open_interest", "持仓线", self.show_open_interest_line, QColor(245, 245, 245)),
        ]
        button_widths = {key: float(fm.horizontalAdvance(label) + 22) for key, label, _on, _color in button_specs}
        total_button_w = sum(button_widths.values()) + button_gap * (len(button_specs) - 1)
        button_y = rect.top() + 3.0
        x_right = rect.right() - 5.0
        toggle_rects: dict[str, QRectF] = {}
        for key, label, on, color in reversed(button_specs):
            w = button_widths[key]
            r = QRectF(x_right - w, button_y, w, button_h)
            toggle_rects[key] = r
            x_right = r.left() - button_gap
        self._volume_toggle_rects = toggle_rects

        # Dark header background extends far enough to cover both the fixed
        # switches and any wrapped value rows below them.
        left = rect.left() + 6.0
        first_row_right = max(left + 40.0, rect.right() - total_button_w - button_gap * 3 - 8.0)
        volume_max_text_w = max(20.0, first_row_right - left)

        # Value legend rows start below the switch row; this guarantees the
        # right-side controls never cover VMA/open-interest readings.
        rows: list[list[tuple[str, QColor, bool]]] = []
        current_row: list[tuple[str, QColor, bool]] = []
        current_width = 0.0
        max_width = max(40.0, rect.width() - 12.0)
        for item in value_items:
            text_item, _color, has_swatch = item
            item_width = fm.horizontalAdvance(text_item) + (22.0 if has_swatch else 10.0)
            if current_row and current_width + item_width > max_width:
                rows.append(current_row)
                current_row = []
                current_width = 0.0
            current_row.append(item)
            current_width += item_width
        if current_row:
            rows.append(current_row)

        header_rows = 1 + len(rows)
        legend_h = header_rows * line_h
        plot_top = rect.top() + 3.0 + legend_h + 5.0
        plot_bottom = rect.bottom() - 4.0
        usable_h = max(1.0, plot_bottom - plot_top)

        scale_parts = [volume_values]
        for column in enabled_vmas:
            if column in vma_visible.columns:
                scale_parts.append(pd.to_numeric(vma_visible[column], errors="coerce").to_numpy(float))
        finite_parts = [a[np.isfinite(a)] for a in scale_parts if np.isfinite(a).any()]
        all_volume_values = np.concatenate(finite_parts) if finite_parts else np.array([], dtype=float)
        volume_max = max(1.0, float(np.max(all_volume_values)) if all_volume_values.size else 1.0)

        def volume_y(value: float) -> float:
            return plot_bottom - max(0.0, float(value)) / volume_max * usable_h

        body_w = max(1.0, min(10.0, self._slot_step() * 0.55))
        for j, value in enumerate(volume_values):
            x = self._index_x(start + j)
            row = data.iloc[j]
            try:
                rising = float(row.get("close", 0.0)) >= float(row.get("open", 0.0))
            except (TypeError, ValueError):
                rising = True
            color = QColor(100, 125, 160) if rising else QColor(75, 100, 130)
            y = volume_y(value)
            p.fillRect(QRectF(x - body_w / 2, y, body_w, max(0.0, plot_bottom - y)), color)

        for column in enabled_vmas:
            if column not in vma_visible.columns:
                continue
            p.setPen(QPen(vma_colors[column], 1.35))
            previous: QPointF | None = None
            for j, value in enumerate(pd.to_numeric(vma_visible[column], errors="coerce").to_numpy(float)):
                if not isfinite(float(value)):
                    previous = None
                    continue
                point = QPointF(self._index_x(start + j), volume_y(float(value)))
                if previous is not None:
                    p.drawLine(previous, point)
                previous = point

        if oi_available and self.show_open_interest_line:
            lo, hi = float(np.nanmin(finite_oi)), float(np.nanmax(finite_oi))

            def oi_y(value: float) -> float:
                if not isfinite(value):
                    return float("nan")
                if hi <= lo:
                    return plot_top + usable_h * 0.5
                return plot_bottom - (value - lo) / (hi - lo) * usable_h

            p.setPen(QPen(QColor(245, 245, 245), 1.35))
            previous = None
            for j, value in enumerate(oi_values):
                y = oi_y(float(value))
                if not isfinite(y):
                    previous = None
                    continue
                point = QPointF(self._index_x(start + j), y)
                if previous is not None:
                    p.drawLine(previous, point)
                previous = point

        p.fillRect(
            QRectF(rect.left() + 3.0, rect.top() + 2.0, rect.width() - 6.0, legend_h + 4.0),
            QColor(14, 16, 20, 225),
        )

        # Current volume value remains at upper-left.
        p.setPen(QColor(185, 195, 208))
        baseline = rect.top() + 3.0 + fm.ascent()
        clipped_volume = fm.elidedText(volume_text, Qt.TextElideMode.ElideRight, int(volume_max_text_w))
        p.drawText(QPointF(left, baseline), clipped_volume)

        # Switches are painted last so their active/inactive state is always
        # unmistakable and remains clickable even when values change rapidly.
        for key, label, on, color in button_specs:
            r = toggle_rects[key]
            border = color if on else QColor(92, 98, 108)
            text_color = color if on else QColor(118, 124, 134)
            fill = QColor(36, 39, 45, 235) if on else QColor(25, 27, 31, 220)
            p.fillRect(r, fill)
            p.setPen(QPen(border, 1.0))
            p.drawRoundedRect(r, 3.0, 3.0)
            swatch_y = r.center().y()
            p.setPen(QPen(text_color, 1.6 if on else 1.0))
            p.drawLine(QPointF(r.left() + 5.0, swatch_y), QPointF(r.left() + 12.0, swatch_y))
            p.setPen(text_color)
            p.drawText(
                QRectF(r.left() + 15.0, r.top(), r.width() - 18.0, r.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

        # Active overlay values are shown from the second header row downward.
        for row_idx, row in enumerate(rows):
            row_baseline = rect.top() + 3.0 + (row_idx + 1) * line_h + fm.ascent()
            x = left
            for text_item, color, has_swatch in row:
                if has_swatch:
                    y_line = row_baseline - fm.ascent() * 0.34
                    p.setPen(QPen(color, 1.5))
                    p.drawLine(QPointF(x, y_line), QPointF(x + 16.0, y_line))
                    x += 21.0
                p.setPen(color)
                p.drawText(QPointF(x, row_baseline), text_item)
                x += fm.horizontalAdvance(text_item) + 10.0

    @staticmethod
    def _atr_series(full: pd.DataFrame, period: int = 14) -> pd.Series:
        """Classic Wilder ATR using only current and prior bars."""
        high = pd.to_numeric(full["high"], errors="coerce")
        low = pd.to_numeric(full["low"], errors="coerce")
        close = pd.to_numeric(full["close"], errors="coerce")
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        period = max(1, int(period))
        values = tr.to_numpy(float)
        atr = np.full(len(values), np.nan, dtype=float)
        if len(values) < period:
            return pd.Series(atr, index=full.index, name="ATR")
        seed = values[:period]
        if np.isfinite(seed).all():
            atr[period - 1] = float(seed.mean())
            for i in range(period, len(values)):
                if np.isfinite(values[i]) and np.isfinite(atr[i - 1]):
                    atr[i] = (atr[i - 1] * (period - 1) + values[i]) / period
        return pd.Series(atr, index=full.index, name="ATR")

    def _indicator_series(self, full: pd.DataFrame, name: str) -> list[tuple[str, pd.Series, QColor]]:
        if name == "MACD":
            m = macd(full["close"])
            cols = list(m.columns)
            out = []
            colors = [QColor(90, 180, 255), QColor(245, 190, 70), QColor(210, 100, 220)]
            for i, col in enumerate(cols[:3]):
                out.append((str(col), m[col], colors[i % len(colors)]))
            return out
        if name == "KDJ":
            k = kdj(full)
            colors = [QColor(90, 180, 255), QColor(245, 190, 70), QColor(210, 100, 220)]
            return [(str(col), k[col], colors[i % 3]) for i, col in enumerate(k.columns[:3])]
        if name == "RSI":
            return [("RSI", rsi(full["close"]), QColor(90, 180, 255))]
        if name == "WR":
            return [("WR", williams_r(full), QColor(90, 180, 255))]
        if name == "CCI":
            return [("CCI", cci(full), QColor(90, 180, 255))]
        if name == "ATR":
            return [("ATR", self._atr_series(full, 14), QColor(245, 190, 70))]
        return []

    def _draw_indicator_panel(self, p: QPainter, rect: QRectF, full: pd.DataFrame, start: int, name: str):
        p.setPen(QPen(QColor(50, 54, 60), 1))
        p.drawRect(rect)
        selected_idx = self._selected_info_index(len(full))
        if selected_idx < 0:
            return

        # MACD is a histogram plus DIF/DEA lines. Its value legend follows the
        # cursor-selected bar, not merely the last visible bar.
        if name == "MACD":
            m_full = macd(full["close"]).astype(float)
            m = m_full.iloc[start:self._end]
            if m.empty:
                return
            vals = m[["DIF", "DEA", "MACD"]].to_numpy(float).ravel()
            vals = vals[np.isfinite(vals)]
            if not vals.size:
                return
            lo = min(0.0, float(np.nanmin(vals)))
            hi = max(0.0, float(np.nanmax(vals)))
            if hi == lo:
                hi, lo = lo + 1.0, lo - 1.0
            pad = (hi - lo) * 0.08
            lo -= pad
            hi += pad

            def y_of(value: float) -> float:
                return rect.bottom() - (float(value) - lo) / (hi - lo) * rect.height()

            y0 = y_of(0.0)
            p.setPen(QPen(QColor(85, 90, 96), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(rect.left(), y0), QPointF(rect.right(), y0))

            hist = m["MACD"]
            bar_w = max(1.0, min(10.0, self._slot_step() * 0.58))
            for j, val in enumerate(hist):
                if not isfinite(float(val)):
                    continue
                x = self._index_x(start + j)
                yv = y_of(float(val))
                top = min(y0, yv)
                height = max(1.0, abs(yv - y0))
                color = QColor(230, 70, 75) if float(val) >= 0 else QColor(40, 190, 125)
                p.fillRect(QRectF(x - bar_w / 2, top, bar_w, height), color)

            for label, color in [("DIF", QColor(90, 180, 255)), ("DEA", QColor(245, 190, 70))]:
                p.setPen(QPen(color, 1))
                prev = None
                for j, val in enumerate(m[label]):
                    if not isfinite(float(val)):
                        prev = None
                        continue
                    pt = QPointF(self._index_x(start + j), y_of(float(val)))
                    if prev is not None:
                        p.drawLine(prev, pt)
                    prev = pt

            if selected_idx < len(m_full):
                selected = m_full.iloc[selected_idx]
                segments = [
                    ("MACD(12,26,9)  ", QColor(185, 195, 208)),
                    (f"DIF {float(selected['DIF']):.3f}  " if isfinite(float(selected['DIF'])) else "DIF --  ", QColor(90, 180, 255)),
                    (f"DEA {float(selected['DEA']):.3f}  " if isfinite(float(selected['DEA'])) else "DEA --  ", QColor(245, 190, 70)),
                    (f"柱 {float(selected['MACD']):.3f}" if isfinite(float(selected['MACD'])) else "柱 --", QColor(210, 100, 220)),
                ]
                self._draw_value_legend(p, rect, segments)
            return

        series_list = self._indicator_series(full, name)
        if not series_list:
            return
        sliced: list[tuple[str, pd.Series, QColor]] = []
        all_vals: list[np.ndarray] = []
        for label, series, color in series_list:
            s = series.iloc[start:self._end].astype(float)
            sliced.append((label, s, color))
            arr = s.to_numpy(float)
            arr = arr[np.isfinite(arr)]
            if arr.size:
                all_vals.append(arr)
        if not all_vals:
            return
        joined = np.concatenate(all_vals)
        lo, hi = float(np.nanmin(joined)), float(np.nanmax(joined))
        if hi == lo:
            hi = lo + 1.0
        pad = (hi - lo) * 0.05
        lo -= pad
        hi += pad
        for _label, series, color in sliced:
            p.setPen(QPen(color, 1))
            prev = None
            for j, val in enumerate(series):
                if not isfinite(float(val)):
                    prev = None
                    continue
                y = rect.bottom() - (float(val) - lo) / (hi - lo) * rect.height()
                pt = QPointF(self._index_x(start + j), y)
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt

        prefixes = {
            "KDJ": "KDJ(9,3,3)  ",
            "RSI": "RSI(14)  ",
            "WR": "WR(14)  ",
            "CCI": "CCI(20)  ",
            "ATR": "ATR(14)  ",
        }
        segments: list[tuple[str, QColor]] = [(prefixes.get(name, name + "  "), QColor(185, 195, 208))]
        for label, series, color in series_list:
            if selected_idx >= len(series):
                value = float("nan")
            else:
                try:
                    value = float(series.iloc[selected_idx])
                except Exception:
                    value = float("nan")
            if isfinite(value):
                value_text = f"{value:.2f}"
            else:
                value_text = "--"
            segments.append((f"{label} {value_text}  ", color))
        self._draw_value_legend(p, rect, segments)

    def _draw_value_legend(self, p: QPainter, rect: QRectF, segments: list[tuple[str, QColor]]):
        """Draw a compact, colored, hover-aware indicator legend in a panel."""
        if not segments:
            return
        fm = p.fontMetrics()
        left = rect.left() + 6.0
        max_width = max(40.0, rect.width() - 12.0)
        rows: list[list[tuple[str, QColor]]] = []
        current: list[tuple[str, QColor]] = []
        current_width = 0.0
        for text, color in segments:
            width = fm.horizontalAdvance(text) + 7.0
            if current and current_width + width > max_width:
                rows.append(current)
                current = []
                current_width = 0.0
            current.append((text, color))
            current_width += width
        if current:
            rows.append(current)

        line_h = fm.height() + 2.0
        top = rect.top() + 3.0
        for row_idx, row in enumerate(rows):
            y_top = top + row_idx * line_h
            row_width = min(max_width, sum(fm.horizontalAdvance(text) + 7.0 for text, _color in row))
            p.fillRect(QRectF(left - 3.0, y_top - 1.0, row_width + 4.0, line_h + 1.0), QColor(14, 16, 20, 220))
            baseline = y_top + fm.ascent()
            x = left
            for text, color in row:
                p.setPen(color)
                p.drawText(QPointF(x, baseline), text)
                x += fm.horizontalAdvance(text) + 7.0

    def _draw_separators(self, p: QPainter):
        if not self._separator_ys:
            return
        for y in self._separator_ys:
            p.fillRect(QRectF(self._plot_rect.left(), y - 2, self._plot_rect.width(), 4), QColor(65, 68, 74))

    def _ray_endpoint(self, a: QPointF, b: QPointF) -> QPointF:
        """Return the visible endpoint of the ray starting at *a* through *b*."""
        dx = b.x() - a.x()
        dy = b.y() - a.y()
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return b
        candidates: list[float] = []
        if dx > 1e-9:
            candidates.append((self._plot_rect.right() - a.x()) / dx)
        elif dx < -1e-9:
            candidates.append((self._plot_rect.left() - a.x()) / dx)
        if dy > 1e-9:
            candidates.append((self._plot_rect.bottom() - a.y()) / dy)
        elif dy < -1e-9:
            candidates.append((self._plot_rect.top() - a.y()) / dy)
        valid: list[tuple[float, QPointF]] = []
        for t in candidates:
            if t < 0:
                continue
            pt = QPointF(a.x() + dx * t, a.y() + dy * t)
            if (self._plot_rect.left() - 1 <= pt.x() <= self._plot_rect.right() + 1
                    and self._plot_rect.top() - 1 <= pt.y() <= self._plot_rect.bottom() + 1):
                valid.append((t, pt))
        return min(valid, key=lambda x: x[0])[1] if valid else b

    def _draw_backtest_markers(self, p: QPainter):
        """Draw selected-backtest buy/sell executions directly on candles."""
        if not self._backtest_marker_indices:
            return
        step = max(3.0, min(8.0, self._slot_step() * 0.42))
        fm = p.fontMetrics()
        for idx, marker in self._backtest_marker_indices:
            if idx < self._start or idx >= self._end:
                continue
            try:
                price = float(marker.get("price"))
            except Exception:
                continue
            x = self._index_x(idx)
            side = str(marker.get("side", "")).lower()
            action = str(marker.get("action", ""))
            is_buy = side == "buy"
            color = QColor(235, 70, 75) if is_buy else QColor(35, 185, 115)
            y = self._price_y(price)
            # Buy triangle points upward from below the execution price; sell
            # triangle points downward from above it. This keeps the candle body visible.
            if is_buy:
                tip_y = y + 2
                poly = QPolygonF([
                    QPointF(x, tip_y),
                    QPointF(x - step, tip_y + step * 1.35),
                    QPointF(x + step, tip_y + step * 1.35),
                ])
                text_y = tip_y + step * 1.35 + fm.height()
                label = "买"
            else:
                tip_y = y - 2
                poly = QPolygonF([
                    QPointF(x, tip_y),
                    QPointF(x - step, tip_y - step * 1.35),
                    QPointF(x + step, tip_y - step * 1.35),
                ])
                text_y = tip_y - step * 1.35 - 2
                label = "卖"
            p.setPen(QPen(color, 1))
            p.setBrush(QBrush(color))
            p.drawPolygon(poly)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Keep labels concise; tooltip-like strategy details remain in the
            # result table/description panel.
            p.drawText(int(x - fm.horizontalAdvance(label) / 2), int(text_y), label)

    def _draw_drawings(self, p: QPainter):
        p.setPen(QPen(QColor(235, 185, 80), 1))
        for d in self.drawings:
            a = QPointF(self._index_x(d.x1), self._price_y(d.y1))
            b = QPointF(self._index_x(d.x2), self._price_y(d.y2))
            if d.kind == "水平线":
                p.drawLine(QPointF(self._plot_rect.left(), a.y()), QPointF(self._plot_rect.right(), a.y()))
            elif d.kind == "垂直线":
                p.drawLine(QPointF(a.x(), self._plot_rect.top()), QPointF(a.x(), self._plot_rect.bottom()))
            elif d.kind == "矩形":
                p.drawRect(QRectF(a, b).normalized())
            elif d.kind == "黄金分割":
                x1, x2 = min(a.x(), b.x()), max(a.x(), b.x())
                y1_price, y2_price = d.y1, d.y2
                for r in (0, .236, .382, .5, .618, .786, 1):
                    y = a.y() + (b.y() - a.y()) * r
                    price = y1_price + (y2_price - y1_price) * r
                    p.drawLine(QPointF(x1, y), QPointF(x2, y))
                    label = f"{r*100:.1f}%  {price:.2f}"
                    p.drawText(int(x2 + 4), int(y - 2), label)
            elif d.kind == "波浪尺":
                cx, cy = getattr(d, "c_x", None), getattr(d, "c_y", None)
                if cx is not None and cy is not None:
                    self._draw_wave_ruler(
                        p, a.x(), a.y(), b.x(), b.y(),
                        self._index_x(float(cx)), self._price_y(float(cy)),
                        c_price=float(cy), live_progress=True,
                    )
            elif d.kind == "射线":
                p.drawLine(a, self._ray_endpoint(a, b))
            else:
                p.drawLine(a, b)
        if self.tool == "波浪尺" and self._plot_rect.contains(self._mouse):
            if len(self._wave_ruler_draft) == 1:
                ax, ay = self._wave_ruler_draft[0]
                a = QPointF(self._index_x(ax), self._price_y(ay))
                p.setPen(QPen(QColor(55, 205, 135), 1.4, Qt.PenStyle.DashLine))
                p.drawLine(a, self._mouse)
            elif len(self._wave_ruler_draft) == 2:
                (ax, ay), (bx, by) = self._wave_ruler_draft
                self._draw_wave_ruler(
                    p,
                    self._index_x(ax), self._price_y(ay),
                    self._index_x(bx), self._price_y(by),
                    self._mouse.x(), self._mouse.y(),
                    c_price=self._y_price(self._mouse.y()),
                    live_progress=False, preview=True,
                )
        elif self._draft is not None and self._plot_rect.contains(self._mouse):
            a = QPointF(self._index_x(self._draft[0]), self._price_y(self._draft[1]))
            p.drawLine(a, self._mouse)

    def _draw_wave_ruler(
        self, p: QPainter,
        ax: float, ay: float, bx: float, by: float,
        cx: float, cy: float, *,
        c_price: float, live_progress: bool = False, preview: bool = False,
    ) -> None:
        """Draw the A-B-C ruler and its C-origin extension targets."""
        levels = calculate_wave_ruler_levels(
            self._y_price(ay), self._y_price(by), c_price
        )
        p.save()
        p.setClipRect(self._plot_rect)

        # A-B-C swing polyline, matching the green structure in the reference.
        base_pen = QPen(
            QColor(45, 190, 105, 230), 1.5,
            Qt.PenStyle.DashLine if preview else Qt.PenStyle.SolidLine,
        )
        p.setPen(base_pen)
        p.drawLine(QPointF(ax, ay), QPointF(bx, by))
        p.drawLine(QPointF(bx, by), QPointF(cx, cy))

        p.setBrush(QBrush(QColor(45, 190, 105, 235)))
        for x0, y0 in ((ax, ay), (bx, by), (cx, cy)):
            p.drawEllipse(QPointF(x0, y0), 3.0, 3.0)

        p.setPen(QColor(230, 238, 242))
        p.drawText(QPointF(ax - 10, ay + (14 if ay < self._plot_rect.center().y() else -8)), "A")
        p.drawText(QPointF(bx - 10, by + (14 if by < self._plot_rect.center().y() else -8)), "B")
        p.drawText(QPointF(cx + 5, cy + (14 if cy < self._plot_rect.center().y() else -8)), "C")

        fm = p.fontMetrics()
        if levels:
            x_left = max(cx, self._plot_rect.left())
            x_right = self._plot_rect.right() - 2.0
            ruler_pen = QPen(
                QColor(245, 170, 45, 225), 1.0,
                Qt.PenStyle.DotLine if preview else Qt.PenStyle.SolidLine,
            )
            p.setPen(ruler_pen)
            for ratio, price in levels:
                y = self._price_y(float(price))
                if y < self._plot_rect.top() + 2 or y > self._plot_rect.bottom() - 2:
                    continue
                p.drawLine(QPointF(x_left, y), QPointF(x_right, y))
                label = f"{ratio:.1f}%  {float(price):.2f}"
                label_w = fm.horizontalAdvance(label) + 8
                label_h = fm.height() + 2
                label_x = max(x_left + 2, x_right - label_w - 2)
                label_rect = QRectF(label_x, y - label_h / 2, label_w, label_h)
                p.fillRect(label_rect, QColor(20, 22, 26, 205))
                p.setPen(QColor(255, 190, 80))
                p.drawText(
                    label_rect,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
                p.setPen(ruler_pen)

        # D is the current replay close: the third leg remains visibly "in
        # progress" and advances with replay instead of being a static fourth click.
        if live_progress and self.current_idx >= 0:
            row = self._row_for_index(self.current_idx)
            if row is not None:
                try:
                    d_price = float(row.get("close"))
                    c_index = self._x_index(cx)
                    d_x = self._index_x(self.current_idx)
                    if isfinite(d_price) and self.current_idx >= c_index:
                        d_y = self._price_y(d_price)
                        p.setPen(QPen(QColor(45, 190, 105, 235), 1.5))
                        p.drawLine(QPointF(cx, cy), QPointF(d_x, d_y))
                        p.setBrush(QBrush(QColor(45, 190, 105, 245)))
                        p.drawEllipse(QPointF(d_x, d_y), 3.0, 3.0)
                        if self._plot_rect.contains(QPointF(d_x, d_y)):
                            p.setPen(QColor(45, 205, 125))
                            p.drawText(QPointF(d_x - 8, d_y - 8), "D")
                            text = "进行中"
                            tx = d_x + 8
                            if tx + fm.horizontalAdvance(text) > self._plot_rect.right():
                                tx = d_x - fm.horizontalAdvance(text) - 8
                            p.drawText(QPointF(tx, d_y + fm.height() / 2), text)
                except (TypeError, ValueError, OverflowError):
                    pass

        p.restore()

    def _draw_trade_lines(self, p: QPainter):
        styles = {
            "成本": (QColor(245, 150, 50), Qt.PenStyle.DashLine),
            "止盈": (QColor(50, 205, 120), Qt.PenStyle.SolidLine),
            "止损": (QColor(235, 70, 70), Qt.PenStyle.SolidLine),
        }

        base_font = p.font()
        pnl_font = p.font()
        pnl_font.setBold(True)
        if pnl_font.pointSizeF() > 0:
            pnl_font.setPointSizeF(pnl_font.pointSizeF() + 1.2)
        elif pnl_font.pixelSize() > 0:
            pnl_font.setPixelSize(pnl_font.pixelSize() + 2)

        def draw_price_and_pnl(name: str, value: float, y: float, color: QColor):
            """Draw P/L immediately to the left of the TP/SL price label."""
            p.setFont(base_font)
            p.setPen(color)
            price_text = f"{name} {float(value):.2f}"
            price_w = p.fontMetrics().horizontalAdvance(price_text)
            price_x = max(
                self._plot_rect.left() + 4.0,
                self._plot_rect.right() - price_w - 6.0,
            )

            pnl_text = ""
            if name in {"止盈", "止损"}:
                pnl_text = self._format_trade_pnl(self._trade_line_pnl(float(value)))

            if pnl_text:
                # The P/L number is deliberately larger and bold, and sits to the
                # left of the stop/take-profit price so it is visible at a glance.
                p.setFont(pnl_font)
                pnl_w = p.fontMetrics().horizontalAdvance(pnl_text)
                pnl_x = max(self._plot_rect.left() + 4.0, price_x - pnl_w - 12.0)
                p.drawText(int(pnl_x), int(y - 3), pnl_text)

            p.setFont(base_font)
            p.drawText(int(price_x), int(y - 3), price_text)

        # Existing TP/SL lines move directly with the mouse while dragging.
        # The cost line is different: dragging it is only a shortcut for creating
        # a new TP or SL.  Keep the real cost line fixed and draw the prospective
        # TP/SL at the live mouse price, with P/L visible from the *first* drag.
        cost_drag_preview = (
            self._drag_trade_line == "成本"
            and self._trade_drag_preview is not None
            and self._trade_lines.get("成本") is not None
            and self._trade_direction is not None
        )

        for name, value in self._trade_lines.items():
            if value is None:
                continue
            draw_value = float(value)
            is_live_drag = (
                self._drag_trade_line == name
                and self._trade_drag_preview is not None
                and name != "成本"
            )
            if is_live_drag:
                draw_value = float(self._trade_drag_preview)
            y = self._price_y(draw_value)
            color, style = styles[name]
            p.setPen(QPen(color, 1.6 if is_live_drag else 1.0, style))
            p.drawLine(self._plot_rect.left(), y, self._plot_rect.right(), y)
            draw_price_and_pnl(name, draw_value, y, color)

        if cost_drag_preview:
            preview = float(self._trade_drag_preview)
            cost = float(self._trade_lines["成本"])
            # Long: above cost = TP, below cost = SL.  Short is the reverse.
            if float(self._trade_direction) >= 0:
                preview_name = "止盈" if preview >= cost else "止损"
            else:
                preview_name = "止盈" if preview <= cost else "止损"
            preview_color, preview_style = styles[preview_name]
            y = self._price_y(preview)
            p.setPen(QPen(preview_color, 1.6, preview_style))
            p.drawLine(self._plot_rect.left(), y, self._plot_rect.right(), y)
            draw_price_and_pnl(preview_name, preview, y, preview_color)

        # Defensive fallback for a future draggable trade-line type that is not
        # present in _trade_lines.
        if (
            self._trade_drag_preview is not None
            and self._drag_trade_line
            and self._drag_trade_line not in self._trade_lines
        ):
            y = self._price_y(float(self._trade_drag_preview))
            preview_color = QColor(255, 220, 120)
            p.setPen(QPen(preview_color, 1.2, Qt.PenStyle.DotLine))
            p.drawLine(self._plot_rect.left(), y, self._plot_rect.right(), y)
            draw_price_and_pnl(self._drag_trade_line, float(self._trade_drag_preview), y, preview_color)

        p.setFont(base_font)
        for label, value in self._limit_lines:
            y = self._price_y(float(value))
            p.setPen(QPen(QColor(130, 150, 255), 1, Qt.PenStyle.DotLine))
            p.drawLine(self._plot_rect.left(), y, self._plot_rect.right(), y)
            p.drawText(int(self._plot_rect.left() + 4), int(y - 3), label)

    def _row_for_index(self, idx: int):
        if idx < 0 or idx >= len(self.df):
            return None
        row = self.df.iloc[idx].copy()
        if idx == self.current_idx and self._paint_partial:
            for k, v in self._paint_partial.items():
                if k in row.index:
                    row[k] = v
        return row

    def _selected_info_index(self, source_length: int | None = None) -> int:
        """Return the bar index for legends: hovered bar, or current replay bar."""
        if self.df.empty or self.current_idx < 0:
            return -1
        max_idx = min(self.current_idx, len(self.df) - 1)
        idx = max_idx
        mouse_x = float(self._mouse.x())
        mouse_y = float(self._mouse.y())
        in_panel = (
            bool(self._panel_rects)
            and self._plot_rect.left() <= mouse_x <= self._plot_rect.right()
            and any(rect.top() <= mouse_y <= rect.bottom() for rect in self._panel_rects)
        )
        if in_panel and self._end > self._start:
            idx = int(round(self._x_index(mouse_x)))
            idx = max(self._start, min(self._end - 1, idx))
        idx = min(idx, max_idx)
        if source_length is not None:
            idx = min(idx, int(source_length) - 1)
        return idx if idx >= 0 else -1

    def _draw_crosshair(self, p: QPainter):
        if not self.crosshair_enabled or not self._panel_rects:
            return
        total = QRectF(
            self._panel_rects[0].left(),
            self._panel_rects[0].top(),
            self._panel_rects[0].width(),
            self._panel_rects[-1].bottom() - self._panel_rects[0].top(),
        )
        if not total.contains(self._mouse):
            return

        idx = int(round(self._x_index(self._mouse.x())))
        idx = max(self._start, min(self._end - 1, idx))
        row = self._row_for_index(idx)
        if row is None:
            return

        # Brighter crosshair so it remains visible on dense candles/indicators.
        x = self._index_x(idx)
        p.setPen(QPen(QColor(145, 150, 160), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x, total.top()), QPointF(x, total.bottom()))
        active_rect = next((r for r in self._panel_rects if r.top() <= self._mouse.y() <= r.bottom()), self._plot_rect)
        p.drawLine(QPointF(active_rect.left(), self._mouse.y()), QPointF(active_rect.right(), self._mouse.y()))

        if active_rect == self._plot_rect:
            price = self._y_price(self._mouse.y())
            p.fillRect(QRectF(0, self._mouse.y() - 10, max(54, self._plot_rect.left() - 2), 20), QColor(45, 48, 54, 235))
            p.setPen(QColor(238, 238, 238))
            p.drawText(QRectF(1, self._mouse.y() - 10, max(52, self._plot_rect.left() - 4), 20), Qt.AlignmentFlag.AlignCenter, f"{price:.2f}")

        # Resolve time robustly whether datetime is a column or the DataFrame index.
        dt_value = row.get("datetime", None)
        if dt_value is None or (isinstance(dt_value, float) and pd.isna(dt_value)):
            try:
                dt_value = self.df.index[idx]
            except Exception:
                dt_value = ""
        try:
            dt = pd.Timestamp(dt_value)
            dt_text = dt.strftime("%Y-%m-%d %H:%M") if not pd.isna(dt) else ""
        except Exception:
            dt_text = str(dt_value or "")

        def _num(name: str) -> float:
            try:
                value = row.get(name, 0.0)
                if value is None or pd.isna(value):
                    return 0.0
                return float(value)
            except Exception:
                return 0.0

        o, h, l, c = (_num("open"), _num("high"), _num("low"), _num("close"))
        # Standard market convention: 涨跌/涨跌幅 are measured from the
        # previous K-line close, not from the current K-line open.
        prev_close = o
        if idx > 0:
            try:
                pv = self.df.iloc[idx - 1].get("close", o)
                if pv is not None and not pd.isna(pv):
                    prev_close = float(pv)
            except Exception:
                pass
        chg = c - prev_close
        pct = (chg / prev_close * 100.0) if prev_close else 0.0
        vol = _num("volume")
        amount = _num("amount")
        oi = _num("open_interest")

        # Two compact rows are used instead of one very long string.  The old
        # single-line box was easy to clip on a narrow chart and could look as
        # though no candle information had been displayed at all.
        line1 = f"{dt_text}   开 {o:.2f}   高 {h:.2f}   低 {l:.2f}   收 {c:.2f}"
        line2 = f"涨跌 {chg:+.2f} ({pct:+.2f}%)   量 {vol:,.0f}   额 {amount:,.0f}   持仓 {oi:,.0f}"
        fm = p.fontMetrics()
        box_left = self._plot_rect.left() + 6
        # Keep candle information below the permanent MA legend.
        box_top = max(self._plot_rect.top() + 5, self._ma_legend_bottom + 5)
        box_w = max(120.0, self._plot_rect.width() - 12.0)
        box_h = fm.height() * 2 + 12
        box = QRectF(box_left, box_top, box_w, box_h)
        p.fillRect(box, QColor(14, 16, 20, 232))
        p.setPen(QColor(238, 240, 244))
        p.drawText(QRectF(box.left() + 7, box.top() + 2, box.width() - 14, fm.height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line1)
        p.setPen(QColor(205, 210, 218))
        p.drawText(QRectF(box.left() + 7, box.top() + fm.height() + 4, box.width() - 14, fm.height() + 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line2)

        # Bottom time tag follows the selected candle.
        if dt_text:
            label = f" {dt_text} "
            tw = fm.horizontalAdvance(label) + 6
            tx = max(total.left(), min(x - tw / 2, total.right() - tw))
            tbox = QRectF(tx, total.bottom() + 2, tw, fm.height() + 3)
            p.fillRect(tbox, QColor(55, 58, 64))
            p.setPen(QColor(235, 235, 235))
            p.drawText(tbox, Qt.AlignmentFlag.AlignCenter, label)

    # ----------------------------- input -----------------------------
    def _hit_separator(self, pos: QPointF) -> int | None:
        if pos.x() < self._plot_rect.left() or pos.x() > self._plot_rect.right():
            return None
        for i, y in enumerate(self._separator_ys):
            if abs(pos.y() - y) <= 5:
                return i
        return None

    def _resize_separator(self, sep_index: int, y: float):
        count = len(self._panel_rects)
        if count < 2 or sep_index < 0 or sep_index >= count - 1:
            return
        weights = list(self._panel_weights[count])
        top = self._panel_rects[0].top()
        bottom = self._panel_rects[-1].bottom()
        total = max(1.0, bottom - top - 6.0 * (count - 1))
        # Convert the drag position to a cumulative weight while enforcing a 10%
        # minimum per panel.
        before = sum(weights[:sep_index])
        after = sum(weights[sep_index + 2:])
        target_cum = (y - top - 6.0 * sep_index) / total
        left_w = max(0.10, target_cum - before)
        right_w = max(0.10, 1.0 - after - before - left_w)
        pair_total = 1.0 - before - after
        if left_w + right_w > pair_total:
            scale = pair_total / (left_w + right_w)
            left_w *= scale; right_w *= scale
        weights[sep_index] = left_w
        weights[sep_index + 1] = right_w
        s = sum(weights)
        self._panel_weights[count] = [w / s for w in weights]
        self.update()

    @staticmethod
    def _point_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
        vx, vy = b.x() - a.x(), b.y() - a.y()
        wx, wy = p.x() - a.x(), p.y() - a.y()
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return hypot(wx, wy)
        t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
        qx, qy = a.x() + t * vx, a.y() + t * vy
        return hypot(p.x() - qx, p.y() - qy)

    def _hit_drawing(self, pos: QPointF) -> tuple[int, str] | None:
        """Hit-test editable non-Fibonacci drawings in cursor mode.

        Modes identify either a resize/rotation handle or a whole-object move.
        Endpoints/corners take priority over body hits.
        """
        if self.tool != "光标":
            return None
        tol_handle = 9.0
        tol_body = 7.0
        for i in range(len(self.drawings) - 1, -1, -1):
            d = self.drawings[i]
            if d.kind == "黄金分割":
                continue
            a = QPointF(self._index_x(d.x1), self._price_y(d.y1))
            b = QPointF(self._index_x(d.x2), self._price_y(d.y2))

            if d.kind == "波浪尺":
                cx, cy = getattr(d, "c_x", None), getattr(d, "c_y", None)
                if cx is None or cy is None:
                    continue
                c = QPointF(self._index_x(float(cx)), self._price_y(float(cy)))
                if hypot(pos.x() - a.x(), pos.y() - a.y()) <= tol_handle:
                    return i, "endpoint1"
                if hypot(pos.x() - b.x(), pos.y() - b.y()) <= tol_handle:
                    return i, "endpoint2"
                if hypot(pos.x() - c.x(), pos.y() - c.y()) <= tol_handle:
                    return i, "endpoint3"
                if (
                    self._point_segment_distance(pos, a, b) <= tol_body
                    or self._point_segment_distance(pos, b, c) <= tol_body
                ):
                    return i, "move"
                if c.x() - tol_body <= pos.x() <= self._plot_rect.right() + tol_body:
                    for _ratio, target in calculate_wave_ruler_levels(d.y1, d.y2, float(cy)):
                        if abs(pos.y() - self._price_y(target)) <= tol_body:
                            return i, "move"
                continue

            if d.kind in {"趋势线", "射线"}:
                if hypot(pos.x() - a.x(), pos.y() - a.y()) <= tol_handle:
                    return i, "endpoint1"
                if hypot(pos.x() - b.x(), pos.y() - b.y()) <= tol_handle:
                    return i, "endpoint2"
                end = self._ray_endpoint(a, b) if d.kind == "射线" else b
                if self._point_segment_distance(pos, a, end) <= tol_body:
                    return i, "move"

            elif d.kind == "水平线":
                if abs(pos.y() - a.y()) <= tol_body and self._plot_rect.left() <= pos.x() <= self._plot_rect.right():
                    return i, "move_hline"

            elif d.kind == "垂直线":
                if abs(pos.x() - a.x()) <= tol_body and self._plot_rect.top() <= pos.y() <= self._plot_rect.bottom():
                    return i, "move_vline"

            elif d.kind == "矩形":
                c11 = a
                c22 = b
                c12 = QPointF(a.x(), b.y())
                c21 = QPointF(b.x(), a.y())
                for pt, mode in ((c11, "corner11"), (c22, "corner22"), (c12, "corner12"), (c21, "corner21")):
                    if hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= tol_handle:
                        return i, mode
                rect = QRectF(a, b).normalized()
                near_border = (
                    rect.adjusted(-tol_body, -tol_body, tol_body, tol_body).contains(pos)
                    and not rect.adjusted(tol_body, tol_body, -tol_body, -tol_body).contains(pos)
                )
                if near_border:
                    return i, "move"

            else:  # defensive fallback for future two-anchor drawings
                if hypot(pos.x() - a.x(), pos.y() - a.y()) <= tol_handle:
                    return i, "endpoint1"
                if hypot(pos.x() - b.x(), pos.y() - b.y()) <= tol_handle:
                    return i, "endpoint2"
                if self._point_segment_distance(pos, a, b) <= tol_body:
                    return i, "move"
        return None

    def _move_drawing_drag(self, pos: QPointF) -> None:
        if (self._drag_drawing_index is None or self._drag_drawing_start is None
                or self._drag_drawing_original is None):
            return
        if not (0 <= self._drag_drawing_index < len(self.drawings)):
            return
        d = self.drawings[self._drag_drawing_index]
        mode = self._drag_drawing_mode or "move"
        ix = self._x_index(pos.x())
        pr = self._y_price(pos.y())
        sx, sy = self._drag_drawing_start
        x1, y1, x2, y2 = self._drag_drawing_original
        dx, dy = ix - sx, pr - sy

        if mode == "endpoint1":
            d.x1, d.y1 = ix, pr
        elif mode == "endpoint2":
            d.x2, d.y2 = ix, pr
        elif mode == "endpoint3" and getattr(d, "kind", "") == "波浪尺":
            d.c_x, d.c_y = ix, pr
        elif mode == "corner11":
            d.x1, d.y1 = ix, pr
        elif mode == "corner22":
            d.x2, d.y2 = ix, pr
        elif mode == "corner12":
            d.x1, d.y2 = ix, pr
        elif mode == "corner21":
            d.x2, d.y1 = ix, pr
        elif mode == "move_hline":
            d.y1, d.y2 = y1 + dy, y2 + dy
        elif mode == "move_vline":
            d.x1, d.x2 = x1 + dx, x2 + dx
        else:
            d.x1, d.x2 = x1 + dx, x2 + dx
            d.y1, d.y2 = y1 + dy, y2 + dy
            if (
                d.kind == "波浪尺"
                and self._drag_drawing_original_c is not None
            ):
                original_cx, original_cy = self._drag_drawing_original_c
                d.c_x = float(original_cx) + dx
                d.c_y = float(original_cy) + dy
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent):
        self._mouse = e.position()
        if self._hit_volume_toggle(self._mouse) is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        if self._drag_drawing_index is not None:
            self._move_drawing_drag(e.position())
            return
        if self._drag_fib_index is not None:
            if (
                0 <= self._drag_fib_index < len(self.drawings)
                and self._drag_fib_start_price is not None
                and self._drag_fib_original is not None
            ):
                d = self.drawings[self._drag_fib_index]
                if d.kind == "黄金分割":
                    current_price = self._y_price(e.position().y())
                    y1_0, y2_0 = self._drag_fib_original
                    mode = self._drag_fib_mode or "move"

                    if mode == "move":
                        # Dragging an inner Fibonacci level translates the complete
                        # grid without changing its vertical span.
                        delta = current_price - self._drag_fib_start_price
                        d.y1 = y1_0 + delta
                        d.y2 = y2_0 + delta
                    elif mode == "resize_y1":
                        # y2 stays fixed; y1 becomes the dragged boundary.  Keep
                        # at least a tiny price span so the grid never collapses.
                        fixed = y2_0
                        min_span = max(abs(self._price_max - self._price_min) * 0.0005, 1e-9)
                        if y1_0 >= fixed:
                            d.y1 = max(current_price, fixed + min_span)
                        else:
                            d.y1 = min(current_price, fixed - min_span)
                        d.y2 = fixed
                    elif mode == "resize_y2":
                        # y1 stays fixed; y2 becomes the dragged boundary.
                        fixed = y1_0
                        min_span = max(abs(self._price_max - self._price_min) * 0.0005, 1e-9)
                        if y2_0 >= fixed:
                            d.y2 = max(current_price, fixed + min_span)
                        else:
                            d.y2 = min(current_price, fixed - min_span)
                        d.y1 = fixed
                    self.update()
            return
        if self._drag_separator_index is not None:
            self._resize_separator(self._drag_separator_index, e.position().y())
            return
        if self._drag_trade_line:
            # Keep updating continuously while the mouse button is held.  Clamp
            # slightly-outside drags to the visible price area instead of freezing
            # the last preview value at the plot boundary.
            drag_y = min(max(self._mouse.y(), self._plot_rect.top()), self._plot_rect.bottom())
            self._trade_drag_preview = self._y_price(drag_y)
            self.update()
            return
        if self._drag_pan_x is not None:
            x = e.position().x()
            y = e.position().y()
            start_x = self._drag_pan_start_x if self._drag_pan_start_x is not None else self._drag_pan_x
            start_y = self._drag_pan_start_y if self._drag_pan_start_y is not None else (self._drag_pan_y if self._drag_pan_y is not None else y)

            # Lock to the dominant drag direction after a few pixels. This avoids
            # tiny hand jitter accidentally scrolling history during a vertical drag
            # (or changing price position during a horizontal drag). Near-diagonal
            # gestures deliberately keep both axes active.
            if self._drag_pan_mode is None:
                total_dx, total_dy = x - start_x, y - start_y
                if hypot(total_dx, total_dy) < 3.0:
                    self.update()
                    return
                if abs(total_dx) >= 1.5 * abs(total_dy):
                    self._drag_pan_mode = "horizontal"
                elif abs(total_dy) >= 1.5 * abs(total_dx):
                    self._drag_pan_mode = "vertical"
                else:
                    self._drag_pan_mode = "both"
                dx, dy = total_dx, total_dy
            else:
                dx = x - self._drag_pan_x
                dy = y - (self._drag_pan_y if self._drag_pan_y is not None else y)

            self._drag_pan_x = x
            self._drag_pan_y = y
            if self._drag_pan_mode == "horizontal":
                dy = 0.0
            elif self._drag_pan_mode == "vertical":
                dx = 0.0

            changed = False
            # Horizontal drag scrolls through historical candles. Only horizontal
            # movement disables follow-latest; vertical movement does not.
            if abs(dx) > 0.01:
                if self.follow_latest:
                    self.follow_latest = False
                    self.followLatestChanged.emit(False)

                step = max(2.0, self._slot_step())
                self._pan_shift_slots += dx / step
                max_offset = max(0, self.current_idx)
                while self._pan_shift_slots >= 1.0 and self.end_offset < max_offset:
                    self.end_offset += 1
                    self._pan_shift_slots -= 1.0
                while self._pan_shift_slots <= -1.0 and self.end_offset > 0:
                    self.end_offset -= 1
                    self._pan_shift_slots += 1.0

                if self.end_offset == 0:
                    self._pan_shift_slots = max(-0.45 * self.visible_bars, self._pan_shift_slots)
                if self.end_offset >= max_offset:
                    self._pan_shift_slots = min(0.45 * self.visible_bars, self._pan_shift_slots)
                changed = True

            # Vertical drag translates the visible price range. A drag down moves
            # the candle field down; a drag up moves it up. Use plot height so the
            # response is proportional and smooth across window sizes.
            if abs(dy) > 0.01 and self._plot_rect.height() > 0:
                self._price_pan_fraction += dy / self._plot_rect.height()
                self._price_pan_fraction = max(-5.0, min(5.0, self._price_pan_fraction))
                changed = True

            if changed:
                self.layoutChanged.emit()
                self.update()
            return
        self.update()

    def leaveEvent(self, e):
        # When the cursor leaves the chart, return legends to the current replay bar
        # and remove the stale crosshair from the previous mouse position.
        self._mouse = QPointF(-1, -1)
        self.update()
        super().leaveEvent(e)

    def _hit_trade_line(self, pos: QPointF) -> str | None:
        if self.tool != "光标":
            return None
        for name, value in self._trade_lines.items():
            if value is not None and abs(pos.y() - self._price_y(value)) <= 6:
                return name
        return None

    def _hit_fibonacci(self, pos: QPointF) -> tuple[int, str] | None:
        """Return ``(drawing_index, edit_mode)`` for a Fibonacci hit.

        The two *visible boundary* lines are resize handles: dragging the top or
        bottom line expands/contracts the Fibonacci grid while the opposite
        boundary stays fixed.  Dragging one of the five inner levels moves the
        entire grid vertically.
        """
        if self.tool != "光标":
            return None
        tolerance = 7.0
        for i in range(len(self.drawings) - 1, -1, -1):
            d = self.drawings[i]
            if d.kind != "黄金分割":
                continue
            x1 = self._index_x(d.x1)
            x2 = self._index_x(d.x2)
            left, right = sorted((x1, x2))
            if pos.x() < left - tolerance or pos.x() > right + tolerance:
                continue

            y1_screen = self._price_y(d.y1)
            y2_screen = self._price_y(d.y2)
            # r=0 is the y1 anchor and r=1 is the y2 anchor.  These are exactly
            # the two visible outer boundary lines even when the user originally
            # drew the Fibonacci object from bottom to top.
            levels = (0.0, .236, .382, .5, .618, .786, 1.0)
            for r in levels:
                y = y1_screen + (y2_screen - y1_screen) * r
                if abs(pos.y() - y) <= tolerance:
                    if r == 0.0:
                        return i, "resize_y1"
                    if r == 1.0:
                        return i, "resize_y2"
                    return i, "move"
        return None

    def mousePressEvent(self, e: QMouseEvent):
        # Volume-pane switches have top priority. Hit-test them before the generic
        # activation/pan/drawing paths, because those can trigger redraws while the
        # replay timer is running.
        if e.button() == Qt.MouseButton.LeftButton:
            volume_toggle = self._hit_volume_toggle(e.position())
            if volume_toggle is not None:
                self.activated.emit()
                self._drag_pan_x = None
                self._drag_pan_y = None
                self._drag_pan_start_x = None
                self._drag_pan_start_y = None
                self._drag_pan_mode = None
                self._toggle_volume_overlay(volume_toggle)
                e.accept()
                return

        self.activated.emit()
        if e.button() == Qt.MouseButton.RightButton:
            # Right-click directly on an existing take-profit / stop-loss line to
            # remove it.  The cost line represents the position average and must
            # not be deletable.  Trade-line deletion has priority over deleting
            # user drawings so a nearby drawing cannot steal the click.
            trade_hit = self._hit_trade_line(e.position())
            if trade_hit in {"止盈", "止损"}:
                self._drag_trade_line = None
                self._trade_drag_preview = None
                self.tradeLineDeleteRequested.emit(trade_hit)
                e.accept()
                return
            if self.drawings and self._plot_rect.contains(e.position()):
                hit_index: int | None = None
                if self.tool == "光标":
                    fib_hit = self._hit_fibonacci(e.position())
                    draw_hit = self._hit_drawing(e.position())
                    if fib_hit is not None:
                        hit_index = fib_hit[0]
                    elif draw_hit is not None:
                        hit_index = draw_hit[0]
                else:
                    ix, pr = self._x_index(e.position().x()), self._y_price(e.position().y())
                    hit_index = min(
                        range(len(self.drawings)),
                        key=lambda k: abs(self.drawings[k].x1 - ix) + abs(self.drawings[k].y1 - pr) / max(1, self._price_max - self._price_min),
                    )
                if hit_index is not None:
                    self.drawings.pop(hit_index)
                    self.drawingChanged.emit()
                    self.update()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return

        sep = self._hit_separator(e.position())
        if sep is not None:
            self._drag_separator_index = sep
            return
        if not self._plot_rect.contains(e.position()):
            return
        hit = self._hit_trade_line(e.position())
        if hit:
            self._drag_trade_line = hit
            self._trade_drag_preview = self._y_price(e.position().y())
            self.update()
            return
        fib_hit = self._hit_fibonacci(e.position())
        if fib_hit is not None:
            fib, mode = fib_hit
            d = self.drawings[fib]
            self._drag_fib_index = fib
            self._drag_fib_mode = mode
            self._drag_fib_start_price = self._y_price(e.position().y())
            self._drag_fib_original = (float(d.y1), float(d.y2))
            return
        drawing_hit = self._hit_drawing(e.position())
        if drawing_hit is not None:
            idx, mode = drawing_hit
            d = self.drawings[idx]
            self._drag_drawing_index = idx
            self._drag_drawing_mode = mode
            self._drag_drawing_start = (self._x_index(e.position().x()), self._y_price(e.position().y()))
            self._drag_drawing_original = (float(d.x1), float(d.y1), float(d.x2), float(d.y2))
            self._drag_drawing_original_c = (
                (float(d.c_x), float(d.c_y))
                if d.kind == "波浪尺" and d.c_x is not None and d.c_y is not None
                else None
            )
            return
        if self.tool in {"限价买", "限价卖"}:
            self.clickedPrice.emit(self._y_price(e.position().y()))
            return
        if self.tool == "光标":
            self._drag_pan_x = e.position().x()
            self._drag_pan_y = e.position().y()
            self._drag_pan_start_x = e.position().x()
            self._drag_pan_start_y = e.position().y()
            self._drag_pan_mode = None
            return
        ix = self._x_index(e.position().x())
        pr = self._y_price(e.position().y())
        if self.tool == "波浪尺":
            self._wave_ruler_draft.append((ix, pr))
            if len(self._wave_ruler_draft) == 3:
                (ax0, ay0), (bx0, by0), (cx0, cy0) = self._wave_ruler_draft
                if calculate_wave_ruler_levels(ay0, by0, cy0):
                    self.drawings.append(
                        Drawing("波浪尺", ax0, ay0, bx0, by0, cx0, cy0)
                    )
                    self.drawingChanged.emit()
                self._wave_ruler_draft = []
            self.update()
            return
        if self._draft is None:
            self._draft = (ix, pr)
        else:
            self.drawings.append(Drawing(self.tool, self._draft[0], self._draft[1], ix, pr))
            self._draft = None
            self.drawingChanged.emit()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_drawing_index is not None:
            self._drag_drawing_index = None
            self._drag_drawing_mode = None
            self._drag_drawing_start = None
            self._drag_drawing_original = None
            self._drag_drawing_original_c = None
            self.drawingChanged.emit()
            self.update()
        if self._drag_fib_index is not None:
            self._drag_fib_index = None
            self._drag_fib_mode = None
            self._drag_fib_start_price = None
            self._drag_fib_original = None
            self.drawingChanged.emit()
            self.update()
        if self._drag_separator_index is not None:
            self._drag_separator_index = None
            self.layoutChanged.emit()
        if self._drag_trade_line:
            if self._trade_drag_preview is not None:
                self.tradeLineChanged.emit(self._drag_trade_line, float(self._trade_drag_preview))
            self._drag_trade_line = None
            self._trade_drag_preview = None
            self.update()
        self._drag_pan_x = None
        self._drag_pan_y = None
        self._drag_pan_start_x = None
        self._drag_pan_start_y = None
        self._drag_pan_mode = None

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self.crosshair_enabled = not self.crosshair_enabled
            self.layoutChanged.emit()
            self.update()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e: QWheelEvent):
        delta = e.angleDelta().y()
        if delta > 0:
            self.visible_bars = max(MIN_VISIBLE_BARS, int(self.visible_bars * 0.85))
        else:
            self.visible_bars = min(MAX_VISIBLE_BARS, int(self.visible_bars * 1.18))
        limit = 0.45 * self.visible_bars
        self._pan_shift_slots = max(-limit, min(limit, self._pan_shift_slots))
        self.layoutChanged.emit()
        self.update()
