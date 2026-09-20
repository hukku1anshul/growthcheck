"""Read `local.env` from the repository root into the environment.

    GOOGLE_FACTCHECK_KEY=AIza...
    # OCDS_URL=https://...

Only two things in this project take a secret - the Google Fact Check Tools key
and an optional OCDS endpoint - so this is deliberately twenty lines rather than
a dependency on python-dotenv.

WHY A FILE AND NOT JUST `setx`
------------------------------
`setx` works, but it puts the key in the Windows user profile where it is easy
to forget and awkward to rotate. A file next to the code is visible, editable
and deletable. The cost is that a file can be committed by accident, so:

  * `local.env` is in .gitignore, along with `.env` and `*.env`
  * `local.env.example` is the tracked template and holds no real value
  * a real environment variable ALWAYS wins over the file, so CI or a shell
    export is never silently overridden by a stale local file

Nothing here is read at request time. This is a build-time pipeline; the
published site is static and never sees a key.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = ROOT / "local.env"


def load(path: Path | None = None, *, quiet: bool = False) -> dict[str, str]:
    """Populate os.environ from `local.env`. Returns the names that were set.

    Values already present in the environment are left alone: an explicit
    export is a deliberate act and outranks a file the author may have
    forgotten about.
    """
    path = path or LOCAL_ENV
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # "export FOO=bar" is a habit people bring from a shell profile.
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            if not quiet:
                print(f"  local.env:{lineno}: ignored, no '=' in {line[:40]!r}")
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        # Quotes are for the shell's benefit, not ours. A key pasted as
        # "AIza..." must not arrive with the quotation marks attached.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not name:
            continue
        if os.environ.get(name):
            continue                      # a real export wins
        os.environ[name] = value
        loaded[name] = value

    if loaded and not quiet:
        # The NAMES only. Printing a secret into a log is how secrets escape.
        print(f"  local.env: loaded {', '.join(sorted(loaded))}", flush=True)
    return loaded
