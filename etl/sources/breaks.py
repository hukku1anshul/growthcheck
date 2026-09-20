"""Structural breaks derived from the indicator series.

WHY THIS EXISTS
---------------
The plan was to use the IMF Structural Reform Database to extend dated policy
events from the 24 hand-curated countries to its 90. Two things stopped that:

  1. data.imf.org sits behind Akamai, which returns "Access Denied" to any
     scripted client, and the SDMX data endpoints return 503 even from inside a
     browser session. There is no reproducible automated path to it today.
  2. More fundamentally, the SRD is not a list of decisions. It is a set of
     *regulatory-stance indexes*, where - in the IMF's own words - "an increase
     in the indexes indicates a structural reform and a decrease indicates a
     reform reversal". Turning it into events means detecting jumps in a series,
     which is exactly what this module does, just with series we can actually get.

So this derives breaks from data already in the build: V-Dem and Regimes of the
World (174 countries, 1789-2025) and the World Bank series. That is wider coverage
than the SRD offers (90 countries, 1973-2014), at the cost of being about political
and economic *outcomes* rather than *legislation*.

WHAT THESE ARE NOT
------------------
A derived break is not a decision. It says "something discontinuous happened here",
not "a government did X". The event kinds are named `derived_*` and the UI must
render them in a visually distinct, secondary style, so a reader is never invited
to mistake an algorithm's changepoint for a documented act of government.

Thresholds are deliberately conservative: it is better to miss a real break than to
litter 174 countries' charts with noise that teaches people to distrust the tool.
"""

from __future__ import annotations

import pandas as pd

REGIME_LABEL = {
    0: "closed autocracy",
    1: "electoral autocracy",
    2: "electoral democracy",
    3: "liberal democracy",
}

# Tuning. Each is a judgement call, stated here rather than buried in the code.
VDEM_WINDOW = 5        # years over which a sustained shift is measured
VDEM_DELTA = 0.20      # index points (0-1 scale) - roughly an autocracy->democracy move
GDP_CRASH = -0.10      # -10% GDP per capita in one year
INFLATION_ONSET = 50.0  # % per year, crossed from below


def _series(obs: pd.DataFrame, indicator: str) -> pd.DataFrame:
    s = obs[obs.indicator == indicator][["iso3", "year", "value"]]
    return s.sort_values(["iso3", "year"])


def regime_transitions(obs: pd.DataFrame) -> list[dict]:
    """Steps in the Regimes of the World classification."""
    out = []
    s = _series(obs, "political_regime")
    for iso3, g in s.groupby("iso3"):
        g = g.dropna(subset=["value"])
        prev_year = prev_val = None
        for year, val in zip(g.year, g.value):
            val = int(round(val))
            if prev_val is not None and val != prev_val and year == prev_year + 1:
                up = val > prev_val
                out.append({
                    "iso3": iso3,
                    "date": f"{int(year)}-01-01",
                    "year": int(year),
                    "kind": "derived_regime_transition",
                    "title": (
                        f"{'Democratisation' if up else 'Autocratisation'}: "
                        f"{REGIME_LABEL.get(prev_val, prev_val)} to "
                        f"{REGIME_LABEL.get(val, val)}"
                    ),
                    "detail": (
                        "Derived from a change in the Regimes of the World "
                        "classification, which is itself built from V-Dem expert "
                        "ratings. It marks the year a country crossed a threshold, "
                        "not a specific law or decision."
                    ),
                    "contested": (
                        "Threshold classifications move in a single step, so the "
                        "date reflects when a gradual change crossed a line rather "
                        "than when it began. Read the continuous V-Dem indices "
                        "alongside this to see the underlying trend."
                    ),
                    "source": "derived: Regimes of the World / V-Dem",
                    "source_url": None,
                    "refs": None,
                    "historical": False,
                })
            prev_year, prev_val = year, val
    return out


