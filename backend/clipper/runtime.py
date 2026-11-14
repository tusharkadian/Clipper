from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import time


def unbiased_seconds() -> float | None:
    if os.name != "nt":
        return None
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel.QueryUnbiasedInterruptTime
        query.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        query.restype = ctypes.c_int
        ticks = ctypes.c_ulonglong()
        return ticks.value / 10_000_000 if query(ctypes.byref(ticks)) else None
    except (OSError, AttributeError):
        return None


class RuntimeMonitor:
    def __init__(self):
        self.started = time.perf_counter()
        self.unbiased_started = unbiased_seconds()

    def snapshot(self) -> dict:
        elapsed = max(0, time.perf_counter() - self.started)
        unbiased = unbiased_seconds()
        measured = self.unbiased_started is not None and unbiased is not None and unbiased >= self.unbiased_started
        active = unbiased - self.unbiased_started if measured else None
        gap = max(0, elapsed - active) if measured else None
        return {"version": 1, "elapsed_seconds": round(elapsed, 3),
                "unbiased_elapsed_seconds": round(active, 3) if active is not None else None,
                "possible_suspend_seconds": round(gap, 3) if gap is not None else None,
                "standby_check": "windows-unbiased-clock" if measured else "unavailable",
                "limitations": "Best-effort clock-gap signal; some Modern Standby states are not observable and no CPU-time inference is made.",
                "standby_suspected": gap > 5 if gap is not None else None}


def set_execution_state(flags: int) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.SetThreadExecutionState
    function.argtypes = [ctypes.c_uint32]
    function.restype = ctypes.c_uint32
    return function(flags)


@contextmanager
def inhibit_idle_sleep(requested: bool):
    state = {"requested": requested, "enabled": False, "release_on_exit": False, "error": None}
    previous = 0
    if requested:
        try:
            if os.name != "nt":
                raise OSError("Job-scoped keep-awake is only supported on Windows.")
            previous = set_execution_state(0x80000001)
            if not previous:
                raise OSError("Windows declined the job-scoped keep-awake request.")
            state.update({"enabled": True, "release_on_exit": True})
        except (OSError, AttributeError) as error:
            state["error"] = str(error)
    try:
        yield state
    finally:
        if previous:
            set_execution_state(previous)