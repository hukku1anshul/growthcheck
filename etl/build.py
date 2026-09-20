"""Build the analysis database and the web bundles.

    python -m etl.build            # full build
    python -m etl.build --quick    # 6 indicators, for iterating

Outputs
    data/processed/pf.db           SQLite, for analysis and ad-hoc SQL
    web/public/data/meta.json      country list, indicator catalogue, provenance
    web/public/data/series/*.json  one file per indicator, all countries
    web/public/data/events/*.json  one file per country, events + spans
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from .countries import registry
from .fetch import source_manifest
from .sources import breaks, owid, reign, worldbank

ROOT = Path(__file__).resolve().parents[1]
CURATED = ROOT / "data" / "curated"
PROCESSED = ROOT / "data" / "processed"
WEBDATA = ROOT / "web" / "public" / "data"

QUICK_SET = {
    "gdp_pc",
    "gdp_growth",
    "inflation",
    "life_expectancy",
    "vdem_electoral",
    "vdem_corruption",
}


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


# --------------------------------------------------------------------------- load
def load_catalogue() -> dict:
    return yaml.safe_load((CURATED / "indicators.yaml").read_text(encoding="utf-8"))


def load_decisions() -> dict:
    """Merge every decisions*.yaml file.

    Split across files so neither grows past the point where a human will
    actually re-read it before adding an entry. `kinds` is defined once, in
    decisions.yaml.
    """
    merged: dict = {"kinds": {}, "decisions": []}
    for path in sorted(CURATED.glob("decisions*.yaml")):
        part = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged["kinds"].update(part.get("kinds") or {})
        merged["decisions"].extend(part.get("decisions") or [])
    return merged


# ------------------------------------------------------------------------ series
def build_series(catalogue: dict, valid_iso3: set[str], quick: bool) -> pd.DataFrame:
    frames = []
    inds = catalogue["indicators"]
    if quick:
        inds = [i for i in inds if i["id"] in QUICK_SET]

    for ind in inds:
        iid = ind["id"]
        try:
            if ind["source"] == "worldbank":
                df = worldbank.series(ind["code"])
            elif ind["source"] == "owid":
                df = owid.series(ind["code"], ind["column"])
            else:
                log(f"SKIP {iid}: unknown source {ind['source']!r}")
                continue
        except Exception as exc:  # noqa: BLE001 - one bad source must not kill the build
            log(f"FAIL {iid}: {type(exc).__name__}: {exc}")
            continue

        before = len(df)
        df = df[df.iso3.isin(valid_iso3)].copy()
        df["indicator"] = iid
        frames.append(df)
        log(
            f"ok   {iid:22} {len(df):>7,} obs  "
            f"{df.year.min() if len(df) else '-'}-{df.year.max() if len(df) else '-'}  "
            f"({before - len(df):,} aggregate rows dropped)"
        )

    if not frames:
        return pd.DataFrame(columns=["iso3", "year", "value", "indicator"])
    return pd.concat(frames, ignore_index=True)


# ------------------------------------------------------------------------ events
def build_events(series: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (events, spans)."""
    ev = reign.events()
    log(f"ok   reign events          {len(ev):>7,}  {ev.year.min()}-{ev.year.max()}")

    dec = load_decisions()
    rows = []
    for d in dec["decisions"]:
        date = str(d["date"])
        rows.append(
            {
                "iso3": d["iso3"],
                "date": date,
                "year": int(date[:4]),
                "kind": d["kind"],
                "title": d["title"],
                "detail": " ".join(str(d["what"]).split()),
                "contested": " ".join(str(d.get("contested", "")).split()) or None,
                "source": "curated",
                "source_url": None,
                "refs": json.dumps([s["ref"] for s in d.get("sources", [])]),
                "historical": False,
            }
        )
    curated = pd.DataFrame(rows)
    log(f"ok   curated decisions     {len(curated):>7,}  covering {curated.iso3.nunique()} countries")

    derived = breaks.all_breaks(series)
    log(
        f"ok   derived breaks        {len(derived):>7,}  covering "
        f"{derived.iso3.nunique()} countries"
    )

    for col in ("contested", "refs"):
        if col not in ev.columns:
            ev[col] = None
    events = pd.concat([ev, curated, derived], ignore_index=True)
    events = events.sort_values(["iso3", "date"]).reset_index(drop=True)

    # spans: leader terms and regime periods, for shaded bands
    ld = reign.leaders()
    ld_spans = pd.DataFrame(
        {
            "iso3": ld.iso3,
            "kind": "leader",
            "label": ld.leader,
            "start": ld.start.dt.date.astype(str),
            "end": ld.end.dt.date.astype(str).where(ld.end.notna(), None),
            "meta": ld.military.map(
                lambda m: json.dumps({"military": bool(m)}) if pd.notna(m) else None
            ),
        }
    )
    rg = reign.regimes()
    rg_spans = pd.DataFrame(
        {
            "iso3": rg.iso3,
            "kind": "regime",
            "label": rg.regime_type,
            "start": rg.start.dt.date.astype(str),
            "end": rg.end.dt.date.astype(str).where(rg.end.notna(), None),
            "meta": None,
        }
    )
    spans = pd.concat([ld_spans, rg_spans], ignore_index=True)
    log(f"ok   spans                 {len(spans):>7,}  ({len(ld_spans):,} leader, {len(rg_spans):,} regime)")
    return events, spans


