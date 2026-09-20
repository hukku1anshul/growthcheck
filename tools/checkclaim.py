"""Check a claim about a politician or a country against sourced data.

    python tools/checkclaim.py "Jyotiraditya Scindia's assets grew 100 times since 2004"
    python tools/checkclaim.py "Priya Saroj has spent none of her MPLADS money"
    python tools/checkclaim.py "Rahul Gandhi has 20 criminal cases"
    python tools/checkclaim.py "India's GDP grew 10% in 2023"

WHAT THIS IS
------------
Fact-checkers check virality: is this image doctored, did this quote happen.
Nobody automatically checks a NUMERIC claim about a politician against the
politician's own sworn affidavit, or a claim about public money against the
scheme portal. This project already holds those figures with receipts, so the
missing piece is small: read the claim, find the person and the fact type, and
put the sourced number next to the claimed one.

WHAT THIS IS NOT
----------------
It never says "true" or "false". It says what the sourced record shows, how far
that is from the claim, and where the record came from - then stops. A claim can
be numerically off and substantially right ("assets grew 100x" when the record
says 118x), or numerically exact and misleading (a 0% MPLADS figure for a member
elected three months ago). The reader gets the evidence; the verdict is theirs.

It is deliberately rule-based. A language model here would be a liability: it
would produce a fluent answer for claims the data cannot support, and fluency is
precisely the failure mode a fact-checking tool must not have. Every branch below
either finds a sourced number or says "cannot check", and nothing in between.

Prototype. Person matching reuses the resolver's scoring; predicate detection is
keyword-based; number parsing handles crore/lakh/%, multiples and years. It will
miss phrasings. When it does, it says so.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingest.resolve import normalise_name, score  # noqa: E402

CLAIMS_DB = ROOT / "data" / "processed" / "claims.db"
PF_DB = ROOT / "data" / "processed" / "pf.db"

# Predicate detection: which words point at which sourced fact.
TOPICS = [
    ("criminal_cases_declared", r"\b(criminal|cases?|charges?|fir|accused)\b"),
    ("budget_spent",            r"\b(mplads|constituency fund|development fund|spent|utili[sz])"),
    ("budget_allocated",        r"\b(allocat|entitle)"),
    ("attendance_pct",          r"\battendance\b"),
    ("questions_asked",         r"\bquestions?\b"),
    ("declared_assets",         r"\b(assets?|wealth|net ?worth|property|rich|crore|crorepati)\b"),
    ("outside_earnings",        r"\b(earn|paid|payment|second job|outside income)\b"),
]

COUNTRY_TOPICS = [
    ("gdp_growth",   r"\bgdp\b.*\b(grew|growth|grow|expand)|\bgrowth rate\b"),
    ("gdp_pc",       r"\bgdp per capita\b|\bincome per (person|head)\b"),
    ("inflation",    r"\binflation\b|\bprices? (rose|rise)\b"),
    ("unemployment", r"\bunemploy"),
    ("poverty",      r"\bpoverty\b|\bpoor\b"),
    ("life_expectancy", r"\blife expectancy\b"),
]

INR_UNITS = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "lakhs": 1e5}


def parse_numbers(text: str) -> dict:
    t = text.lower()
    out = {"years": [int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-4]\d)\b", t)]}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|times|fold)\b", t)
    if m:
        out["multiple"] = float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)", t)
    if m:
        out["percent"] = float(m.group(1))
    m = re.search(r"(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(crore|cr|lakhs?|lac)\b", t)
    if m:
        out["amount"] = float(m.group(1)) * INR_UNITS[m.group(2)]
    if re.search(r"\b(no|zero|none|nil|0)\b", t):
        out["zero"] = True
    m = re.search(r"\b(\d{1,3})\b(?!\s*(?:%|x|times|crore|lakh|cr)\b)(?![\d.])", t)
    if m and "amount" not in out and "multiple" not in out:
        n = int(m.group(1))
        if n not in out["years"]:
            out["count"] = n
    return out


def find_person(con, text: str):
    """Best-scoring person whose name tokens appear in the claim."""
    words = re.findall(r"[a-z]+", text.lower())
    best, best_s = None, 0.0
    for r in con.execute("SELECT id, full_name, norm_name, country FROM persons"):
        toks = r["norm_name"].split()
        # every substantial token of the person's name must be in the claim
        if len(toks) < 2 or not all(t in words for t in toks if len(t) > 2):
            continue
        s = score(r["norm_name"], normalise_name(" ".join(w for w in words if w in toks)))
        if s > best_s:
            best, best_s = r, s
    return best


def sourced(con, pid: int, predicate: str):
    return con.execute(
        """SELECT c.value_num, c.value_text, c.as_of, c.note, c.confidence,
                  s.url, s.fetched_at, s.sha256
           FROM claims c JOIN sources s ON s.id = c.source_id
           WHERE c.person_id = ? AND c.predicate = ? ORDER BY c.as_of""",
        (pid, predicate),
    ).fetchall()


def fmt_inr(v: float) -> str:
    return f"Rs {v/1e7:,.2f} crore" if v >= 1e7 else f"Rs {v/1e5:,.2f} lakh"


def check_person(con, claim: str) -> list[str]:
    out: list[str] = []
    person = find_person(con, claim)
    if person is None:
        return ["CANNOT CHECK: no politician in the record matches a name in this claim."]
    out.append(f"person   : {person['full_name']} ({person['country']})")

    topic = next((p for p, rx in TOPICS if re.search(rx, claim, re.I)), None)
    if topic is None:
        return out + ["CANNOT CHECK: could not tell which kind of fact the claim is about."]
    nums = parse_numbers(claim)
    rows = sourced(con, person["id"], topic)
    if not rows:
        return out + [f"CANNOT CHECK: the record holds no '{topic}' facts for this person."]
    out.append(f"fact type: {topic}")

    if topic == "declared_assets":
        pts = [(int(r["as_of"][:4]), r["value_num"]) for r in rows if r["as_of"] and r["value_num"]]
        pts = sorted(dict(pts).items())
        out.append("record   : " + "; ".join(f"{y}: {fmt_inr(v)}" for y, v in pts))
        if "multiple" in nums and len(pts) >= 2:
            y0 = max([y for y in nums["years"] if y <= pts[0][0]] + [pts[0][0]]) if nums["years"] else pts[0][0]
            start = next((p for p in pts if p[0] >= y0), pts[0])
            mult = pts[-1][1] / start[1] if start[1] else None
            if mult:
                out.append(f"claimed  : x{nums['multiple']:g}   record: x{mult:.1f} "
                           f"({start[0]} -> {pts[-1][0]})   "
                           f"{'CONSISTENT' if abs(mult - nums['multiple'])/mult < 0.25 else 'NOT CONSISTENT'} "
                           f"(within 25%)")
        elif "amount" in nums:
            latest = pts[-1]
            diff = abs(latest[1] - nums["amount"]) / latest[1]
            out.append(f"claimed  : {fmt_inr(nums['amount'])}   record ({latest[0]}): {fmt_inr(latest[1])}   "
                       f"{'CONSISTENT' if diff < 0.1 else 'NOT CONSISTENT'} (within 10%)")

    elif topic == "criminal_cases_declared":
        latest = [r for r in rows if r["as_of"] and r["value_num"] is not None][-1]
        n = int(latest["value_num"])
        out.append(f"record   : {n} declared PENDING cases as of {latest['as_of'][:4]} "
                   f"(self-declared on the ECI affidavit; NOT convictions)")
        claimed = 0 if nums.get("zero") else nums.get("count")
        if claimed is not None:
            out.append(f"claimed  : {claimed}   record: {n}   "
                       f"{'CONSISTENT' if claimed == n else 'NOT CONSISTENT'}")

    elif topic in ("budget_spent", "budget_allocated"):
        spent = sourced(con, person["id"], "budget_spent")
        alloc = sourced(con, person["id"], "budget_allocated")
        s_val = spent[-1]["value_num"] if spent else 0.0
        a_val = alloc[-1]["value_num"] if alloc else None
        if a_val:
            out.append(f"record   : allocated {fmt_inr(a_val)}, completed works {fmt_inr(s_val)} "
                       f"= {100*s_val/a_val:.1f}% utilised")
            if nums.get("zero"):
                out.append(f"claimed  : none spent   record: {100*s_val/a_val:.1f}%   "
                           f"{'CONSISTENT' if s_val == 0 else 'NOT CONSISTENT'}")
            elif "percent" in nums:
                p = 100 * s_val / a_val
                out.append(f"claimed  : {nums['percent']:g}%   record: {p:.1f}%   "
                           f"{'CONSISTENT' if abs(p - nums['percent']) <= 5 else 'NOT CONSISTENT'} (within 5 points)")
            out.append("caveat   : the member RECOMMENDS works; district authorities sanction, "
                       "implement and pay. Utilisation reflects them as much as the member.")
        else:
            out.append("CANNOT CHECK: no MPLADS allocation on record for this person.")

    elif topic in ("attendance_pct", "questions_asked"):
        r = rows[-1]
        out.append(f"record   : {r['value_num']:g} ({r['note'] or 'no benchmark'})")
        c = nums.get("percent") if topic == "attendance_pct" else nums.get("count")
        if c is not None:
            out.append(f"claimed  : {c:g}   record: {r['value_num']:g}   "
                       f"{'CONSISTENT' if abs(c - r['value_num']) <= 3 else 'NOT CONSISTENT'}")

    src = rows[-1]
    out.append(f"source   : {src['url']}")
    out.append(f"archived : {src['fetched_at'][:10]}  sha256 {src['sha256'][:12]}")
    return out


def check_country(claim: str) -> list[str] | None:
    topic = next((p for p, rx in COUNTRY_TOPICS if re.search(rx, claim, re.I)), None)
    if topic is None or not PF_DB.exists():
        return None
    con = sqlite3.connect(f"file:{PF_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    countries = con.execute("SELECT iso3, name FROM countries").fetchall()
    hit = next((c for c in countries if c["name"].lower() in claim.lower()), None)
    if hit is None:
        return None
    nums = parse_numbers(claim)
    year = nums["years"][-1] if nums["years"] else None
    q = "SELECT year, value FROM observations WHERE iso3=? AND indicator=?"
    rows = con.execute(q + (" AND year=?" if year else " ORDER BY year DESC LIMIT 1"),
                       (hit["iso3"], topic, year) if year else (hit["iso3"], topic)).fetchall()
    ind = con.execute("SELECT name, unit FROM indicators WHERE id=?", (topic,)).fetchone()
    out = [f"country  : {hit['name']}", f"fact type: {ind['name']} ({ind['unit']})"]
    if not rows:
        return out + [f"CANNOT CHECK: no {topic} value for {hit['name']}" + (f" in {year}" if year else "")]
    y, v = rows[0]["year"], rows[0]["value"]
    out.append(f"record   : {v:.2f} in {y}  (World Bank WDI, via etl.build)")
    c = nums.get("percent")
    if c is not None and topic in ("gdp_growth", "inflation", "unemployment", "poverty"):
        out.append(f"claimed  : {c:g}   record: {v:.1f}   "
                   f"{'CONSISTENT' if abs(v - c) <= 1.0 else 'NOT CONSISTENT'} (within 1 point)")
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    claim = " ".join(sys.argv[1:])
    print(f'\nclaim    : "{claim}"')
    lines = check_country(claim)
    if lines is None:
        con = sqlite3.connect(f"file:{CLAIMS_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        lines = check_person(con, claim)
        con.close()
    for ln in lines:
        print(ln)
    print("verdict  : none. The record is above; the judgement is yours.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
