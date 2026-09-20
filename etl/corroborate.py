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

WHAT TIME DOES TO THIS, LEARNED THE SECOND TIME
-----------------------------------------------
Agreement had settled at 97.5%. Adding the 2019 Lok Sabha affidavits and the
2026 OpenSanctions/Wikidata witnesses dropped it to 57.3% overnight - and not
one of those new disagreements was real. Three separate date bugs, each of which
only became visible once a person had more than one claim from the same
publisher:

  1. The comparison took an ARBITRARY claim per publisher. With 2019 and 2024
     affidavits both present, it would compare MyNeta's 2019 age against PRS's
     2024 age and call a man who had simply had five birthdays a disagreement.

  2. Age was compared as a raw number. Age is not a property of a person, it is
     a property of a person ON A DATE; the invariant is the implied BIRTH YEAR.
     50 in 2019, 55 in 2024 and 58 in 2026 are one consistent fact, and the
     docstring here claimed to align reference dates while the code below simply
     did not.

  3. Worst of the three: the alias learner took every party value a person had
     ever been given as evidence that those names denote the same party. Once
     2019 was in the store, every DEFECTOR taught it that "Indian National
     Congress" means "BJP". One switcher poisons that key for all 1,008 people,
     and which way it broke depended on dict ordering.

The repair is that co-occurrence is no longer enough to merge two party names.
They must co-occur FOR THE SAME PERSON IN THE SAME YEAR - two publishers
describing one person's party at one moment - and the two spellings must also be
structurally related, one being the other's initials or a morphological variant.
Defection is then indistinguishable from what it is: a change of party, not a
change of name.
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

# Slack on the IMPLIED BIRTH YEAR, not on the reported age. Publishers round
# differently - an affidavit age is as at nomination, PRS shows age today, and a
# witness derived from a birth date is exact - so two years of drift plus one of
# rounding is agreement.
AGE_TOLERANCE_YEARS = 3

# Party is the one fact here that legitimately CHANGES. Comparing a 2019
# affidavit against a 2026 witness asks whether the person defected, not whether
# the publishers agree, so claims more than this far apart are not compared at
# all and are counted separately.
PARTY_WINDOW_YEARS = 2


def norm_text(v) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(v or "").lower()).strip()


def _strip(value) -> str:
    """Normalised party name with only the genuinely empty words removed.

    Stripping "dal", "sena" or "congress" destroys the name: "Janata Dal
    (United)" became "janata united", whose initials are "ju", so it never
    matched "JD(U)".
    """
    n = re.sub(r"\s+", " ", norm_text(value))
    n = re.sub(r"\b(party|the)\b", " ", n).strip()
    return re.sub(r"\s+", " ", n)


# Words nobody puts in an abbreviation. "Communist Party of India
# (Marxist-Leninist) (Liberation)" is CPI(ML)(L), never CPOIML(L).
INITIAL_SKIP = {"of", "and", "the"}


def initials_of(value) -> str:
    """The abbreviation a party name would be written as.

    Taken from the FULL name, before "party" is stripped: "Bharatiya Janata
    Party" abbreviates to BJP, and the P is the word this module otherwise
    discards as noise.

    A token that is already an acronym contributes ALL of its letters. "YSR
    Congress Party" is YSRCP, not YCP, because the YSR is itself three
    initials - which is why this reads the original casing rather than the
    lower-cased form.
    """
    out = []
    for tok in re.sub(r"[^A-Za-z0-9 ]", " ", str(value or "")).split():
        if tok.lower() in INITIAL_SKIP:
            continue
        out.append(tok.lower() if len(tok) >= 2 and tok.isupper() else tok[0].lower())
    return "".join(out)


