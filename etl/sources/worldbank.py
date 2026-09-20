"""World Bank World Development Indicators."""

from __future__ import annotations

import json

import pandas as pd

from ..fetch import cached

API = "https://api.worldbank.org/v2/country/all/indicator/{code}"
QS = "?format=json&per_page=20000&date={start}:{end}"


def series(code: str, start: int = 1960, end: int = 2026) -> pd.DataFrame:
    """Fetch one WDI indicator for every country.

    Returns long format: iso3, year, value.
    Aggregates (world, regions, income groups) come back from this endpoint too; they
    are filtered out downstream by joining against the country registry.
    """
    url = API.format(code=code) + QS.format(start=start, end=end)
    path = cached(url, f"wb_{code}.json", max_age_days=7)
    payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        return pd.DataFrame(columns=["iso3", "year", "value"])

    header = payload[0]
    rows = payload[1]

    # The API caps per_page; if the indicator has more rows than one page we would
    # silently truncate. Fail loudly instead.
    if header.get("pages", 1) > 1:
        raise RuntimeError(
            f"{code}: {header['pages']} pages returned - pagination not implemented, "
            f"raise per_page or add a page loop"
        )

    out = pd.DataFrame(
        [
            {
                "iso3": r.get("countryiso3code") or None,
                "year": int(r["date"]),
                "value": r["value"],
            }
            for r in rows
            if r.get("value") is not None
        ]
    )
    if out.empty:
        return pd.DataFrame(columns=["iso3", "year", "value"])
    return out.dropna(subset=["iso3"]).reset_index(drop=True)
