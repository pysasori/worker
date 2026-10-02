"""Налаштування процесу: OpenCV в один потік, пріоритет нижче звичайного."""
from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="пріоритет процесу — лише Windows")
def test_tune_process_lowers_priority_and_limits_opencv():
    import ctypes
    from ctypes import wintypes

    import cv2

    from app.core.perf import BELOW_NORMAL_PRIORITY_CLASS, tune_process

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    before_threads = cv2.getNumThreads()
    before_class = kernel32.GetPriorityClass(kernel32.GetCurrentProcess())
    try:
        tune_process(low_priority=True, cv_threads=1)
        assert cv2.getNumThreads() == 1
        assert kernel32.GetPriorityClass(kernel32.GetCurrentProcess()) == BELOW_NORMAL_PRIORITY_CLASS
    finally:
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), before_class or 0x20)
        cv2.setNumThreads(before_threads)