def _variant_of(a: str, b: str, ia: str, ib: str) -> bool:
    """Is one of these two spellings plausibly a shorthand for the other?

    The structural check that stops a defection from being read as a synonym.
    Co-occurrence says "one person, one moment, two labels"; this says whether
    the two labels can be the same NAME. "BJP"/"Bharatiya Janata Party" passes
    on initials, "BJP"/"Indian National Congress" cannot pass on anything.
    """
    ca, cb = a.replace(" ", ""), b.replace(" ", "")
    if ca == cb:
        return True
    if ca == ib or cb == ia:                     # "bjp" <-> Bharatiya Janata Party
        return True
    short, long_ = sorted((ca, cb), key=len)
    # A morphological tail only - "democrat"/"democratic". Deliberately NOT a
    # general prefix test, which would merge "Janata Dal" into "Janata Dal
    # (United)"; those are two parties, and the split is the whole point of the
    # second name.
    if len(short) >= 6 and long_.startswith(short) and len(long_) - len(short) <= 3:
        return True
    return False


def learn_party_aliases(rows) -> dict[str, str]:
    """Map every spelling of a party onto one canonical key.

    `rows` is {person_id: {(value, as_of), ...}}. Aliases are learned from the
    data rather than hard-coded, because a hand-written list would rot with every
    new party and every merger - but the evidence required is narrow: the two
    spellings must describe the same person IN THE SAME YEAR, and must pass
    `_variant_of`. Both conditions exist to keep party switchers out; see the
    module docstring.
    """
    # Two publishers describing one person at one moment.
    slots: dict[tuple, set] = defaultdict(set)
    for person_id, values in rows.items():
        for value, as_of in values:
            if value:
                slots[(person_id, str(as_of or "")[:4])].add(str(value))

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            # Shorter spelling wins the key, so groups converge on "bjp".
            lo, hi = sorted((rx, ry), key=lambda s: (len(s), s))
            parent[hi] = lo

    for forms in slots.values():
        if len(forms) < 2:
            continue
        pairs = [(_strip(f), initials_of(f)) for f in forms]
        pairs = [(s, i) for s, i in pairs if s]
        for n, (a, ia) in enumerate(pairs):
            for b, ib in pairs[n + 1:]:
                if _variant_of(a, b, ia, ib):
                    union(a, b)

    alias = {f: find(f) for f in list(parent)}

    # A publisher may use an abbreviation nobody was ever observed alongside, so
    # each group's own initials are registered too - but only when exactly one
    # group claims them. An abbreviation two parties would answer to is worse
    # than no abbreviation at all.
    derived: dict[str, set] = defaultdict(set)
    for canon in set(alias.values()):
        ini = "".join(w[0] for w in canon.split() if w)
        if len(ini) >= 2:
            for form in (ini, " ".join(ini)):
                if form not in alias:
                    derived[form].add(canon)
    for form, canons in derived.items():
        if len(canons) == 1:
            alias[form] = next(iter(canons))
    return alias


def party_key(value: str, learned: dict[str, str]) -> str:
    """Canonical party token; falls back to the normalised name itself.

    The last resort is the name's own abbreviation. A publisher can use a
    spelling that was never seen beside any other - Wikidata calls the YSRCP
    "YSR Congress Party", which appears in no affidavit - and reducing it to
    YSRCP finds the group it belongs to. That lookup is safe because an
    abbreviation two different parties answer to is never registered.
    """
    n = _strip(value)
    if n in learned:
        return learned[n]
    for cand in (" ".join(n.replace(" ", "")),    # "jdu" -> "j d u"
                 n.replace(" ", ""),
                 initials_of(value)):
        if cand in learned:
            return learned[cand]
    return n


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


def year_of(as_of) -> int | None:
    m = re.match(r"^(\d{4})", str(as_of or ""))
    return int(m.group(1)) if m else None


def birth_year(age, as_of) -> float | None:
    """The invariant behind a reported age.

    An age is only meaningful with the date it was reported on. Comparing
    publishers means comparing what each of them implies about when the person
    was born.
    """
    y = year_of(as_of)
    if y is None or age is None:
        return None
    try:
        return y - float(age)
    except (TypeError, ValueError):
        return None


