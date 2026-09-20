"""Our World in Data grapher CSV endpoints.

OWID republishes V-Dem, Maddison and Regimes-of-the-World in a clean, stable,
citable CSV form. Using their mirror rather than scraping the originals means the
pipeline runs with no registration, no 200MB Stata files, and no bespoke parser per
academic dataset - at the cost of depending on OWID's slug staying stable, which is
why every fetch is cached and hashed.
"""

from __future__ import annotations

import pandas as pd

from ..fetch import cached

GRAPHER = "https://ourworldindata.org/grapher/{slug}.csv"


def series(slug: str, column: str) -> pd.DataFrame:
    """Fetch one OWID grapher series. Returns long format: iso3, year, value."""
    path = cached(GRAPHER.format(slug=slug), f"owid_{slug}.csv", max_age_days=30)
    df = pd.read_csv(path, encoding="utf-8")

    # OWID is inconsistent about capitalising the entity/code/year headers.
    lower = {c.lower(): c for c in df.columns}
    code_col = lower.get("code")
    year_col = lower.get("year")
    if code_col is None or year_col is None:
        raise RuntimeError(f"{slug}: expected Code/Year columns, got {list(df.columns)}")

    if column not in df.columns:
        raise RuntimeError(
            f"{slug}: column '{column}' not found. Available: "
            f"{[c for c in df.columns if c not in (code_col, year_col)]}"
        )

    out = df[[code_col, year_col, column]].copy()
    out.columns = ["iso3", "year", "value"]
    out = out.dropna(subset=["iso3", "value"])
    # OWID uses OWID_* pseudo-codes for aggregates and regions.
    out = out[~out.iso3.astype(str).str.startswith("OWID")]
    out["year"] = out.year.astype(int)
    return out.reset_index(drop=True)


def available_columns(slug: str) -> list[str]:
    """Helper for adding new indicators - shows what a grapher slug actually exposes."""
    path = cached(GRAPHER.format(slug=slug), f"owid_{slug}.csv", max_age_days=30)
    return list(pd.read_csv(path, nrows=1, encoding="utf-8").columns)
