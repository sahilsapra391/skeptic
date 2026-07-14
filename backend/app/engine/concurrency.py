"""The engine's OOM-serialization contract, in one place.

Engine + full gauntlet run in-process as background tasks (single web
worker). The gauntlet's sensitivity sweep re-runs the whole engine ~20×;
two overlapping full-history runs' transient PEAKS are what tip a small
container OOM (measured: no per-run reference leak — the Python heap is
flat run-over-run — so the risk is concurrency, not accumulation).
ENGINE_LOCK serializes them: a second submission stays honestly 'queued'
until the first finishes.

EVERY code path that loads a market store and runs the engine — normal
runs, fill audits, notebook reproduces — must hold this ONE lock. It
lives here, in a neutral module, so no consumer has to reach into
another route module for it (2026-07-14 consolidation: notebook.py used
to import the lock from runs.py, where a name-stable rebind would have
silently broken reproduce's serialization).
"""

from __future__ import annotations

import ctypes
import gc
import threading

ENGINE_LOCK = threading.Lock()


def release_memory() -> None:
    """Hand the sweep's freed transient buffers back to the OS. glibc keeps
    freed pandas/numpy chunks in per-thread arenas, so container RSS ratchets
    even though nothing leaks; gc + malloc_trim return them. A no-op where
    malloc_trim is unavailable (macOS / musl)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
