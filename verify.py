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

# A relayed fact-check is the one place this project shows a VERDICT about a
# named living person. It is only defensible because the verdict is somebody
# else's, attributed and linked - and because the match is admitted to be by
# name alone. `claims:search` has no identifier to join on, so a review found
# for "Rajesh Verma" may be about a different Rajesh Verma, and publishing that
# against the wrong politician is the worst thing this feature could do.
if claims_db.exists():
    con = sqlite3.connect(f"file:{claims_db}?mode=ro", uri=True)
    total_fc = con.execute(
        "SELECT COUNT(*) FROM claims WHERE predicate = 'factcheck_published'"
    ).fetchone()[0]
    if total_fc:
        unwarned = con.execute(
            "SELECT COUNT(*) FROM claims WHERE predicate = 'factcheck_published' "
            "AND (note IS NULL OR note NOT LIKE '%MATCHED BY NAME%')"
        ).fetchone()[0]
        check("every relayed fact-check admits it was matched by name only",
              unwarned == 0, f"{unwarned} of {total_fc} missing the caveat")
        unattributed = con.execute(
            "SELECT COUNT(*) FROM claims WHERE predicate = 'factcheck_published' "
            "AND (note IS NULL OR note NOT LIKE '%not ours%')"
        ).fetchone()[0]
        check("no relayed rating is presented as our own finding",
              unattributed == 0, f"{unattributed} of {total_fc} unattributed")
        if people_js.exists():
            check("and the reader sees that caveat as a warning, not small print",
                  'className="warn"' in src and "factchecks" in src)
    else:
        skip("relayed fact-checks", "none harvested; set GOOGLE_FACTCHECK_KEY")
    con.close()

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
    # Pairs taken from the real MPLADS-vs-MyNeta reconciliation of 543 members.
    ("SHARMA RAJESH KUMAR", "Rajesh Kumar Sharma", "merge", "token order + case"),
    ("CN Annadurai", "C N Annadurai", "merge", "run-together initials"),
    ("Devendra Alias Bhole Singh", "Devendra Singh Alias Bhole Singh", "merge",
     "repeated surname token"),
    ("Balashowry Vallabbhaneni", "Balashowry Vallabhaneni", "merge",
     "transliteration, long surname"),
    ("Daggubati Purandeshwari", "Daggubati Purandheshwari", "merge",
     "transliteration, long surname"),
    ("Andimuthu Raja", "Raja A", "review", "initial vs full given name"),
    ("Devusinh Jesingbhai Chauhan", "Devusinh Chauhan", "review", "dropped patronymic"),
    ("Bhagirath Chaudhary", "Pankaj Chaudhary", "separate", "shared surname only"),
    ("Venkatesan S", "V. Somanna", "separate_or_review",
     "different MPs; must never auto-merge"),
    ("Selvaganapathi T M", "Tharaniventhan M S", "separate_or_review",
     "different MPs; must never auto-merge"),
]
for a_raw, b_raw, want, label in CALIBRATION:
    sc = score(normalise_name(a_raw), normalise_name(b_raw))
    got = "merge" if sc >= AUTO_MERGE else ("review" if sc >= REVIEW else "separate")
    # "separate_or_review" means: a human may look, but the machine must not
    # decide. These are genuinely different politicians whose names score high.
    ok = (got in ("separate", "review")) if want == "separate_or_review" else (got == want)
    check(f"{want:18} - {label}", ok, f"score {sc:.2f}, got {got}")

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

# 8 ---------------------------------------------------------------------------
print("\n[8] No source file contains a control character where an escape was meant.")
# `_seat_key` was written with the rule `\b(sc|st)\b` and reached the repo as two
# literal backspace bytes, because a shell heredoc ate the escapes. The result
# compiled, imported, ran, and matched NOTHING - a rule that silently did not
# exist. No test caught it and no reviewer would: the line looks correct in most
# editors, which render 0x08 as nothing at all.
#
# Any of these bytes inside a source file means an escape was eaten the same way.
CONTROL = {0x00: "\\0", 0x07: "\\a", 0x08: "\\b",
           0x0b: "\\v", 0x0c: "\\f", 0x1b: "\\e"}
damaged = []
for path in sorted(ROOT.rglob("*.py")) + sorted((ROOT / "web" / "src").rglob("*.js*")):
    rel = path.relative_to(ROOT).as_posix()
    if "node_modules" in rel or rel.startswith("data/"):
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for lineno, line in enumerate(text.splitlines(), 1):
        hit = next((CONTROL[ord(c)] for c in line if ord(c) in CONTROL), None)
        if hit:
            damaged.append(f"{rel}:{lineno} (should be {hit})")
