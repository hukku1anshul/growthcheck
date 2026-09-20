"""Entity resolution.

This is the part that decides whether the project works. Charts are easy; deciding
that "Shri R. K. Sharma", "Rajesh Kumar Sharma" and "SHARMA RAJESH KUMAR" are one
person across four portals in three scripts is not.

The approach here is deliberately conservative and auditable rather than clever:

  1. Normalise aggressively (honorifics, punctuation, case, ordering).
  2. Block on a cheap key so we never compare all pairs.
  3. Score candidates, and only auto-merge above a high threshold.
  4. Everything between the two thresholds goes to a review queue, not a guess.

A wrong merge attributes one person's assets or criminal cases to another. That is
a defamation risk, not a data-quality annoyance, so the default is to refuse to
merge and leave two records rather than to merge and be wrong.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone

# Titles and honorifics that carry no identifying information.
HONORIFICS = {
    "shri", "shrimati", "smt", "sri", "mr", "mrs", "ms", "miss", "dr", "prof",
    "adv", "advocate", "er", "eng", "capt", "col", "maj", "gen", "lt", "sh",
    "kumari", "km", "thiru", "tmt", "selvi", "hon", "honourable", "late",
    "md", "mohd", "shrimathi", "shrimati",
}

# Relationship markers that appear inside affidavit name fields.
RELATION = re.compile(r"\b(s/o|d/o|w/o|c/o|son of|daughter of|wife of)\b.*", re.I)

AUTO_MERGE = 0.93   # above this: same person
REVIEW = 0.62       # between: queue for a human. below: different people
#
# REVIEW is calibrated against the cases in verify.py, not picked by feel. The
# binding constraint is the pair of real Indian politicians "Rahul Gandhi" and
# "Rajiv Gandhi" (0.33) and the near-miss "Rajesh/Ramesh Kumar Sharma" (0.50):
# both must stay well below it. Dropping from 0.78 to 0.62 brings dropped middle
# names - "Narendra Modi" vs "Narendra Damodardas Modi" (0.67), very common in
# Indian and Spanish naming - into human review instead of silently splitting
# them, while keeping a comfortable margin above the true negatives.
#
# AUTO_MERGE must stay high, and here is the evidence from a real 543-member run.
# These three pairs are DIFFERENT sitting MPs, and they scored:
#
#     Venkatesan S        vs  V. Somanna            0.85
#     Selvaraj V          vs  Venkatesan S          0.85
#     Selvaganapathi T M  vs  Tharaniventhan M S    0.90
#
# South Indian naming defeats the initial-matching heuristic: a lone initial is
# often the father's name rather than a first name, token order varies by source,
# and a single letter will happily match the first letter of any long given name.
# All three landed in the review band and were kept apart, which is the system
# working. Lowering AUTO_MERGE towards 0.85 would have merged three pairs of real
# politicians and attributed one person's assets and pending cases to another.


def normalise_name(name: str) -> str:
    """Case-folded, de-honorificked, punctuation-free, token-sorted name."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = RELATION.sub(" ", s)
    s = s.lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in HONORIFICS]
    # Split run-together initials: sources write both "C N Annadurai" and
    # "CN Annadurai", and treating "cn" as one token made them score 0.62 and
    # stay apart. A short token with no vowel is initials, not a name.
    expanded: list[str] = []
    for t in tokens:
        if 2 <= len(t) <= 3 and not set(t) & set("aeiou"):
            expanded.extend(t)
        else:
            expanded.append(t)
    # Sorting makes "sharma rajesh" and "rajesh sharma" identical. South Asian and
    # Hungarian name ordering both vary by source, so order carries little signal.
    return " ".join(sorted(expanded))


def _near(a: str, b: str) -> bool:
    """True if two LONG tokens differ by a single character.

    Transliteration noise scales with name length: "vallabhaneni" and
    "vallabbhaneni" are the same surname spelled two ways, and so are
    "purandeshwari" and "purandheshwari". Short names are the opposite - "rajesh"
    and "ramesh" also differ by one character and belong to different people, as
    do "rahul" and "rajiv". So this deliberately applies only at length >= 8,
    where a one-character difference is far more likely to be a spelling variant
    than a different name.
    """
    if min(len(a), len(b)) < 8 or abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    if len(a) == len(b):  # substitution
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)  # insertion
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


def initials_key(norm: str) -> str:
    """Blocking key: sorted first letters. Cheap, and stable under abbreviation."""
    return "".join(sorted(t[0] for t in norm.split() if t))


