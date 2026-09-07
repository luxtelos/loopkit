#!/usr/bin/env python3
"""loop_next_pick.py — thin shim. The implementation lives in `loopkit_core.loop_next_pick`.

M2 part A moved this module into `plugins/loopkit/loopkit_core/`, the portable
package that knows nothing about Claude Code. The script path stays because
hooks, `loop-next.sh`, `tests/selftest.sh` and other projects call it by path,
and a move that breaks its callers is a rewrite wearing a refactor's name.

This file re-exports the package module's ENTIRE namespace rather than a
hand-picked list. A hand-picked list is a second place to forget a symbol:
`loop_next_pick` is loaded here by `importlib.util.spec_from_file_location` from
several callers, under several module names, and each of them reaches for a
different set of names. Copying the namespace makes the shim's surface equal
to the package module's by construction, which is what
`tests/pins/core-shim-parity.py` asserts — same objects, not merely same
behaviour.
"""
from __future__ import annotations

import os
import sys

# `plugins/loopkit`, the directory that CONTAINS the package.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from loopkit_core import loop_next_pick as _impl  # noqa: E402

# Everything except this module's own identity. Rebinding `__name__`,
# `__file__` or `__spec__` would make the shim claim to BE the package module,
# which breaks `importlib` callers that registered it under another name.
_OWN = {"__name__", "__file__", "__loader__", "__spec__", "__package__",
        "__builtins__", "__path__", "__cached__"}
globals().update({k: v for k, v in vars(_impl).items() if k not in _OWN})

if __name__ == "__main__":
    sys.exit(_impl.main())