def democratic_shifts(obs: pd.DataFrame) -> list[dict]:
    """Sustained moves in V-Dem's electoral democracy index.

    Reported once per episode, at the steepest year, so a decade-long slide does
    not produce ten markers.
    """
    out = []
    s = _series(obs, "vdem_electoral")
    for iso3, g in s.groupby("iso3"):
        g = g.dropna(subset=["value"]).reset_index(drop=True)
        if len(g) <= VDEM_WINDOW:
            continue
        vals = g.value.to_numpy()
        years = g.year.to_numpy()
        deltas = vals[VDEM_WINDOW:] - vals[:-VDEM_WINDOW]

        i = 0
        while i < len(deltas):
            if abs(deltas[i]) < VDEM_DELTA:
                i += 1
                continue
            sign = 1 if deltas[i] > 0 else -1
            j = i
            while j + 1 < len(deltas) and (
                deltas[j + 1] * sign > 0 and abs(deltas[j + 1]) >= VDEM_DELTA
            ):
                j += 1
            peak = i + int(
                max(range(j - i + 1), key=lambda k: abs(deltas[i + k]))
            )
            end_idx = peak + VDEM_WINDOW
            up = sign > 0
            out.append({
                "iso3": iso3,
                "date": f"{int(years[end_idx])}-01-01",
                "year": int(years[end_idx]),
                "kind": "derived_democratic_shift",
                "title": (
                    f"{'Democratic opening' if up else 'Democratic erosion'}: "
                    f"electoral democracy index {'rose' if up else 'fell'} "
                    f"{abs(deltas[peak]):.2f} over {VDEM_WINDOW} years"
                ),
                "detail": (
                    f"From {vals[peak]:.2f} in {int(years[peak])} to "
                    f"{vals[end_idx]:.2f} in {int(years[end_idx])}. Derived by "
                    f"detecting a sustained shift of at least {VDEM_DELTA} index "
                    f"points over {VDEM_WINDOW} years; marked at the steepest year "
                    f"of the episode."
                ),
                "contested": (
                    "V-Dem indices are aggregated expert judgements with published "
                    "uncertainty intervals. A shift of this size is well outside the "
                    "noise, but the exact year is a property of the detection rule, "
                    "not a documented event."
                ),
                "source": "derived: V-Dem electoral democracy index",
                "source_url": None,
                "refs": None,
                "historical": False,
            })
            i = j + 1
    return out


def economic_ruptures(obs: pd.DataFrame) -> list[dict]:
    """Single-year collapses in GDP per capita."""
    out = []
    s = _series(obs, "gdp_pc")
    for iso3, g in s.groupby("iso3"):
        g = g.dropna(subset=["value"]).reset_index(drop=True)
        for k in range(1, len(g)):
            if g.year[k] != g.year[k - 1] + 1 or g.value[k - 1] <= 0:
                continue
            change = g.value[k] / g.value[k - 1] - 1
            if change <= GDP_CRASH:
                out.append({
                    "iso3": iso3,
                    "date": f"{int(g.year[k])}-01-01",
                    "year": int(g.year[k]),
                    "kind": "derived_economic_rupture",
                    "title": f"Output collapse: GDP per capita fell {abs(change) * 100:.0f}%",
                    "detail": (
                        f"Real GDP per capita fell from {g.value[k - 1]:,.0f} to "
                        f"{g.value[k]:,.0f} (constant 2015 US$) in a single year."
                    ),
                    "contested": (
                        "A one-year collapse of this size usually has several causes "
                        "at once - war, currency crisis, commodity price, or the "
                        "end of a political order. This marker says it happened; it "
                        "does not say why."
                    ),
                    "source": "derived: World Bank GDP per capita",
                    "source_url": None,
                    "refs": None,
                    "historical": False,
                })
    return out


def inflation_crises(obs: pd.DataFrame) -> list[dict]:
    """Onset years where inflation crosses 50% from below."""
    out = []
    s = _series(obs, "inflation")
    for iso3, g in s.groupby("iso3"):
        g = g.dropna(subset=["value"]).reset_index(drop=True)
        for k in range(1, len(g)):
            if g.year[k] != g.year[k - 1] + 1:
                continue
            if g.value[k - 1] < INFLATION_ONSET <= g.value[k]:
                out.append({
                    "iso3": iso3,
                    "date": f"{int(g.year[k])}-01-01",
                    "year": int(g.year[k]),
                    "kind": "derived_inflation_crisis",
                    "title": f"Inflation crossed 50% (reached {g.value[k]:,.0f}%)",
                    "detail": (
                        f"Consumer price inflation rose from {g.value[k - 1]:,.1f}% to "
                        f"{g.value[k]:,.1f}%, crossing the 50% threshold."
                    ),
                    "contested": (
                        "The 50% line is a convention, not a natural boundary. "
                        "Official inflation statistics are also among the most "
                        "manipulated numbers governments publish, so the true figure "
                        "may be higher than the one recorded here."
                    ),
                    "source": "derived: World Bank consumer price inflation",
                    "source_url": None,
                    "refs": None,
                    "historical": False,
                })
    return out


def all_breaks(obs: pd.DataFrame) -> pd.DataFrame:
    rows = (
        regime_transitions(obs)
        + democratic_shifts(obs)
        + economic_ruptures(obs)
        + inflation_crises(obs)
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["iso3", "date"]).reset_index(drop=True)


KINDS = {
    "derived_regime_transition": {"label": "Regime transition (derived)", "colour": "rupture"},
    "derived_democratic_shift": {"label": "Democratic shift (derived)", "colour": "politics"},
    "derived_economic_rupture": {"label": "Output collapse (derived)", "colour": "crisis"},
    "derived_inflation_crisis": {"label": "Inflation crisis (derived)", "colour": "crisis"},
}
