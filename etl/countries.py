"""Canonical country registry and name resolution.

Everything in this project is keyed on ISO3. Sources disagree about names and use
three different code systems (ISO3, COW, Maddison), so all resolution happens here
and nowhere else.

Historical states (Czechoslovakia, East Germany, South Yemen, the two Vietnams) have
no modern ISO3. We map them onto their successor state and set `historical=True` so
the UI can say "this event predates the modern state" rather than silently pretending
the East German economy was Germany's.
"""

from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
import requests

WB_COUNTRIES = "https://api.worldbank.org/v2/country?format=json&per_page=400"

# REIGN / historical name -> (iso3, historical)
# `historical=True` means the source entity is not the same polity as the ISO3 state.
ALIASES: dict[str, tuple[str, bool]] = {
    "bosnia-herzegovina": ("BIH", False),
    "cape verde": ("CPV", False),
    "congo": ("COG", False),                      # Republic of the Congo (Brazzaville)
    "democratic republic of congo": ("COD", False),
    "czech republic": ("CZE", False),
    "slovakia": ("SVK", False),
    "east timor": ("TLS", False),
    "egypt": ("EGY", False),
    "iran": ("IRN", False),
    "laos": ("LAO", False),
    "micronesia": ("FSM", False),
    "myanmar (burma)": ("MMR", False),
    "nauru": ("NRU", False),
    "north korea": ("PRK", False),
    "south korea": ("KOR", False),
    "russia": ("RUS", False),
    "serbia (yugoslavia)": ("SRB", False),
    "somalia": ("SOM", False),
    "st. vincent": ("VCT", False),
    "surinam": ("SUR", False),
    "swaziland": ("SWZ", False),                  # renamed Eswatini in 2018
    "syria": ("SYR", False),
    "tajkistan": ("TJK", False),                  # sic - typo is in the source data
    "turkey": ("TUR", False),
    "united states of america": ("USA", False),
    "venezuela": ("VEN", False),
    "yemen": ("YEM", False),
    # --- historical states, mapped to successor ---
    "czechoslovakia": ("CZE", True),
    "east germany": ("DEU", True),
    "german federal republic": ("DEU", False),    # West Germany == modern Germany's state
    "republic of vietnam": ("VNM", True),         # South Vietnam
    "democratic republic of vietnam": ("VNM", True),  # North Vietnam
    "south yemen": ("YEM", True),
}

_STOPWORDS = re.compile(
    r"\b(the|of|republic|democratic|people s|peoples|federal|islamic|kingdom"
    r"|state|states|union|socialist)\b"
)


def norm(s: str) -> str:
    """Aggressive name normalisation for fuzzy matching."""
    s = str(s).lower().strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z ]", " ", s)
    s = _STOPWORDS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def registry() -> pd.DataFrame:
    """Canonical country list from the World Bank, aggregates excluded.

    Columns: iso3, iso2, name, region, income, capital, lat, lon
    """
    payload = requests.get(WB_COUNTRIES, timeout=90).json()
    rows = [
        {
            "iso3": c["id"],
            "iso2": c["iso2Code"],
            "name": c["name"],
            "region": c["region"]["value"],
            "income": c["incomeLevel"]["value"],
            "capital": c.get("capitalCity") or None,
            "lat": _f(c.get("latitude")),
            "lon": _f(c.get("longitude")),
        }
        for c in payload[1]
    ]
    df = pd.DataFrame(rows)
    df = df[df.region != "Aggregates"].reset_index(drop=True)
    return df


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _lookup() -> dict[str, str]:
    reg = registry()
    return dict(zip(reg.name.map(norm), reg.iso3))


def resolve(name: str) -> tuple[str | None, bool]:
    """Resolve a source's country name to (iso3, historical).

    Returns (None, False) when the name cannot be resolved - callers should count
    and report these rather than dropping them silently.
    """
    raw = str(name).strip().lower()
    if raw in ALIASES:
        return ALIASES[raw]
    iso3 = _lookup().get(norm(name))
    return (iso3, False) if iso3 else (None, False)


def resolve_frame(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Add iso3 + historical columns to `df` based on the country-name column `col`."""
    out = df.copy()
    pairs = out[col].map(resolve)
    out["iso3"] = [p[0] for p in pairs]
    out["historical"] = [p[1] for p in pairs]
    return out
