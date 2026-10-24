"""Temporary system cursor replacement, with exact in-memory snapshots."""
import ctypes
from ctypes import wintypes as w

CURSOR_IDS = (32512, 32513, 32514, 32515, 32516, 32642, 32643,
              32644, 32645, 32646, 32648, 32649, 32650)


class CursorAPI:
    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        specs = {
            'LoadCursorW': ([w.HINSTANCE, ctypes.c_void_p], w.HANDLE),
            'CopyImage': ([w.HANDLE, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT], w.HANDLE),
            'CreateCursor': ([w.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p], w.HANDLE),
            'SetSystemCursor': ([w.HANDLE, w.DWORD], w.BOOL),
            'DestroyCursor': ([w.HANDLE], w.BOOL),
            'GetSystemMetrics': ([ctypes.c_int], ctypes.c_int),
            'SystemParametersInfoW': ([w.UINT, w.UINT, ctypes.c_void_p, w.UINT], w.BOOL),
        }
        for name, (args, result) in specs.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result

    def snapshot(self, cursor_id):
        original = self.user.LoadCursorW(None, cursor_id)
        if not original:
            raise ctypes.WinError(ctypes.get_last_error())
        saved = self.user.CopyImage(original, 2, 0, 0, 0)
        if not saved:
            raise ctypes.WinError(ctypes.get_last_error())
        return saved

    def transparent(self):
        width = self.user.GetSystemMetrics(13)
        height = self.user.GetSystemMetrics(14)
        if not (0 < width <= 1024 and 0 < height <= 1024):
            raise RuntimeError('Invalid system cursor dimensions')
        size = ((width+15)//16)*2*height
        and_mask = (ctypes.c_ubyte * size)(*([255]*size))
        xor_mask = (ctypes.c_ubyte * size)()
        cursor = self.user.CreateCursor(None, 0, 0, width, height, and_mask, xor_mask)
        if not cursor:
            raise ctypes.WinError(ctypes.get_last_error())
        return cursor

    def replace(self, cursor, cursor_id):
        if not self.user.SetSystemCursor(cursor, cursor_id):
            # Ownership transfers only on successful replacement.
            self.user.DestroyCursor(cursor)
            raise ctypes.WinError(ctypes.get_last_error())

    def destroy(self, cursor):
        self.user.DestroyCursor(cursor)

    def reset_scheme(self):
        if not self.user.SystemParametersInfoW(0x0057, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())


class CursorScheme:
    def __init__(self, api=None):
        self.api = api if api is not None else CursorAPI()
        self.saved = {}
        self.applied = set()

    def hide(self):
        if self.saved:
            raise RuntimeError('Cursor scheme lease already active')
        try:
            # Snapshot every role before changing any role.
            for cursor_id in CURSOR_IDS:
                self.saved[cursor_id] = self.api.snapshot(cursor_id)
            for cursor_id in CURSOR_IDS:
                self.api.replace(self.api.transparent(), cursor_id)
                self.applied.add(cursor_id)
        except Exception:
            self.restore()
            raise

    def restore(self):
        errors = []
        for cursor_id, cursor in list(self.saved.items()):
            try:
                if cursor_id in self.applied:
                    self.api.replace(cursor, cursor_id)
                else:
                    self.api.destroy(cursor)
            except Exception as exc:
                errors.append(exc)
        self.saved.clear()
        self.applied.clear()
        if errors:
            self.api.reset_scheme()
