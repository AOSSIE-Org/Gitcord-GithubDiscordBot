from __future__ import annotations

import sys
from pathlib import Path


# Ensure tests run against this repo's source tree (src-layout), not an unrelated
# globally installed `ghdcbot` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