def score(a: str, b: str) -> float:
    """Similarity of two normalised names, 0..1.

    Token-set Jaccard, with credit for initial-vs-full-token matches so that
    'r k sharma' scores well against 'rajesh kumar sharma'.
    """
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0

    exact = ta & tb
    rest_a, rest_b = ta - exact, tb - exact

    # Match leftover single letters against leftover full tokens, in both
    # directions. Both sides of a match must be consumed: removing only the
    # full token leaves the initial sitting in the unmatched pile, where it is
    # counted a second time as evidence of difference.
    initial_hits = 0
    for x in sorted(list(rest_a)):
        if len(x) == 1:
            hit = next((y for y in sorted(rest_b) if y.startswith(x)), None)
            if hit:
                rest_a.discard(x)
                rest_b.discard(hit)
                initial_hits += 1
    for y in sorted(list(rest_b)):
        if len(y) == 1:
            hit = next((x for x in sorted(rest_a) if x.startswith(y)), None)
            if hit:
                rest_b.discard(y)
                rest_a.discard(hit)
                initial_hits += 1

    # Long tokens that differ by one character are almost certainly the same name
    # spelled two ways. Scored slightly below an exact match so a pair resting on
    # them lands in review rather than auto-merging.
    near_hits = 0
    for x in sorted(list(rest_a)):
        hit = next((y for y in sorted(rest_b) if _near(x, y)), None)
        if hit:
            rest_a.discard(x)
            rest_b.discard(hit)
            near_hits += 1

    # The denominator counts distinct *people-name components*, not distinct
    # strings. When an initial matches a full token they are one component, so
    # counting both in the union understates the similarity: "r k sharma" vs
    # "rajesh kumar sharma" scored 0.68 that way and fell below even the review
    # band, meaning an obvious same-person candidate was silently split in two.
    components = len(exact) + initial_hits + near_hits + len(rest_a) + len(rest_b)
    matched = len(exact) + initial_hits * 0.85 + near_hits * 0.9
    return min(1.0, matched / components) if components else 0.0


def find_or_create(
    con: sqlite3.Connection,
    country: str,
    full_name: str,
    source_id: int,
    *,
    context: str | None = None,
) -> tuple[int, str]:
    """Resolve `full_name` to a person id.

    Returns (person_id, action) where action is 'matched', 'created' or 'review'.
    `context` (constituency, party, year) is recorded on the alias so a human
    reviewing an ambiguous pair has something to go on.
    """
    norm = normalise_name(full_name)
    if not norm:
        raise ValueError(f"name normalised to nothing: {full_name!r}")

    key = initials_key(norm)
    tokens = set(norm.split())
    rows = con.execute(
        "SELECT id, full_name, norm_name FROM persons WHERE country = ?", (country,)
    ).fetchall()

    # Blocking decides what even gets compared, so it caps recall absolutely: a
    # pair the blocker rejects can never be merged no matter how well it scores.
    # Keying on the initials multiset alone was far too strict, because any
    # difference in token COUNT changes the key. "Devendra Alias Bhole Singh" and
    # "Devendra Singh Alias Bhole Singh" score 1.00 against each other and were
    # never compared, and 74 of 543 MPLADS members failed to match a MyNeta record
    # for this reason alone.
    #
    # Sharing any substantial name token is a much better candidate test. It is
    # looser, so more pairs are scored - but scoring is where precision lives, and
    # the thresholds and review band are unchanged. Over a few hundred people per
    # country the extra comparisons cost nothing.
    candidates = [
        c for c in rows
        if initials_key(c["norm_name"]) == key
        or tokens & {t for t in c["norm_name"].split() if len(t) >= 3}
    ]

    best, best_score = None, 0.0
    for c in candidates:
        s = score(norm, c["norm_name"])
        if s > best_score:
            best, best_score = c, s

    if best and best_score >= AUTO_MERGE:
        pid, action = best["id"], "matched"
    elif best and best_score >= REVIEW:
        # Ambiguous. Create a separate person rather than risk a wrong merge, and
        # leave a breadcrumb so the pair can be reviewed.
        pid = _create(con, country, full_name, norm)
        con.execute(
            """INSERT OR IGNORE INTO claims
               (person_id, subject, predicate, value_text, source_id, extractor,
                confidence, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pid, "person", "possible_duplicate_of", str(best["id"]), source_id,
             "resolve", round(best_score, 3),
             f"scored {best_score:.3f} against {best['full_name']!r}; "
             f"below auto-merge threshold {AUTO_MERGE}"),
        )
        action = "review"
    else:
        pid = _create(con, country, full_name, norm)
        action = "created"

    con.execute(
        "INSERT OR IGNORE INTO person_aliases (person_id, alias, norm_alias, source_id)"
        " VALUES (?,?,?,?)",
        (pid, f"{full_name}{f' [{context}]' if context else ''}", norm, source_id),
    )
    return pid, action


def _create(con: sqlite3.Connection, country: str, full_name: str, norm: str) -> int:
    cur = con.execute(
        "INSERT INTO persons (country, full_name, norm_name, created_at) VALUES (?,?,?,?)",
        (country, full_name.strip(), norm,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    return cur.lastrowid
