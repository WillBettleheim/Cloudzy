"""Presentation helpers for the Cloudzy web app: colours, icons, charts, tables.

Pure functions with no Streamlit calls, so they can be tested directly.

Colour rule used everywhere: provider data is blue and Cloudzy predictions are
orange (aqua is a third series where needed). One set of series colours serves
both themes: it passes a palette validator (colour-blind separation, lightness,
contrast) against the light *and* the dark background. Streamlit doesn't rerun
the page when a viewer switches theme, so colours chosen per theme would go
stale. Text, grid and tooltip colours are left to Streamlit's Plotly theme
(`theme="streamlit"`), which restyles them in the browser immediately.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Validated on both #f7fafd (light) and #0b1630 (dark): all checks pass.
SERIES = {"provider": "#3987e5", "cloudzy": "#d95926", "third": "#199e70",
          # Neutral grey, >= 3:1 on both backgrounds: observed values and baselines.
          "muted": "#7c879a", "observed": "#7c879a"}

_EMOJI = [  # (WMO codes, day icon, night icon)
    ({0}, "☀️", "🌙"), ({1}, "🌤️", "🌙"), ({2}, "⛅", "☁️"), ({3}, "☁️", "☁️"),
    ({45, 48}, "🌫️", "🌫️"),
    ({51, 53, 55, 56, 57}, "🌦️", "🌧️"),
    ({61, 63, 65, 66, 67, 80, 81, 82}, "🌧️", "🌧️"),
    ({71, 73, 75, 77, 85, 86}, "🌨️", "🌨️"),
    ({95, 96, 99}, "⛈️", "⛈️"),
]
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


# Passed to st.plotly_chart: the hover tooltip is the interaction; the toolbar
# only crowds the legend on small screens.
CHART_CONFIG = {"displayModeBar": False, "responsive": True}


def palette(theme_type: str | None = None) -> dict[str, str]:
    """Series colours. The same in both themes (see the module docstring)."""
    return dict(SERIES)


def weather_icon(code, is_day=1) -> str:
    if code is None or pd.isna(code):
        return "❔"
    for codes, day, night in _EMOJI:
        if int(code) in codes:
            return day if (is_day is None or pd.isna(is_day) or int(is_day) == 1) else night
    return "❔"


def compass(degrees) -> str:
    if degrees is None or pd.isna(degrees):
        return "—"
    return COMPASS[int((float(degrees) % 360) / 22.5 + 0.5) % 16]


def fmt(value, unit: str = "", digits: int = 0) -> str:
    """Format a number, showing an em dash for missing values instead of a fake zero."""
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.{digits}f}{unit}"


def _layout(fig: go.Figure, colors: dict, title: str, y_title: str, height: int = 280) -> go.Figure:
    # No text, grid or tooltip colours here: Streamlit's Plotly theme supplies
    # them and follows the viewer's theme without a rerun.
    fig.update_layout(
        # Title on the first line, legend on its own line beneath it, so they never
        # collide, even in a half-width column.
        title={"text": title, "x": 0, "xanchor": "left", "y": 1, "yref": "container", "yanchor": "top",
               "pad": {"t": 6}, "font": {"size": 15}},
        height=height + 24, margin={"l": 56, "r": 16, "t": 80, "b": 44},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 0, "xanchor": "left", "yanchor": "bottom"},
    )
    fig.update_xaxes(showgrid=False, automargin=True)
    fig.update_yaxes(title_text=y_title, zeroline=False, automargin=True)
    return fig


def temperature_chart(hourly: pd.DataFrame, colors: dict) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=hourly.index, y=hourly["temperature_2m"], name="Temperature", mode="lines",
                    line={"color": colors["provider"], "width": 2},
                    hovertemplate="%{y:.1f} °C<extra>Temperature</extra>")
    fig.add_scatter(x=hourly.index, y=hourly["apparent_temperature"], name="Feels like", mode="lines",
                    line={"color": colors["muted"], "width": 2, "dash": "dot"},
                    hovertemplate="%{y:.1f} °C<extra>Feels like</extra>")
    return _layout(fig, colors, "Temperature", "°C")


def rain_chart(hourly: pd.DataFrame, colors: dict) -> go.Figure:
    """Rain probability bars. Amounts go in the hover text: one axis, one unit."""
    fig = go.Figure()
    fig.add_bar(x=hourly.index, y=hourly["precipitation_probability"], name="Chance of rain",
                marker={"color": colors["provider"], "cornerradius": 3},
                customdata=hourly["precipitation"],
                hovertemplate="%{y:.0f}% · %{customdata:.1f} mm<extra>Rain</extra>")
    fig = _layout(fig, colors, "Chance of rain", "%", height=220)
    fig.update_yaxes(range=[0, 100])
    fig.update_layout(bargap=0.15, showlegend=False)
    return fig


def daily_range_chart(daily: pd.DataFrame, colors: dict) -> go.Figure:
    """Floating bar from daily minimum to maximum, labelled at both ends."""
    labels = [pd.Timestamp(d).strftime("%a %d") for d in daily.index]
    lo, hi = daily["temperature_2m_min"], daily["temperature_2m_max"]
    fig = go.Figure()
    fig.add_bar(x=labels, y=hi - lo, base=lo, name="Range",
                marker={"color": colors["provider"], "cornerradius": 6},
                customdata=list(zip(lo, hi)),
                hovertemplate="%{customdata[0]:.0f}° – %{customdata[1]:.0f}°<extra></extra>")
    fig.add_scatter(x=labels, y=hi, mode="text", text=[fmt(v, "°") for v in hi],
                    textposition="top center", hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=labels, y=lo, mode="text", text=[fmt(v, "°") for v in lo],
                    textposition="bottom center", hoverinfo="skip", showlegend=False)
    fig = _layout(fig, colors, "Daily low and high", "°C", height=300)
    span = (hi.max() - lo.min()) if hi.notna().any() and lo.notna().any() else 10
    fig.update_yaxes(range=[lo.min() - 0.15 * span - 1, hi.max() + 0.15 * span + 1])
    fig.update_layout(showlegend=False, bargap=0.45, hovermode="closest")
    return fig


def cloudzy_chart(predictions: list[dict], provider: list[dict], colors: dict) -> go.Figure:
    """Cloudzy and provider values for the same valid times, as two labelled series."""
    t_c = [pd.Timestamp(p["valid_time_local"]) for p in predictions]
    t_p = [pd.Timestamp(p["valid_time_local"]) for p in provider]
    fig = go.Figure()
    fig.add_scatter(x=t_p, y=[p["temperature_2m_c"] for p in provider], name="Provider (ECMWF IFS)",
                    mode="lines+markers", line={"color": colors["provider"], "width": 2},
                    marker={"size": 8}, hovertemplate="%{y:.1f} °C<extra>Provider</extra>")
    fig.add_scatter(x=t_c, y=[p["temperature_2m_c"] for p in predictions], name="Cloudzy Core",
                    mode="lines+markers", line={"color": colors["cloudzy"], "width": 2},
                    marker={"size": 9, "symbol": "diamond"},
                    hovertemplate="%{y:.1f} °C<extra>Cloudzy</extra>")
    return _layout(fig, colors, "Temperature at each horizon", "°C", height=300)


def hourly_table(hourly: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Time": [t.strftime("%a %H:%M") for t in hourly.index],
        "": [weather_icon(c, d) for c, d in zip(hourly["weather_code"], hourly["is_day"])],
        "Temp °C": hourly["temperature_2m"].round(1).to_numpy(),
        "Feels °C": hourly["apparent_temperature"].round(1).to_numpy(),
        "Rain %": hourly["precipitation_probability"].to_numpy(),
        "Rain mm": hourly["precipitation"].round(1).to_numpy(),
        "Humidity %": hourly["relative_humidity_2m"].to_numpy(),
        "Wind km/h": hourly["wind_speed_10m"].round(0).to_numpy(),
        "From": [compass(d) for d in hourly["wind_direction_10m"]],
    })


def cloudzy_table(predictions: list[dict], provider: list[dict]) -> pd.DataFrame:
    prov = {p["horizon_h"]: p["temperature_2m_c"] for p in provider}
    return pd.DataFrame({
        "Ahead": [f"+{p['horizon_h']} h" for p in predictions],
        "Local time": [pd.Timestamp(p["valid_time_local"]).strftime("%a %H:%M") for p in predictions],
        "Cloudzy °C": [round(p["temperature_2m_c"], 1) for p in predictions],
        "Provider °C": [prov.get(p["horizon_h"]) for p in predictions],
        "Cloudzy avg error °C (2025)": [round(p["typical_error_c"], 2) for p in predictions],
    })
