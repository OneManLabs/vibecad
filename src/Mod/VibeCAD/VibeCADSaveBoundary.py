# SPDX-License-Identifier: LGPL-2.1-or-later
"""Thread-local marker for internal CAD copies that are not user Save As actions."""

from __future__ import annotations

from contextlib import contextmanager
import threading


_STATE = threading.local()


def internal_document_save_active() -> bool:
    return int(getattr(_STATE, "depth", 0)) > 0


@contextmanager
def internal_document_save():
    _STATE.depth = int(getattr(_STATE, "depth", 0)) + 1
    try:
        yield
    finally:
        _STATE.depth = max(0, int(getattr(_STATE, "depth", 1)) - 1)