def main() -> int:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

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
        if pred == "party_affiliation":
            for vals in srcs.values():
                for v, when in vals:
                    party_values[pid].add((v, when))
    aliases = learn_party_aliases(party_values)

    tally = defaultdict(lambda: {"agree": 0, "differ": 0, "unknown": 0, "stale": 0})
    examples: dict[str, list] = defaultdict(list)

    for (pid, pred), srcs in by_fact.items():
        if len(srcs) < 2:
            continue
        # The publisher's most recent statement, not an arbitrary one.
        picks = {ex: max(vals, key=lambda t: str(t[1] or ""))
                 for ex, vals in srcs.items()}

        if pred == "party_affiliation":
            years = {ex: year_of(d) for ex, (_, d) in picks.items()}
            known = [y for y in years.values() if y is not None]
            if known:
                latest = max(known)
                picks = {ex: p for ex, p in picks.items()
                         if years[ex] is None
                         or latest - years[ex] <= PARTY_WINDOW_YEARS}
            if len(picks) < 2:
                tally[pred]["stale"] += 1       # only comparable across a defection
                continue
            keys = {party_key(str(v), aliases) for v, _ in picks.values()}
            verdict = "agree" if len(keys) == 1 else "differ"
        elif pred == "education_level":
            spans = [education_span(v) for v, _ in picks.values()]
            if any(sp is None for sp in spans):
                verdict = "unknown"
            else:
                lo = max(sp[0] for sp in spans)
                hi = min(sp[1] for sp in spans)
                verdict = "agree" if lo <= hi else "differ"   # ranges overlap at all
        else:  # age -> compare the implied birth year
            born = [b for b in (birth_year(v, d) for v, d in picks.values())
                    if b is not None]
            if len(born) < 2:
                verdict = "unknown"
            else:
                verdict = ("agree"
                           if max(born) - min(born) <= AGE_TOLERANCE_YEARS
                           else "differ")

        tally[pred][verdict] += 1
        if verdict == "differ" and len(examples[pred]) < 6:
            nm = con.execute("SELECT full_name FROM persons WHERE id=?",
                             (pid,)).fetchone()
            examples[pred].append(
                (nm["full_name"] if nm else pid,
                 " | ".join(f"{e}={v}@{(d or '?')[:4]}" for e, (v, d) in picks.items()))
            )

    print("\n=== corroboration after normalising representation and dates ===")
    print(f"  {'fact':22} {'agree':>7} {'differ':>7} {'unknown':>8} {'agreement':>11}")
    ta = td = tu = ts = 0
    for pred in sorted(tally):
        t = tally[pred]
        a, d, u = t["agree"], t["differ"], t["unknown"]
        ta, td, tu, ts = ta + a, td + d, tu + u, ts + t["stale"]
        pct = f"{100*a/(a+d):.1f}%" if (a + d) else "-"
        print(f"  {pred:22} {a:>7} {d:>7} {u:>8} {pct:>11}")
    pct = f"{100*ta/(ta+td):.1f}%" if (ta + td) else "-"
    print(f"  {'TOTAL':22} {ta:>7} {td:>7} {tu:>8} {pct:>11}")
    if ts:
        print(f"\n  {ts} people had party reported only in years more than "
              f"{PARTY_WINDOW_YEARS} apart.\n  Those are not compared: the question "
              f"would be whether they changed party,\n  not whether the publishers "
              f"agree.")

    print("\n  For comparison, comparing the raw strings without normalising")
    print("  reported 9.4% agreement - a number about publishers' vocabularies,")
    print("  not about whether the facts match.")

    if any(examples.values()):
        print("\n=== genuine disagreements, for a human ===")
        for pred, items in examples.items():
            for nm, shown in items:
                print(f"  {str(nm)[:24]:26} {pred:20} {shown[:76]}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