# ------------------------------------------------------------------------ sqlite
def write_sqlite(countries, series, events, spans, catalogue) -> Path:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    db = PROCESSED / "pf.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)

    countries.to_sql("countries", con, index=False)
    series.to_sql("observations", con, index=False)
    events.to_sql("events", con, index=False)
    spans.to_sql("spans", con, index=False)
    pd.DataFrame(catalogue["indicators"]).to_sql("indicators", con, index=False)

    cur = con.cursor()
    cur.execute("CREATE INDEX ix_obs ON observations(indicator, iso3, year)")
    cur.execute("CREATE INDEX ix_obs_country ON observations(iso3, year)")
    cur.execute("CREATE INDEX ix_ev ON events(iso3, year)")
    cur.execute("CREATE INDEX ix_sp ON spans(iso3, kind)")
    con.commit()
    con.close()
    return db


# ------------------------------------------------------------------------ bundles
def write_bundles(countries, series, events, spans, catalogue) -> None:
    (WEBDATA / "series").mkdir(parents=True, exist_ok=True)
    (WEBDATA / "events").mkdir(parents=True, exist_ok=True)

    # one file per indicator: {iso3: [[year, value], ...]}
    covered = {}
    for iid, grp in series.groupby("indicator"):
        payload = {}
        for iso3, g in grp.groupby("iso3"):
            g = g.sort_values("year")
            payload[iso3] = [
                [int(y), None if pd.isna(v) else round(float(v), 6)]
                for y, v in zip(g.year, g.value)
            ]
        (WEBDATA / "series" / f"{iid}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        covered[iid] = {
            "countries": len(payload),
            "min_year": int(grp.year.min()),
            "max_year": int(grp.year.max()),
            "observations": len(grp),
        }

    # one file per country: events + spans
    ev_by = {k: v for k, v in events.groupby("iso3")}
    sp_by = {k: v for k, v in spans.groupby("iso3")}
    for iso3 in sorted(set(ev_by) | set(sp_by)):
        e = ev_by.get(iso3)
        s = sp_by.get(iso3)
        payload = {
            "events": [] if e is None else json.loads(e.to_json(orient="records")),
            "spans": [] if s is None else json.loads(s.to_json(orient="records")),
        }
        (WEBDATA / "events" / f"{iso3}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": catalogue["categories"],
        "indicators": [
            {**ind, "coverage": covered.get(ind["id"])}
            for ind in catalogue["indicators"]
            if ind["id"] in covered
        ],
        "countries": json.loads(
            countries[["iso3", "name", "region", "income"]]
            .sort_values("name")
            .to_json(orient="records")
        ),
        "event_kinds": {
            **{k: v for k, v in load_decisions()["kinds"].items()},
            "leader_change": {"label": "Leader change", "colour": "politics"},
            "election": {"label": "Election", "colour": "politics"},
            "power_transfer": {"label": "Election - power changes hands", "colour": "rupture"},
            "regime_change": {"label": "Regime change", "colour": "rupture"},
            **breaks.KINDS,
        },
        "countries_with_events": sorted(set(ev_by) | set(sp_by)),
        "caveats": {
            "reign_coverage_end": reign.COVERAGE_END,
            "note": (
                "Automatic political events (leaders, elections, regimes) come from "
                "REIGN, whose public release ends in 2021. Curated landmark decisions "
                "run to the present but are not exhaustive - absence of a marker is "
                "not evidence that nothing happened."
            ),
        },
        "provenance": source_manifest(),
    }
    (WEBDATA / "meta.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8"
    )


# -------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="build a 6-indicator subset")
    args = ap.parse_args()

    print("\n== countries ==")
    countries = registry()
    valid = set(countries.iso3)
    log(f"ok   registry              {len(countries):>7,} countries")

    print("\n== series ==")
    catalogue = load_catalogue()
    series = build_series(catalogue, valid, args.quick)
    if series.empty:
        print("\nno series built - aborting", file=sys.stderr)
        return 1

    print("\n== events ==")
    events, spans = build_events(series)
    unknown = set(events.iso3.dropna()) - valid
    if unknown:
        log(f"warn {len(unknown)} event iso3 codes not in registry: {sorted(unknown)[:8]}")
    events = events[events.iso3.isin(valid)]
    spans = spans[spans.iso3.isin(valid)]

    print("\n== write ==")
    db = write_sqlite(countries, series, events, spans, catalogue)
    log(f"ok   {db.relative_to(ROOT)}  {db.stat().st_size / 1e6:.1f} MB")
    write_bundles(countries, series, events, spans, catalogue)
    n = sum(1 for _ in (WEBDATA / "series").glob("*.json"))
    m = sum(1 for _ in (WEBDATA / "events").glob("*.json"))
    log(f"ok   web bundles           {n} series, {m} country event files")

    print(
        f"\ndone: {len(series):,} observations, {len(events):,} events, "
        f"{len(spans):,} spans, {series.indicator.nunique()} indicators\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
