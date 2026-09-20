"""Check that the promises in docs/ETHICS.md actually hold.

    python verify.py

A commitment that is only written down is a comment. Each check below corresponds
to a numbered commitment in docs/ETHICS.md, and fails loudly if the code has
drifted away from it. Run this before shipping anything.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CURATED = ROOT / "data" / "curated"
PROCESSED = ROOT / "data" / "processed"
ARCHIVE = ROOT / "data" / "archive"
WEBDATA = ROOT / "web" / "public" / "data"

failures: list[str] = []
skipped: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f'  - {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def skip(name: str, why: str) -> None:
    print(f"  SKIP  {name}  - {why}")
    skipped.append(name)


# 1 ---------------------------------------------------------------------------
print("\n[1] We mark events. We never assert causes.")
decisions = yaml.safe_load((CURATED / "decisions.yaml").read_text(encoding="utf-8"))
missing_contested = [
    d["title"] for d in decisions["decisions"] if not str(d.get("contested", "")).strip()
]
check(
    "every curated decision states what is disputed",
    not missing_contested,
    f"missing on: {missing_contested[:3]}" if missing_contested else "",
)
missing_sources = [
    d["title"] for d in decisions["decisions"] if not d.get("sources")
]
check("every curated decision cites a source", not missing_sources,
      f"missing on: {missing_sources[:3]}" if missing_sources else "")

if (WEBDATA / "meta.json").exists():
    meta = json.loads((WEBDATA / "meta.json").read_text(encoding="utf-8"))
    derived_kinds = [k for k in meta["event_kinds"] if k.startswith("derived_")]
    check(
        "derived breaks are namespaced so the UI can distinguish them",
        len(derived_kinds) > 0,
        f"{len(derived_kinds)} derived kinds",
    )
    chart = (ROOT / "web" / "src" / "Chart.jsx").read_text(encoding="utf-8")
    check(
        "derived markers are rendered differently from documented ones",
        "isDerived(e) ? 'dashed'" in chart,
        "dashed line style applied in Chart.jsx",
    )
else:
    skip("derived-break rendering", "no meta.json; run `python -m etl.build`")

# 2 ---------------------------------------------------------------------------
print("\n[2] Every number carries a receipt.")
claims_db = PROCESSED / "claims.db"
if claims_db.exists():
    con = sqlite3.connect(f"file:{claims_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    orphans = con.execute("SELECT COUNT(*) FROM claims WHERE source_id IS NULL").fetchone()[0]
    check("no claim exists without a source document", orphans == 0, f"{orphans} orphans")

    dangling = con.execute(
        "SELECT COUNT(*) FROM claims c LEFT JOIN sources s ON s.id = c.source_id "
        "WHERE s.id IS NULL"
    ).fetchone()[0]
    check("no claim points at a missing source row", dangling == 0, f"{dangling} dangling")

    rows = con.execute("SELECT sha256, archive_path FROM sources").fetchall()
    ok = bad = missing = 0
    for r in rows:
        p = ARCHIVE / r["archive_path"]
        if not p.exists():
            missing += 1
        elif hashlib.sha256(p.read_bytes()).hexdigest() == r["sha256"]:
            ok += 1
        else:
            bad += 1
    check(
        "every archived document still matches its recorded hash",
        bad == 0 and missing == 0,
        f"{ok} verified, {bad} corrupt, {missing} missing",
    )
    con.close()

    # the constraint itself, not just the current data
    from ingest.schema import connect as claims_connect

    probe = claims_connect(claims_db)
    try:
        probe.execute(
            "INSERT INTO claims (subject,predicate,value_num,source_id,extractor)"
            " VALUES ('person','age',1,999999999,'verify')"
        )
        check("the database REFUSES a claim with no source document", False,
              "insert succeeded - foreign keys are not being enforced")
    except sqlite3.IntegrityError:
        check("the database REFUSES a claim with no source document", True)
    except sqlite3.OperationalError as exc:
        # An extractor is mid-run and holds the write lock. That is not a failure
        # of the constraint, and verify must not report it as one - but it must
        # not quietly claim a pass either.
        skip("the database REFUSES a claim with no source document", f"{exc}")
    finally:
        probe.rollback()
        probe.close()
else:
    skip("claim-store receipts", "no claims.db; run `python -m ingest.run myneta`")

# 3 ---------------------------------------------------------------------------
print("\n[3] We publish facts, not scores.")
from ingest.schema import PREDICATES  # noqa: E402

banned = {"score", "rating", "rank", "grade", "integrity_score", "performance"}
check(
    "no scoring predicate in the vocabulary",
    not (PREDICATES & banned),
    f"vocabulary: {len(PREDICATES)} predicates",
)
people_js = ROOT / "web" / "src" / "People.jsx"
if people_js.exists():
    src = people_js.read_text(encoding="utf-8")
    check(
        "the People view shows the national comparison, not a verdict",
        "national_multiple" in src and "not_a_score" in src,
    )

# 4 ---------------------------------------------------------------------------
print("\n[4] Declared assets are not evidence of corruption.")
myneta = (ROOT / "ingest" / "extractors" / "myneta.py").read_text(encoding="utf-8")
check(
    "criminal-case claims are labelled as pending, not convictions",
    "NOT convictions" in myneta,
)
if claims_db.exists():
    con = sqlite3.connect(f"file:{claims_db}?mode=ro", uri=True)
    unlabelled = con.execute(
        "SELECT COUNT(*) FROM claims WHERE predicate = 'criminal_cases_declared' "
        "AND (note IS NULL OR note NOT LIKE '%NOT convictions%')"
    ).fetchone()[0]
    check("every stored criminal-case claim carries that caveat", unlabelled == 0,
          f"{unlabelled} unlabelled")
    con.close()

# 5 ---------------------------------------------------------------------------
print("\n[5] A wrong merge is a defamation risk.")
from ingest.resolve import AUTO_MERGE, REVIEW, normalise_name, score  # noqa: E402

check("auto-merge threshold is conservative", AUTO_MERGE >= 0.90, f"{AUTO_MERGE}")
check("there is a review band, not a binary decision", REVIEW < AUTO_MERGE,
      f"review {REVIEW} -> auto {AUTO_MERGE}")
# Calibration table. "merge" = safe to join automatically, "review" = a human
# must decide, "separate" = must never be joined. These are the cases the
# thresholds are tuned against; changing a threshold must keep them all passing.
CALIBRATION = [
    ("SHARMA RAJESH KUMAR", "Rajesh Kumar Sharma", "merge", "reordered + caps"),
    ("Smt. Sonia Gandhi", "Sonia Gandhi", "merge", "honorific only"),
    ("Shri R. K. Sharma", "Rajesh Kumar Sharma", "review", "abbreviated initials"),
    ("Dr. A. P. J. Abdul Kalam", "Abdul Pakir Jainulabdeen Kalam", "review",
     "initials vs full names"),
    ("Narendra Modi", "Narendra Damodardas Modi", "review", "dropped middle name"),
    ("Rajesh Kumar Sharma", "Ramesh Kumar Sharma", "separate", "one letter apart"),
    ("Rahul Gandhi", "Rajiv Gandhi", "separate", "real, different politicians"),
]
for a_raw, b_raw, want, label in CALIBRATION:
    sc = score(normalise_name(a_raw), normalise_name(b_raw))
    got = "merge" if sc >= AUTO_MERGE else ("review" if sc >= REVIEW else "separate")
    check(f"{want:8} - {label}", got == want, f"score {sc:.2f}, got {got}")

# 6 ---------------------------------------------------------------------------
print("\n[6] Absence of a marker is not evidence that nothing happened.")
if (WEBDATA / "meta.json").exists():
    meta = json.loads((WEBDATA / "meta.json").read_text(encoding="utf-8"))
    note = meta.get("caveats", {}).get("note", "")
    check("the coverage caveat ships with the data", "not exhaustive" in note)
    app = (ROOT / "web" / "src" / "App.jsx").read_text(encoding="utf-8")
    check("and the UI renders it", "caveats.note" in app or "caveats}" in app)

# 7 ---------------------------------------------------------------------------
print("\n[7] Indicators ship with their blind spots attached.")
cat = yaml.safe_load((CURATED / "indicators.yaml").read_text(encoding="utf-8"))
incomplete = [
    i["id"]
    for i in cat["indicators"]
    if not all(str(i.get(k, "")).strip() for k in ("plain", "measures", "blindspots"))
]
check("every indicator explains itself and its blind spots", not incomplete,
      f"incomplete: {incomplete}" if incomplete else f"{len(cat['indicators'])} indicators")
app = (ROOT / "web" / "src" / "App.jsx").read_text(encoding="utf-8")
check("blind spots are rendered as a warning, not hidden in a tooltip",
      'className="warn"' in app and "blindspots" in app)

# ------------------------------------------------------------------------------
print()
if failures:
    print(f"FAILED {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"All checks passed{f' ({len(skipped)} skipped)' if skipped else ''}.\n")
