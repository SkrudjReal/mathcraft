"""Protected desktop cursor overlay and separately supervised cursor scheme lease."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from ._cursor import VirtualCursor
from ._cursor_scheme import CursorAPI

ALLOWED_EXECUTABLES = frozenset(('pycharm.exe', 'pycharm64.exe', 'code.exe', 'code-insiders.exe'))


def allowed_executable(path):
    return path.replace('\\', '/').rsplit('/', 1)[-1].casefold() in ALLOWED_EXECUTABLES


class CursorGuard:
    def __init__(self):
        self.process = None
        self.messages = queue.Queue()
        self.restored = False
        self.reason = ''
        self.started = False

    def start(self):
        script = Path(__file__).with_name('_cursor_guard.py')
        self.process = subprocess.Popen(
            [sys.executable, str(script), '--parent', str(os.getpid())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW,
        )
        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            except (ValueError, OSError):
                self.messages.put({'state': 'error', 'error': 'Invalid watchdog response'})
        self.reader = threading.Thread(target=read, daemon=True, name='mathcraft-cursor-status')
        self.reader.start()
        try:
            message = self.messages.get(timeout=5)
            if message.get('state') != 'ready':
                raise RuntimeError(message.get('error', 'Cursor watchdog startup failed'))
            self.started = True
        except Exception:
            self.close()
            raise

    def ping(self):
        self.process.stdin.write('PING\n')
        self.process.stdin.flush()

    def status(self):
        try:
            while True:
                message = self.messages.get_nowait()
                if message.get('state') == 'restored':
                    self.restored = True
                    self.reason = message.get('reason', 'restored')
                elif message.get('state') == 'error':
                    self.reason = message.get('error', 'watchdog error')
        except queue.Empty:
            pass
        return self.process.poll()

    def close(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.stdin.write('STOP\n')
                process.stdin.flush()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                # Recover the user's configured scheme before stopping a failed guard.
                CursorAPI().reset_scheme()
                process.terminate()
                process.wait(timeout=2)
        self.reader.join(timeout=1)
        self.status()
        if self.started and process.returncode != 0:
            CursorAPI().reset_scheme()
        process.stdin.close()
        process.stdout.close()
        self.process = None


class DesktopCursor:
    def __init__(self, root, hwnd, protect_window, on_disabled, internal_pointer):
        self.root, self.hwnd = root, hwnd
        self.on_disabled = on_disabled
        self.internal_pointer = internal_pointer
        self.overlay = VirtualCursor(root, protect_window)
        self.guard = CursorGuard()
        self.active = False
        self._after = None
        self._last_ping = 0
        self._cache = {}
        self._saved_cursors = []
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('GetCursorPos', [ctypes.POINTER(w.POINT)], w.BOOL),
            ('WindowFromPoint', [w.POINT], w.HWND),
            ('GetAncestor', [w.HWND, w.UINT], w.HWND),
            ('GetWindowThreadProcessId', [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            ('GetAsyncKeyState', [ctypes.c_int], ctypes.c_short),
        ):
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                           ctypes.POINTER(w.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = w.BOOL

    def _allowed_window(self, hwnd):
        if not hwnd:
            return False
        if self.user.GetAncestor(hwnd, 2) == self.hwnd:
            return True
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        now = time.monotonic()
        cached = self._cache.get(pid.value)
        if cached and now-cached[0] < 1:
            return cached[1]
        allowed = False
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                name, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
                if self.kernel.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(length)):
                    allowed = allowed_executable(name.value)
            finally:
                self.kernel.CloseHandle(process)
        if len(self._cache) >= 128:
            self._cache.clear()
        self._cache[pid.value] = now, allowed
        return allowed

    def start(self):
        try:
            self.guard.start()
            # Tk widgets may use private cursor resources rather than system roles.
            pending = [self.root]
            while pending:
                widget = pending.pop()
                if widget.winfo_toplevel() is not self.root:
                    continue
                pending.extend(widget.winfo_children())
                self._saved_cursors.append((widget, widget.cget('cursor')))
                widget.configure(cursor='none')
            self.active = True
            self._tick()
        except Exception:
            self.close()
            raise

    def _tick(self):
        if not self.active:
            return
        try:
            code = self.guard.status()
            if self.guard.restored or code is not None:
                reason = self.guard.reason or 'Cursor watchdog stopped'
                try:
                    self.on_disabled(reason)
                finally:
                    self.close()
                return
            if all(self.user.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x7B)):
                try:
                    self.on_disabled('Ctrl+Alt+F12')
                finally:
                    self.close()
                return
            now = time.monotonic()
            if now-self._last_ping >= 0.5:
                self.guard.ping()
                self._last_ping = now
            point = w.POINT()
            if (not self.internal_pointer() and self.user.GetCursorPos(ctypes.byref(point))
                    and self._allowed_window(self.user.WindowFromPoint(point))):
                self.overlay.show((point.x, point.y), hide_system_cursor=False)
            else:
                self.overlay.hide()
            self._after = self.root.after(10, self._tick)
        except Exception as exc:
            try:
                self.on_disabled('Desktop cursor disabled: '+str(exc))
            finally:
                self.close()

    def close(self):
        self.active = False
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        try:
            self.guard.close()
        finally:
            for widget, cursor in self._saved_cursors:
                try:
                    widget.configure(cursor=cursor)
                except Exception:
                    pass
            self._saved_cursors.clear()
            try:
                self.overlay.close()
            except Exception:
                pass
