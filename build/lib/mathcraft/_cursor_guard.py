"""Standalone watchdog: restores system cursors on parent death or lost heartbeat."""
import argparse
import ctypes
from ctypes import wintypes as w
import json
import sys
import threading
import time

if __package__:
    from ._cursor_scheme import CursorScheme, CursorAPI
else:
    from _cursor_scheme import CursorScheme, CursorAPI


def run(parent_pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    parent = kernel.OpenProcess(0x00100000, False, parent_pid)
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    mutex = None
    scheme = None
    try:
        ctypes.set_last_error(0)
        mutex = kernel.CreateMutexW(None, False, 'Local\\MathcraftCursorSchemeLease')
        if not mutex or ctypes.get_last_error() == 183:
            raise RuntimeError('Another mathcraft desktop cursor session is already running')
        scheme = CursorScheme()
        scheme.hide()
        stopped = threading.Event()
        heartbeat = [time.monotonic()]
        def commands():
            try:
                for line in sys.stdin:
                    command = line.strip()
                    if command == 'STOP':
                        break
                    if command == 'PING':
                        heartbeat[0] = time.monotonic()
            finally:
                stopped.set()
        threading.Thread(target=commands, daemon=True).start()
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user.GetAsyncKeyState.restype = ctypes.c_short
        print(json.dumps({'state': 'ready'}), flush=True)
        reason = 'stop'
        while not stopped.is_set():
            wait = kernel.WaitForSingleObject(parent, 20)
            if wait == 0:
                reason = 'parent exited'
                break
            if wait != 258:
                reason = 'parent monitoring failed'
                break
            if time.monotonic()-heartbeat[0] > 3:
                reason = 'GUI heartbeat expired'
                break
            if all(user.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x7B)):
                reason = 'Ctrl+Alt+F12'
                break
        scheme.restore()
        print(json.dumps({'state': 'restored', 'reason': reason}), flush=True)
    finally:
        try:
            if scheme:
                scheme.restore()
        finally:
            if mutex:
                kernel.CloseHandle(mutex)
            kernel.CloseHandle(parent)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=int)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.restore:
        CursorAPI().reset_scheme()
        return
    if args.parent is None or args.parent <= 0:
        parser.error('--parent is required')
    try:
        run(args.parent)
    except Exception as exc:
        print(json.dumps({'state': 'error', 'error': str(exc)}), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
