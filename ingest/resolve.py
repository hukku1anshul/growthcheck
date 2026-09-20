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
REVIEW      = 0.78  # between: queue for a human. below: different people


def normalise_name(name: str) -> str:
    """Case-folded, de-honorificked, punctuation-free, token-sorted name."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = RELATION.sub(" ", s)
    s = s.lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in HONORIFICS]
    # Sorting makes "sharma rajesh" and "rajesh sharma" identical. South Asian and
    # Hungarian name ordering both vary by source, so order carries little signal.
    return " ".join(sorted(tokens))


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

    # match leftover single letters against leftover full tokens
    initial_hits = 0
    for x in sorted(rest_a):
        if len(x) == 1:
            hit = next((y for y in sorted(rest_b) if y.startswith(x)), None)
            if hit:
                rest_b.discard(hit)
                initial_hits += 1
    for y in sorted(list(rest_b)):
        if len(y) == 1:
            hit = next((x for x in sorted(rest_a) if x.startswith(y)), None)
            if hit:
                rest_a.discard(hit)
                initial_hits += 1

    matched = len(exact) + initial_hits * 0.85
    union = len(ta | tb) - initial_hits * 0.5
    return min(1.0, matched / union) if union else 0.0


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
    candidates = con.execute(
        "SELECT id, full_name, norm_name FROM persons WHERE country = ?", (country,)
    ).fetchall()
    # cheap blocking - only score names sharing the initials multiset
    candidates = [c for c in candidates if initials_key(c["norm_name"]) == key]

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
