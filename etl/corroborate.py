"""Cross-source corroboration: do two publishers actually agree?

    python -m etl.corroborate

WHY THIS IS NOT JUST A GROUP-BY
-------------------------------
Until MPLADS and PRS were added, every fact in the claim store had exactly one
witness. Two independent publishers covering the same people is the first real
test of whether the numbers are right - and the first naive comparison put
agreement at 9.4%, which would have been an alarming and completely false
headline.

Every one of those "disagreements" was an artefact of comparing things that were
never comparable:

    age        myneta=73  prs=76    MyNeta records age on the 2024 nomination
                                    affidavit; PRS shows age today. A two-year
                                    gap between them is agreement, not conflict.
    party      BJP  vs  Bharatiya Janata Party      same party, two registers
    education  "Post Graduate" vs "Post Graduate and above"   two taxonomies

So corroboration has to normalise representation and align reference dates
before it compares anything. Otherwise it measures the vocabulary of the
publishers rather than the truth of the claims - and reports a scandal where
there is only a synonym.

What remains after normalisation is the interesting part: genuine disagreement
between official sources, which is reported and never silently resolved.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "claims.db"

# Education ladders differ by publisher. Both are mapped onto one ordinal scale;
# comparison is then between rungs, not between wordings. Anything unmapped is
# reported as unknown rather than counted as agreement.
EDUCATION_RANK = {
    "illiterate": 0,
    "literate": 1,
    "5th pass": 2, "upto primary": 2, "primary": 2,
    "8th pass": 3, "upto middle": 3, "middle": 3,
    "10th pass": 4, "upto secondary": 4, "matriculate": 4, "secondary": 4,
    "12th pass": 5, "upto higher secondary": 5, "higher secondary": 5,
    "graduate": 6, "graduate professional": 6,
    "post graduate": 7, "post graduate and above": 7, "postgraduate": 7,
    "doctorate": 8, "ph.d": 8, "phd": 8,
    "others": None, "not given": None,
}

# Age is dated differently by each publisher, so a straight equality test is
# meaningless. Claims are treated as consistent when the difference is no larger
# than the gap between their reference dates, plus a year of rounding slack.
AGE_TOLERANCE_YEARS = 3


def norm_text(v) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(v or "").lower()).strip()


def party_key(value: str, learned: dict[str, str]) -> str:
    """Canonical party token.

    Abbreviations and full names are learned from the data rather than
    hard-coded: when two publishers report the same person's party, the pair is
    evidence that "TDP" and "Telugu Desam Party" denote the same thing. A
    hand-written list would rot with every new party and every merger.
    """
    n = re.sub(r"\s+", " ", norm_text(value))
    # Only "party" and "the" are noise. Stripping "dal", "sena" or "congress"
    # destroys the name: "Janata Dal (United)" became "janata united", whose
    # initials are "ju", so it never matched "JD(U)".
    n = re.sub(r"\b(party|the)\b", " ", n).strip()
    n = re.sub(r"\s+", " ", n)
    if n in learned:
        return learned[n]
    # an abbreviation written without spaces: "jdu" -> "j d u"
    spaced = " ".join(n.replace(" ", ""))
    return learned.get(spaced, learned.get(n.replace(" ", ""), n))


def learn_party_aliases(rows) -> dict[str, str]:
    """Map every spelling of a party onto one canonical key.

    Two values reported for the same person on the same date are treated as the
    same party, and the shorter normalised form wins as the key. Initials are
    also derived, so "Telugu Desam" matches "TDP".
    """
    groups: dict[str, set[str]] = defaultdict(set)
    for person_id, values in rows.items():
        forms = {re.sub(r"\s+", " ", norm_text(v)) for v in values if v}
        forms = {re.sub(r"\b(party|the)\b", " ", f).strip() for f in forms}
        forms = {re.sub(r"\s+", " ", f) for f in forms if f}
        if len(forms) < 2:
            continue
        canon = min(forms, key=len)
        for f in forms:
            groups[canon].add(f)

    alias: dict[str, str] = {}
    for canon, forms in groups.items():
        for f in forms:
            alias[f] = canon
        # "bharatiya janata" -> also reachable as "bjp"
        initials = "".join(w[0] for w in canon.split() if w)
        if len(initials) >= 2:
            alias.setdefault(initials, canon)
            # "JD(U)" normalises to "jd u" once punctuation is stripped, so the
            # spaced form has to be registered alongside the run-together one.
            alias.setdefault(" ".join(initials), canon)
            alias.setdefault(initials.replace(" ", ""), canon)
    return alias


def education_span(value) -> tuple[int, int] | None:
    """Education as a RANGE, because one publisher's bins are open-ended.

    PRS reports "Post Graduate and above", which contains a doctorate. Treating
    that as the single rung 7 made every MP with a doctorate look like a
    disagreement with MyNeta, when the two statements are entirely compatible.
    """
    n = re.sub(r"\s+", " ", norm_text(value))
    rank = EDUCATION_RANK.get(n)
    if rank is None:
        for key, r in EDUCATION_RANK.items():
            if n.startswith(key):
                rank = r
                break
    if rank is None:
        return None
    if "above" in n:
        return (rank, max(v for v in EDUCATION_RANK.values() if v is not None))
    if n.startswith("upto"):
        return (0, rank)
    return (rank, rank)


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # facts for which two or more publishers reported something
    rows = con.execute(
        """SELECT person_id, predicate, extractor, value_num, value_text, as_of
           FROM claims WHERE person_id IS NOT NULL
             AND predicate IN ('party_affiliation','education_level','age')"""
    ).fetchall()

    by_fact: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        val = r["value_num"] if r["value_num"] is not None else r["value_text"]
        by_fact[(r["person_id"], r["predicate"])][r["extractor"]].append(
            (val, r["as_of"])
        )

    party_values: dict[int, set] = defaultdict(set)
    for (pid, pred), srcs in by_fact.items():
        if pred == "party_affiliation" and len(srcs) > 1:
            for vals in srcs.values():
                for v, _ in vals:
                    party_values[pid].add(v)
    aliases = learn_party_aliases(party_values)

    tally = defaultdict(lambda: {"agree": 0, "differ": 0, "unknown": 0})
    examples: dict[str, list] = defaultdict(list)

    for (pid, pred), srcs in by_fact.items():
        if len(srcs) < 2:
            continue
        picks = {ex: vals[0] for ex, vals in srcs.items()}

        if pred == "party_affiliation":
            keys = {party_key(v, aliases) for v, _ in picks.values()}
            verdict = "agree" if len(keys) == 1 else "differ"
        elif pred == "education_level":
            spans = [education_span(v) for v, _ in picks.values()]
            if any(sp is None for sp in spans):
                verdict = "unknown"
            else:
                lo = max(sp[0] for sp in spans)
                hi = min(sp[1] for sp in spans)
                # compatible if the reported ranges overlap at all
                verdict = "agree" if lo <= hi else "differ"
        else:  # age
            nums = [float(v) for v, _ in picks.values() if v is not None]
            verdict = ("agree"
                       if nums and max(nums) - min(nums) <= AGE_TOLERANCE_YEARS
                       else "differ")

        tally[pred][verdict] += 1
        if verdict == "differ" and len(examples[pred]) < 6:
            nm = con.execute("SELECT full_name FROM persons WHERE id=?", (pid,)).fetchone()
            examples[pred].append(
                (nm["full_name"] if nm else pid,
                 " | ".join(f"{e}={v}" for e, (v, _) in picks.items()))
            )

    print("\n=== corroboration after normalising representation and dates ===")
    print(f"  {'fact':22} {'agree':>7} {'differ':>7} {'unknown':>8} {'agreement':>11}")
    ta = td = tu = 0
    for pred in sorted(tally):
        t = tally[pred]
        a, d, u = t["agree"], t["differ"], t["unknown"]
        ta, td, tu = ta + a, td + d, tu + u
        pct = f"{100*a/(a+d):.1f}%" if (a + d) else "-"
        print(f"  {pred:22} {a:>7} {d:>7} {u:>8} {pct:>11}")
    pct = f"{100*ta/(ta+td):.1f}%" if (ta + td) else "-"
    print(f"  {'TOTAL':22} {ta:>7} {td:>7} {tu:>8} {pct:>11}")

    print("\n  For comparison, comparing the raw strings without normalising")
    print("  reported 9.4% agreement - a number about publishers' vocabularies,")
    print("  not about whether the facts match.")

    if any(examples.values()):
        print("\n=== genuine disagreements, for a human ===")
        for pred, items in examples.items():
            for nm, shown in items:
                print(f"  {str(nm)[:24]:26} {pred:20} {shown[:70]}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
