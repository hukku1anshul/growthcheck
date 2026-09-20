"""REIGN - Rulers, Elections, and Irregular Governance.

Turns REIGN's three lists into the two shapes the chart layer needs:

  events  - dated points to draw as vertical markers
  spans   - start/end intervals to draw as shaded bands (who was in power, what kind
            of regime it was)

Coverage caveat, surfaced in the UI: REIGN's public release stopped in August 2021.
Leaders run 1921-2021, elections 1915-2027 (scheduled ones included). Anything after
2021 must come from a different source, and the app must say so rather than implying
the record simply ends.
"""

from __future__ import annotations

import pandas as pd

from ..countries import resolve_frame
from ..fetch import cached

BASE = (
    "https://raw.githubusercontent.com/OEFDataScience/REIGN.github.io/"
    "gh-pages/data_sets"
)
FILES = {
    "leaders": "leader_list_8_21.csv",
    "elections": "election_list_8_21.csv",
    "regimes": "regime_list.csv",
}
HOMEPAGE = "https://oefdatascience.github.io/REIGN.github.io/"

COVERAGE_END = 2021  # public releases stopped here

READ = dict(encoding="latin-1")  # REIGN csvs are not utf-8

ELECTION_TYPE = {"L": "legislative", "X": "executive", "R": "referendum"}


def _load(key: str) -> pd.DataFrame:
    fname = FILES[key]
    path = cached(f"{BASE}/{fname}", f"reign_{fname}", max_age_days=30)
    return pd.read_csv(path, **READ)


def _names() -> pd.DataFrame:
    """ccode -> country name, taken from the election list (the only file with both)."""
    el = _load("elections")
    return el[["ccode", "country"]].drop_duplicates(subset=["ccode"])


def _iso3_by_ccode() -> dict[int, tuple[str | None, bool]]:
    n = resolve_frame(_names(), "country")
    return {
        int(r.ccode): (r.iso3, bool(r.historical))
        for r in n.itertuples()
        if r.iso3 is not None
    }


def leaders() -> pd.DataFrame:
    """Leader spells. Columns: iso3, leader, start, end, military, historical."""
    ld = _load("leaders")
    lut = _iso3_by_ccode()

    rows = []
    for r in ld.itertuples():
        hit = lut.get(int(r.ccode))
        if not hit or hit[0] is None:
            continue
        iso3, hist = hit
        try:
            start = pd.Timestamp(
                year=int(r.syear), month=int(r.smonth), day=int(r.sdate)
            )
        except (ValueError, TypeError):
            continue
        end = None
        if pd.notna(r.eyear) and pd.notna(r.emonth):
            try:
                end = pd.Timestamp(year=int(r.eyear), month=int(r.emonth), day=1)
            except (ValueError, TypeError):
                end = None
        rows.append(
            {
                "iso3": iso3,
                "leader": str(r.leader).strip(),
                "start": start,
                "end": end,
                "military": bool(r.militarycareer) if pd.notna(r.militarycareer) else None,
                "historical": hist,
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["iso3", "start"]).reset_index(drop=True)


def regimes() -> pd.DataFrame:
    """Regime spells. Columns: iso3, regime_type, start, end."""
    rg = _load("regimes")
    lut = _iso3_by_ccode()

    rows = []
    for r in rg.itertuples():
        hit = lut.get(int(r.cowcode)) if pd.notna(r.cowcode) else None
        if not hit or hit[0] is None:
            continue
        start = pd.to_datetime(r.gwf_startdate, format="%m/%d/%Y", errors="coerce")
        end = pd.to_datetime(r.gwf_enddate, format="%m/%d/%Y", errors="coerce")
        if pd.isna(start):
            continue
        rows.append(
            {
                "iso3": hit[0],
                "regime_type": str(r.gwf_regimetype).strip(),
                "start": start,
                "end": None if pd.isna(end) else end,
            }
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["iso3", "start"]).reset_index(drop=True)


def events() -> pd.DataFrame:
    """Dated point events for chart markers.

    Columns: iso3, date, year, kind, title, detail, source, source_url, historical
    """
    out: list[dict] = []
    lut = _iso3_by_ccode()

    # --- leadership changes -------------------------------------------------
    for r in leaders().itertuples():
        out.append(
            {
                "iso3": r.iso3,
                "date": r.start.date().isoformat(),
                "year": int(r.start.year),
                "kind": "leader_change",
                "title": f"{r.leader} takes power",
                "detail": (
                    "Leader with a military career."
                    if r.military
                    else "Change of national leader."
                ),
                "source": "REIGN leader list",
                "source_url": HOMEPAGE,
                "historical": bool(r.historical),
            }
        )

    # --- elections ----------------------------------------------------------
    el = _load("elections")
    votes = el[el.event.astype(str).str.startswith("Vote", na=False)]
    for r in votes.itertuples():
        hit = lut.get(int(r.ccode)) if pd.notna(r.ccode) else None
        if not hit or hit[0] is None or pd.isna(r.elec_year):
            continue
        month = int(r.elec_month) if pd.notna(r.elec_month) else 1
        try:
            date = pd.Timestamp(year=int(r.elec_year), month=month, day=1)
        except (ValueError, TypeError):
            continue
        kind = "power_transfer" if str(r.change).strip() == "Y" else "election"
        etype = ELECTION_TYPE.get(str(r.type).strip(), "election")
        out.append(
            {
                "iso3": hit[0],
                "date": date.date().isoformat(),
                "year": int(r.elec_year),
                "kind": kind,
                "title": (
                    f"{etype.title()} election - power changes hands"
                    if kind == "power_transfer"
                    else f"{etype.title()} election"
                ),
                "detail": str(r.event).strip(),
                "source": "REIGN election list",
                "source_url": HOMEPAGE,
                "historical": bool(hit[1]),
            }
        )

    # --- regime changes -----------------------------------------------------
    for r in regimes().itertuples():
        out.append(
            {
                "iso3": r.iso3,
                "date": r.start.date().isoformat(),
                "year": int(r.start.year),
                "kind": "regime_change",
                "title": f"Regime becomes {r.regime_type}",
                "detail": (
                    "Regime classification from Geddes, Wright & Frantz, "
                    "as distributed with REIGN."
                ),
                "source": "REIGN regime list",
                "source_url": HOMEPAGE,
                "historical": False,
            }
        )

    df = pd.DataFrame(out)
    return df.sort_values(["iso3", "date"]).reset_index(drop=True)
