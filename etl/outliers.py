"""Cost comparison: which works cost far more than others of the same kind.

    python -m etl.outliers        # report, without touching the export

WHAT THIS IS, AND THE LINE IT DOES NOT CROSS
--------------------------------------------
Brazil's *Operação Serenata de Amor* built "Rosie", which reads congressional
reimbursements and flags the ones that look wrong for a human to check. It is
the most effective civic-data project of its kind, and the obvious question is
why this project does not do the same.

It can, and this is it - but the framing is the whole engineering problem.

    NOT THIS   "this member's spending is suspicious"
    THIS       "this work cost 273x the median for its category
                (median Rs 1.70 lakh across 5,180 works)"

The first is an accusation about a named living person and a judgement this
project must never publish. The second is arithmetic over published figures
with the comparison set attached, and a reader can check it, disagree with the
category, or find the innocent explanation - which very often exists.

The distinction is not a euphemism. It changes what is computed:

  * the subject is a WORK, never a person. There is no per-member count, no
    "most flagged MPs" list, and the figure is deliberately kept out of the
    index so the people table cannot be sorted by it. A ranking of politicians
    by outlier count is a score, and docs/ETHICS.md forbids scores.
  * every flag carries its denominator - the category, its median and how many
    works it was compared against. A ratio without its comparison set is an
    insinuation.
  * the language is descriptive. "Costs more than", never "suspicious",
    "irregular", "anomalous" or "flagged", because those words carry a verdict
    the arithmetic does not support.

WHY THE MEDIAN, AND WHY A RATIO
-------------------------------
These distributions are extremely skewed - a mean would be dragged upward by
the very works being looked for, and a standard-deviation rule then hides them.
The median is unmoved by outliers, and a ratio to it is a number a reader can
check by hand from the two figures shown.

MIN_SET exists because a "median" over four works is not a comparison, it is a
coincidence. 59 of the 100 MPLADS work categories clear it.

WHAT A HIGH RATIO IS NOT
------------------------
It is not evidence of anything on its own. A category bundles very different
jobs: "Street lights" covers one pole and an entire constituency's installation
booked as a single work. Terrain, materials, transport to remote districts and
multi-year scope all move cost legitimately. And under MPLADS the member
recommends - district authorities sanction, implement and pay - so the cost is
not the member's decision in the first place.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict

# A comparison set smaller than this is not a comparison.
MIN_SET = 30
# How many times the category median before a work is worth showing. Chosen so
# the list stays short enough to read: at 10x it is a few hundred works out of
# 35,000, which a person can actually look through.
RATIO = 10.0

# "category: WS/MP490/2024-2025/187515-Construction of roads, link roads, ..."
# The greedy prefix matters: a lazy one anchors on "/2024-" and returns the
# year plus the work id instead of the category.
CATEGORY = re.compile(r"category:\s*.*/\d+-(.+?)(?:\s*\|.*)?$")


def category_of(note: str | None) -> str | None:
    """The work category a MPLADS note describes, or None."""
    head = (note or "").split(" | agency:")[0]
    m = CATEGORY.search(head)
    return m.group(1).strip() if m else None


def baselines(works) -> dict[str, dict]:
    """{category: {median, n}} for every category with enough works to compare.

    `works` is an iterable of (category, amount).
    """
    by: dict[str, list] = defaultdict(list)
    for cat, amount in works:
        if cat and amount and amount > 0:
            by[cat].append(float(amount))
    return {
        cat: {"median": statistics.median(v), "n": len(v)}
        for cat, v in by.items()
        if len(v) >= MIN_SET and statistics.median(v) > 0
    }


def compare(amount: float, category: str, base: dict[str, dict]) -> dict | None:
    """How this work's cost compares with others of its kind, or None.

    Returns the comparison whenever one can be made - the caller decides what
    is worth showing. Handing back only the extremes would hide the denominator
    from the code that renders it.
    """
    b = base.get(category or "")
    if not b or not amount or amount <= 0:
        return None
    return {
        "category": category,
        "ratio": round(amount / b["median"], 1),
        "median": b["median"],
        "n": b["n"],
    }


def main() -> int:
    import sqlite3
    from pathlib import Path

    db = Path(__file__).resolve().parents[1] / "data" / "processed" / "claims.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [
        (category_of(r["note"]), r["value_num"], r["note"])
        for r in con.execute(
            "SELECT value_num, note FROM claims WHERE predicate = 'contract_awarded' "
            "AND extractor = 'mplads' AND value_num IS NOT NULL AND value_num > 0"
        )
    ]
    con.close()

    base = baselines((c, a) for c, a, _ in rows)
    print(f"\n{len(rows):,} works, {len({c for c, _, _ in rows if c})} categories")
    print(f"{len(base)} categories with at least {MIN_SET} works to compare against\n")

    flagged = []
    for cat, amount, note in rows:
        cmp_ = compare(amount, cat, base)
        if cmp_ and cmp_["ratio"] >= RATIO:
            flagged.append((cmp_["ratio"], amount, cmp_, note))
    flagged.sort(reverse=True, key=lambda t: t[0])

    print(f"{len(flagged):,} works cost at least {RATIO:g}x the median for their "
          f"category ({100*len(flagged)/len(rows):.1f}% of all works)\n")
    print("  largest ratios:")
    for ratio, amount, c, note in flagged[:12]:
        print(f"    Rs {amount/1e5:>9,.1f} lakh  {ratio:>7.1f}x median "
              f"(Rs {c['median']/1e5:.1f} lakh, n={c['n']:,})  {c['category'][:44]}")
    print("\n  A high ratio is not evidence of anything on its own. A category "
          "bundles\n  very different jobs, and under MPLADS the member recommends "
          "while district\n  authorities sanction, implement and pay.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