check("no eaten escapes in any source file", not damaged,
      "; ".join(damaged) if damaged else "scanned every .py and web/src/*.js*")

# 9 ---------------------------------------------------------------------------
print("\n[9] The 'sources disagree' warning is not dominated by one predicate.")
# A conflict is only meaningful for a fact with ONE value per person per date.
# When a predicate that legitimately repeats is not declared in
# etl.export_people.SINGLE_VALUED, every repetition becomes a fake
# disagreement - and because the fakes arrive in bulk, they drown the real ones.
#
# `parliamentary_question` and `disclosure_filed` did exactly this: 90% of every
# conflict on the site was an MP tabling more than one question in a day. The
# shape of that failure is always the same, so the shape is what is checked.
people_dir = ROOT / "web" / "public" / "data" / "people"
index_file = people_dir / "index.json"
if not index_file.exists():
    skip("conflict mix", "no exported bundle yet - run python -m etl.export_people")
else:
    per_predicate: dict[str, int] = {}
    for person_file in people_dir.glob("*.json"):
        if person_file.name == "index.json":
            continue
        try:
            doc = json.loads(person_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for conflict in doc.get("conflicts") or []:
            key = conflict.get("predicate", "?")
            per_predicate[key] = per_predicate.get(key, 0) + 1
    total = sum(per_predicate.values())
    if not total:
        check("no predicate dominates the conflict count", True, "no conflicts")
    else:
        predicate, count = max(per_predicate.items(), key=lambda kv: kv[1])
        share = count / total
        check(
            "no predicate dominates the conflict count",
            share <= 0.5,
            f"largest is {predicate} at {count}/{total} ({share:.0%})"
            + ("  <- is it really single-valued per date? see SINGLE_VALUED"
               if share > 0.5 else ""),
        )

# 10 --------------------------------------------------------------------------
print("\n[10] No credential reaches the published data.")
# Provenance is this project's whole argument, so every claim ships the URL it
# came from and the site renders it as a link. That makes the URL a PUBLISHING
# channel, and an API that authenticates with `?key=` turns the operator's
# secret into public data the moment their extractor runs.
#
# ingest.archive.redact() masks these before anything is written down. This
# check is the backstop: it reads what is actually on disk and about to ship.
import re as _re  # noqa: E402
import urllib.parse as _urlparse  # noqa: E402

from ingest.archive import SECRET_PARAMS, is_secret  # noqa: E402

# Candidate parameters, judged by ingest.archive.is_secret rather than by name.
# Judging on the name alone is wrong and was caught here: MPLADS requests a
# metric with `key=Allocated Limit for Hon'ble MPs`, which is a field selector,
# not a credential. The scanner and the redactor must agree about what a secret
# is, so they share one function.
candidate = _re.compile(
    r"(%s)=([^&\"'\s\\]+)" % "|".join(sorted(SECRET_PARAMS)), _re.IGNORECASE
)
# Vendor-shaped keys, in case one arrives somewhere other than a query string.
vendor_key = _re.compile(r"AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9]{32,}")


def _leaks(text: str) -> bool:
    if vendor_key.search(text):
        return True
    for name, raw in candidate.findall(text):
        if is_secret(name, _urlparse.unquote_plus(raw)):
            return True
    return False


leaks: list[str] = []
data_root = ROOT / "web" / "public" / "data"
for blob in sorted(data_root.rglob("*.json")) if data_root.exists() else []:
    try:
        text = blob.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    if _leaks(text):
        leaks.append(blob.relative_to(ROOT).as_posix())

# The claim store is not published, but a secret there becomes one at the next
# export, so it is worth failing on now rather than after it ships.
if claims_db.exists():
    con = sqlite3.connect(f"file:{claims_db}?mode=ro", uri=True)
    try:
        for (src_url,) in con.execute("SELECT url FROM sources"):
            if src_url and _leaks(src_url):
                leaks.append("claims.db sources.url")
                break
    except sqlite3.Error:
        pass
    con.close()

check("no API key in any exported bundle or source URL", not leaks,
      "; ".join(leaks[:5]) if leaks
      else f"scanned {sum(1 for _ in data_root.rglob('*.json')) if data_root.exists() else 0}"
           " json files and every source URL")

# ------------------------------------------------------------------------------
print()
if failures:
    print(f"FAILED {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"All checks passed{f' ({len(skipped)} skipped)' if skipped else ''}.\n")
