from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class TrendSettings:
    lookback: int = 1600
    confirmation_bars: int = 1
    minimum_interval: int = 2
    standard_window: int = 40
    standard_sample: int = 5
    show_intervals: bool = True
    show_navigation: bool = False
    show_waves: bool = True
    show_subwaves: bool = True
    show_trendline: bool = True
    show_pivot_line: bool = True
    show_boundaries: bool = True
    show_ratios: bool = True
    show_candles: bool = True
    show_corrections: bool = True
    show_levels: bool = True
    show_targets: bool = False
    show_reverse_fibonacci: bool = True
    show_main_wave_targets: bool = True
    show_historical_arrivals: bool = True

    @classmethod
    def from_dict(cls, values=None):
        values = values if isinstance(values, dict) else {}
        defaults = cls()
        result = {}
        ranges = {"lookback": (200, 10000), "confirmation_bars": (1, 10),
                  "minimum_interval": (2, 30), "standard_window": (10, 200),
                  "standard_sample": (1, 20)}
        for f in fields(cls):
            value = values.get(f.name, getattr(defaults, f.name))
            if f.name in ranges:
                try:
                    lo, hi = ranges[f.name]
                    value = max(lo, min(hi, int(value)))
                except (ValueError, TypeError, OverflowError):
                    value = getattr(defaults, f.name)
            else:
                value = value if isinstance(value, bool) else getattr(defaults, f.name)
            result[f.name] = value
        result["standard_sample"] = min(result["standard_sample"], result["standard_window"])
        return cls(**result)

    def to_dict(self):
        return asdict(self)
