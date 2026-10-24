"""Opt-in, foreground-only hook experiment, not an input security boundary."""
import ctypes
from ctypes import wintypes as w
import queue
import threading
import time

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)


class KeyboardData(ctypes.Structure):
    _fields_ = [('vk', w.DWORD), ('scan', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', ctypes.c_size_t)]


class MouseData(ctypes.Structure):
    _fields_ = [('point', w.POINT), ('data', w.DWORD), ('flags', w.DWORD),
                ('time', w.DWORD), ('extra', ctypes.c_size_t)]


class ForegroundInput:
    """Suppress later LL hooks for supported keys/buttons in our client area.

    Modifiers, system shortcuts and unsupported input pass through.
    Raw Input and kernel observers are outside this experiment's scope.
    """
    def __init__(self, hwnd, virtual_mouse=False):
        self.hwnd = hwnd
        self.virtual_mouse = virtual_mouse
        self.cursor_hwnd = None
        self._anchor = None
        self._position = None
        self.events = queue.Queue(maxsize=512)
        self.enabled = True
        self.heartbeat = time.monotonic()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self._keys = set()
        self._buttons = set()
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        specs = {
            'SetWindowsHookExW': ([ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD], w.HANDLE),
            'UnhookWindowsHookEx': ([w.HANDLE], w.BOOL),
            'CallNextHookEx': ([w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM], LRESULT),
            'SetThreadDpiAwarenessContext': ([w.HANDLE], w.HANDLE),
            'GetForegroundWindow': ([], w.HWND),
            'GetAsyncKeyState': ([ctypes.c_int], ctypes.c_short),
            'GetKeyState': ([ctypes.c_int], ctypes.c_short),
            'GetWindowThreadProcessId': ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            'GetKeyboardLayout': ([w.DWORD], w.HANDLE),
            'ToUnicodeEx': ([w.UINT, w.UINT, ctypes.POINTER(w.BYTE), w.LPWSTR,
                             ctypes.c_int, w.UINT, w.HANDLE], ctypes.c_int),
            'ScreenToClient': ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            'ClientToScreen': ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            'GetCursorPos': ([ctypes.POINTER(w.POINT)], w.BOOL),
            'SetCursorPos': ([ctypes.c_int, ctypes.c_int], w.BOOL),
            'GetClientRect': ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            'WindowFromPoint': ([w.POINT], w.HWND),
            'GetAncestor': ([w.HWND, w.UINT], w.HWND),
            'PeekMessageW': ([ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT], w.BOOL),
            'TranslateMessage': ([ctypes.POINTER(w.MSG)], w.BOOL),
            'DispatchMessageW': ([ctypes.POINTER(w.MSG)], LRESULT),
        }
        for name, (args, result) in specs.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = w.HMODULE
        self._keyboard_callback = HOOKPROC(self._keyboard)
        self._mouse_callback = HOOKPROC(self._mouse)
        self._thread = threading.Thread(target=self._run, name='mathcraft-input', daemon=True)

    def start(self):
        self._thread.start()
        if not self._ready.wait(3):
            self.close()
            raise RuntimeError('Input hook startup timed out')
        if self._error:
            raise self._error

    def close(self):
        self.enabled = False
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise RuntimeError('Input hook worker did not stop')

    def disable(self, reason):
        self.enabled = False
        self.reason = reason

    def _active(self):
        return (self.enabled and time.monotonic()-self.heartbeat < 0.5
                and self.user.GetForegroundWindow() == self.hwnd)

    def _put(self, event):
        try:
            self.events.put_nowait(event)
            return True
        except queue.Full:
            self.disable('Input queue overflow; interception disabled')
            return False

    def _next(self, code, message, address):
        return self.user.CallNextHookEx(None, code, message, address)

    def _down(self, key):
        return bool(self.user.GetAsyncKeyState(key) & 0x8000)

    def _keyboard(self, code, message, address):
        try:
            if code < 0:
                return self._next(code, message, address)
            data = ctypes.cast(address, ctypes.POINTER(KeyboardData)).contents
            key = data.vk
            down = message in (0x100, 0x104)
            if down and key == 0x7B and self._down(0x11) and self._down(0x12):
                self.disable('Ctrl+Alt+F12: interception disabled')
                return self._next(code, message, address)
            if not down and key in self._keys:
                self._keys.discard(key)
                return 1
            if down and key in self._keys and not self._active():
                self._keys.discard(key)
            # Keep OS modifier state and shell shortcuts working normally.
            supported = (key in (8, 9, 13, 27, 32, 33, 34, 35, 36, 37, 38, 39, 40, 45, 46)
                         or 0x30 <= key <= 0x5A or 0x60 <= key <= 0x6F
                         or 0xBA <= key <= 0xE2)
            if (not down or not supported or data.flags & 0x10 or not self._active()
                    or self._down(0x12) or self._down(0x5B) or self._down(0x5C)):
                return self._next(code, message, address)
            shift, control = self._down(0x10), self._down(0x11)
            if control and key not in (65, 67, 86, 88, 89, 90):
                # Let Tk handle shortcuts we do not implement rather than drop them.
                return self._next(code, message, address)
            state = (w.BYTE * 256)()
            state[key] = 0x80
            state[0x10] = 0x80 if shift else 0
            state[0x11] = 0x80 if control else 0
            state[0x14] = self.user.GetKeyState(0x14) & 1
            thread = self.user.GetWindowThreadProcessId(self.hwnd, None)
            layout = self.user.GetKeyboardLayout(thread)
            buffer = ctypes.create_unicode_buffer(16)
            count = self.user.ToUnicodeEx(key, data.scan, state, buffer, 16, 4, layout)
            if count < 0:
                self.disable('Dead-key layout: interception disabled')
                return self._next(code, message, address)
            text = buffer[:count] if count > 0 else ''
            if self._put(('key', key, text, shift, control)):
                self._keys.add(key)
                return 1
        except Exception:
            self.disable('Keyboard hook error; interception disabled')
        return self._next(code, message, address)

    @property
    def pointer_position(self):
        return self._position if self._anchor is not None else None

    def _client_bounds(self):
        origin, rect = w.POINT(), w.RECT()
        if (not self.user.ClientToScreen(self.hwnd, ctypes.byref(origin))
                or not self.user.GetClientRect(self.hwnd, ctypes.byref(rect))):
            return None
        return origin.x, origin.y, origin.x+rect.right, origin.y+rect.bottom

    @staticmethod
    def _inside(bounds, x, y):
        return bool(bounds and bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3])

    def _release_pointer(self, restore=True):
        anchor, position = self._anchor, self._position
        self._anchor = None
        self._position = None
        if anchor is not None and restore:
            actual = w.POINT()
            # Do not overwrite a cursor relocation made by another application.
            if (self.user.GetCursorPos(ctypes.byref(actual))
                    and (actual.x, actual.y) == anchor):
                if not self.user.SetCursorPos(*position):
                    self.disable('Cursor restoration failed; interception disabled')

    def _sync_pointer(self):
        if self._anchor is not None and not self._active():
            self._release_pointer()

    def _virtual_move(self, x, y):
        bounds = self._client_bounds()
        if self._anchor is None:
            actual = w.POINT()
            if (not self.user.GetCursorPos(ctypes.byref(actual))
                    or not self._inside(bounds, actual.x, actual.y)):
                return False
            hit = self.user.WindowFromPoint(actual)
            if self.user.GetAncestor(hit, 2) not in (self.hwnd, self.cursor_hwnd):
                return False
            self._anchor = (actual.x, actual.y)
            self._position = self._anchor
        ax, ay = self._anchor
        px, py = self._position
        self._position = (px+x-ax, py+y-ay)
        if not self._inside(bounds, *self._position):
            self._release_pointer()
            return True
        if self._buttons:
            if not self._put(('motion', tuple(self._buttons), *self._position)):
                self._release_pointer()
        return True

    def _mouse(self, code, message, address):
        # Windows can invoke hooks under a different DPI context than our pump.
        change_dpi = getattr(self.user, 'SetThreadDpiAwarenessContext', None)
        previous = change_dpi(ctypes.c_void_p(-4)) if change_dpi else None
        if change_dpi and not previous:
            self.disable('DPI context unavailable; interception disabled')
            return self._next(code, message, address)
        try:
            return self._mouse_event(code, message, address)
        finally:
            if previous:
                change_dpi(previous)

    def _mouse_event(self, code, message, address):
        try:
            if code < 0:
                return self._next(code, message, address)
            data = ctypes.cast(address, ctypes.POINTER(MouseData)).contents
            virtual = getattr(self, 'virtual_mouse', False)
            if data.flags & 1:
                if virtual and message == 0x200:
                    self._release_pointer(restore=False)
                return self._next(code, message, address)
            pairs = {0x201: (1, True), 0x202: (1, False),
                     0x204: (3, True), 0x205: (3, False),
                     0x207: (2, True), 0x208: (2, False)}
            if not self._active():
                if virtual:
                    self._release_pointer()
                # Pair only releases for button downs we previously consumed.
                if message in pairs:
                    button, down = pairs[message]
                    if not down and button in self._buttons:
                        self._buttons.discard(button)
                        return 1
                return self._next(code, message, address)
            x, y = data.point.x, data.point.y
            if virtual and message == 0x200:
                if self._virtual_move(x, y):
                    return 1
                return self._next(code, message, address)
            if virtual and self.pointer_position is not None:
                x, y = self.pointer_position
            if message in pairs:
                button, down = pairs[message]
                if not down and button in self._buttons:
                    self._buttons.discard(button)
                    self._put(('mouse', button, False, x, y, 0))
                    return 1
            hit = self.user.WindowFromPoint(w.POINT(x, y))
            point, rect = w.POINT(x, y), w.RECT()
            if (self.user.GetAncestor(hit, 2) not in (self.hwnd, getattr(self, 'cursor_hwnd', None))
                    or not self.user.ScreenToClient(self.hwnd, ctypes.byref(point))
                    or not self.user.GetClientRect(self.hwnd, ctypes.byref(rect))
                    or not (0 <= point.x < rect.right and 0 <= point.y < rect.bottom)):
                if virtual:
                    self._release_pointer()
                return self._next(code, message, address)
            if message in pairs:
                button, down = pairs[message]
                if self._put(('mouse', button, down, x, y, 0)):
                    if down:
                        self._buttons.add(button)
                    return 1
            elif message == 0x200 and self._buttons:
                self._put(('motion', tuple(self._buttons), x, y))
            elif message == 0x20A:
                delta = ctypes.c_short(data.data >> 16).value
                if self._put(('mouse', 0, False, x, y, delta)):
                    return 1
        except Exception:
            self.disable('Mouse hook error; interception disabled')
            if getattr(self, 'virtual_mouse', False):
                self._release_pointer()
        return self._next(code, message, address)

    def _run(self):
        handles = []
        previous_dpi = None
        try:
            previous_dpi = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            if not previous_dpi:
                raise ctypes.WinError(ctypes.get_last_error())
            module = self.kernel.GetModuleHandleW(None)
            for kind, callback in ((13, self._keyboard_callback), (14, self._mouse_callback)):
                handle = self.user.SetWindowsHookExW(kind, callback, module, 0)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
            self._ready.set()
            message = w.MSG()
            while not self._stop.is_set():
                self._sync_pointer()
                while self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == 0x12:
                        return
                    self.user.TranslateMessage(ctypes.byref(message))
                    self.user.DispatchMessageW(ctypes.byref(message))
                self._stop.wait(0.005)
        except Exception as exc:
            self._error = exc
            self.disable('Input hook worker stopped')
        finally:
            self.enabled = False
            for handle in reversed(handles):
                self.user.UnhookWindowsHookEx(handle)
            try:
                self._release_pointer()
            finally:
                if previous_dpi:
                    self.user.SetThreadDpiAwarenessContext(previous_dpi)
                self._ready.set()
