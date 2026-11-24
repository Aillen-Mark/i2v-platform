"""Keep the media binary tied to the lifetime of its worker."""

import ctypes
import os
import signal
import subprocess
import sys


def linux_exec(parent: int, program: str, args: list[str]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        raise SystemExit(125)
    os.execvp(program, [program, *args])


def windows_run(parent: int, program: str, args: list[str]) -> int:
    from ctypes import wintypes

    handle, dword = wintypes.HANDLE, wintypes.DWORD

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong),
            ("job_time", ctypes.c_longlong),
            ("flags", dword),
            ("min_working", ctypes.c_size_t),
            ("max_working", ctypes.c_size_t),
            ("active_processes", dword),
            ("affinity", ctypes.c_size_t),
            ("priority", dword),
            ("scheduling", dword),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io_counters", ctypes.c_ulonglong * 6),
            ("memory_limits", ctypes.c_size_t * 4),
        ]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = (dword, wintypes.BOOL, dword)
    api.OpenProcess.restype = handle
    api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    api.CreateJobObjectW.restype = handle
    api.GetCurrentProcess.restype = handle
    api.SetInformationJobObject.argtypes = (handle, ctypes.c_int, ctypes.c_void_p, dword)
    api.AssignProcessToJobObject.argtypes = (handle, handle)
    api.WaitForSingleObject.argtypes = (handle, dword)
    api.WaitForSingleObject.restype = dword
    api.WaitForMultipleObjects.argtypes = (dword, ctypes.POINTER(handle), wintypes.BOOL, dword)
    api.WaitForMultipleObjects.restype = dword

    parent_handle = api.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE
    job = api.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (
        parent_handle
        and job
        and api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
        and api.AssignProcessToJobObject(job, api.GetCurrentProcess())
    ):
        return 125

    if api.WaitForSingleObject(parent_handle, 0) != 258:  # WAIT_TIMEOUT
        return 125

    # The media process inherits job membership. The guard is its only job-handle owner.
    process = subprocess.Popen([program, *args], stdin=subprocess.DEVNULL)
    child_handle = api.OpenProcess(0x00100000, False, process.pid)
    if not child_handle:
        status = process.poll()
        return status if status is not None else 125
    objects = (handle * 2)(parent_handle, child_handle)
    result = api.WaitForMultipleObjects(2, objects, False, 0xFFFFFFFF)
    return process.wait(timeout=3) if result == 1 else 125


def main():
    parent, program, *args = sys.argv[1:]
    if os.name == "nt":
        raise SystemExit(windows_run(int(parent), program, args))
    linux_exec(int(parent), program, args)


if __name__ == "__main__":
    main()
