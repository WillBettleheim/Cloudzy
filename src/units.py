"""Display units for Cloudzy.

Forecasts are always fetched and validated in metric units (°C, km/h, mm, hPa,
m). They are converted only for display, so one cached API response serves
every unit choice and the validation never has to guess units.

Missing values stay missing: every function returns None for None or NaN,
never zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TEMPERATURE = ("°C", "°F")
WIND = ("km/h", "m/s", "mph", "kn")
PRECIPITATION = ("mm", "in")

_WIND_FROM_KMH = {"km/h": 1.0, "m/s": 1 / 3.6, "mph": 1 / 1.609344, "kn": 1 / 1.852}
_MM_PER_INCH = 25.4


@dataclass(frozen=True)
class Units:
    temperature: str = "°C"
    wind: str = "km/h"
    precipitation: str = "mm"

    def __post_init__(self):
        if self.temperature not in TEMPERATURE:
            raise ValueError(f"Unknown temperature unit {self.temperature!r}")
        if self.wind not in WIND:
            raise ValueError(f"Unknown wind unit {self.wind!r}")
        if self.precipitation not in PRECIPITATION:
            raise ValueError(f"Unknown precipitation unit {self.precipitation!r}")

    @property
    def distance(self) -> str:
        """Visibility follows the precipitation system: metric → km, imperial → mi."""
        return "km" if self.precipitation == "mm" else "mi"


METRIC = Units()
IMPERIAL = Units("°F", "mph", "in")


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def temperature(celsius, unit: str):
    if _missing(celsius):
        return None
    return celsius * 9 / 5 + 32 if unit == "°F" else float(celsius)


def temperature_delta(delta_c, unit: str):
    """A temperature *difference* (such as an average error): scale only, no offset."""
    if _missing(delta_c):
        return None
    return delta_c * 9 / 5 if unit == "°F" else float(delta_c)


def wind(kmh, unit: str):
    if _missing(kmh):
        return None
    return kmh * _WIND_FROM_KMH[unit]


def precipitation(mm, unit: str):
    if _missing(mm):
        return None
    return mm / _MM_PER_INCH if unit == "in" else float(mm)


def distance(metres, unit: str):
    if _missing(metres):
        return None
    return metres / 1609.344 if unit == "mi" else metres / 1000


def precipitation_digits(unit: str) -> int:
    return 2 if unit == "in" else 1


def convert_frame(df, units: Units):
    """Copy of a provider hourly/daily table with display-unit columns converted."""
    out = df.copy()
    for col in out.columns:
        if col in ("temperature_2m", "apparent_temperature", "temperature_2m_max", "temperature_2m_min",
                   "dew_point_2m"):
            out[col] = out[col].map(lambda v: temperature(v, units.temperature)).astype(float)
        elif col in ("wind_speed_10m", "wind_gusts_10m", "wind_speed_10m_max"):
            out[col] = out[col].map(lambda v: wind(v, units.wind)).astype(float)
        elif col in ("precipitation", "precipitation_sum"):
            out[col] = out[col].map(lambda v: precipitation(v, units.precipitation)).astype(float)
    return out
