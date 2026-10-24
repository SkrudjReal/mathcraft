"""Small noninteractive cursor overlay owned by the terminal's GUI thread."""
import ctypes
from ctypes import wintypes as w
import tkinter as tk


class VirtualCursor:
    def __init__(self, root, protect_window):
        self.root = root
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg='#ff00ff')
        self.window.geometry('22x28+0+0')
        self.window.attributes('-disabled', True)
        self.canvas = tk.Canvas(self.window, width=22, height=28, bg='#ff00ff',
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack()
        self.canvas.create_polygon(2, 2, 2, 22, 7, 17, 11, 26, 15, 24,
                                   11, 16, 19, 16, fill='white', outline='black', width=2)
        try:
            self.hwnd = protect_window(self.window)
            self.window.attributes('-transparentcolor', '#ff00ff')
            self.window.attributes('-topmost', True)
            user = ctypes.WinDLL('user32', use_last_error=True)
            get_style = getattr(user, 'GetWindowLongPtrW', user.GetWindowLongW)
            set_style = getattr(user, 'SetWindowLongPtrW', user.SetWindowLongW)
            get_style.argtypes, get_style.restype = [w.HWND, ctypes.c_int], ctypes.c_ssize_t
            set_style.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            style = get_style(self.hwnd, -20)
            ctypes.set_last_error(0)
            if not set_style(self.hwnd, -20, style | 0x08000000 | 0x20 | 0x80):
                if ctypes.get_last_error():
                    raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.window.destroy()
            raise
        self.position = None
        self._saved_cursors = []

    def _hide_system_cursor(self):
        if self._saved_cursors:
            return
        pending = [self.root]
        while pending:
            widget = pending.pop()
            if widget.winfo_toplevel() is not self.root:
                continue
            pending.extend(widget.winfo_children())
            try:
                self._saved_cursors.append((widget, widget.cget('cursor')))
                widget.configure(cursor='none')
            except tk.TclError:
                pass

    def show(self, position, hide_system_cursor=True):
        if hide_system_cursor:
            self._hide_system_cursor()
        if self.position != position:
            x, y = position
            # +negative coordinates retain screen coordinates on left-hand monitors.
            self.window.geometry('22x28+%d+%d' % (x, y))
            self.position = position
        self.window.deiconify()

    def hide(self):
        self.window.withdraw()
        self.position = None
        for widget, cursor in self._saved_cursors:
            try:
                widget.configure(cursor=cursor)
            except tk.TclError:
                pass
        self._saved_cursors.clear()

    def close(self):
        self.hide()
        self.window.destroy()
