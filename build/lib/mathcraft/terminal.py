"""Run commands in PowerShell from a Python-owned, capture-excluded Tk window.

The standard powershell.exe console window is NOT protected by this module.
Only the top-level Tk window created by this process is protected.
"""

import base64
import ctypes
from ctypes import wintypes
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid

WDA_EXCLUDEFROMCAPTURE = 0x00000011
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x2


def _protect_window(root):
    """Apply capture exclusion to our own top-level window, or fail loudly."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetWindowDisplayAffinity.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetLayeredWindowAttributes.argtypes = (
        wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD
    )
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

    # GetWindowDisplayAffinity requires a layered window.
    # Get/SetWindowLongPtr are the correct APIs for both 32- and 64-bit Windows.
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.argtypes = (wintypes.HWND, ctypes.c_int)
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    set_style.restype = ctypes.c_ssize_t

    root.update_idletasks()
    hwnd = user32.GetAncestor(root.winfo_id(), GA_ROOT)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    old_style = get_style(hwnd, GWL_EXSTYLE)
    if not old_style & WS_EX_LAYERED:
        ctypes.set_last_error(0)
        if not set_style(hwnd, GWL_EXSTYLE, old_style | WS_EX_LAYERED) and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
    if not user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        raise ctypes.WinError(ctypes.get_last_error())

    affinity = wintypes.DWORD(0)
    if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
        raise ctypes.WinError(ctypes.get_last_error())
    if affinity.value != WDA_EXCLUDEFROMCAPTURE:
        raise RuntimeError("Windows did not enable WDA_EXCLUDEFROMCAPTURE")
    return hwnd


class _Terminal:
    def __init__(self, title, experimental_input=False, virtual_mouse=False, desktop_cursor=False):
        self.root = tk.Tk()
        self.root.withdraw()  # Configure exclusion before making the window visible.
        self.root.title(title)
        self.root.geometry("820x520")
        self.root.minsize(440, 260)
        self.root.configure(bg="#171717")
        self.events = queue.Queue()
        self.pending_marker = None
        self.closed = False
        self.input_hook = None
        self.virtual_cursor = None
        self.desktop_cursor = None
        self._destroyed = False
        self._mouse_targets = {}
        self._hover_widget = None

        try:
            self.hwnd = _protect_window(self.root)
        except Exception:
            self.root.destroy()
            raise

        self.output = tk.Text(
            self.root, wrap="word", height=1, width=1, bg="#171717", fg="#dedede",
            insertbackground="#dedede", font=("Consolas", 11),
            relief="flat", padx=12, pady=12, state="disabled",
        )
        self.output.pack(fill="both", expand=True)
        self.output.tag_configure("prompt", foreground="#7cc7b4")
        self.output.tag_configure("info", foreground="#b2b2b2")

        bar = tk.Frame(self.root, bg="#222222", padx=10, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text="PS>", bg="#222222", fg="#7cc7b4",
                 font=("Consolas", 11)).pack(side="left")
        self.command = tk.Entry(
            bar, bg="#222222", fg="#ffffff", insertbackground="#ffffff",
            font=("Consolas", 11), relief="flat", borderwidth=0,
        )
        self.command.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.command.bind("<Return>", self._submit)
        for widget in (self.output, self.command):
            widget.configure(exportselection=False)
            # Class bindings update selections after instance event handlers.
            for sequence in ("<ButtonRelease-1>", "<KeyRelease>", "<<Selection>>"):
                widget.bind(sequence, self._selection_changed, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.input_status = tk.Label(self.root, text="Input interception: OFF",
                                     bg="#222222", fg="#dedede")
        self.input_status.pack(fill="x")
        self.disable_button = tk.Button(self.root,
            text="Restore cursor / disable input (Ctrl+Alt+F12)", command=self._disable_input)
        self.disable_button.pack(fill="x")
        self.root.bind("<Control-Alt-F12>", self._disable_input, add="+")

        try:
            self.process = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            self.root.destroy()
            raise

        threading.Thread(target=self._reader, daemon=True).start()
        self._append("PowerShell запущен. Команды выполняются в одной сессии.\n", "info")
        self._append("Интерактивный ввод внутри команд (например, Read-Host) не поддерживается.\n\n", "info")
        self._send_raw("$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)")
        if experimental_input or virtual_mouse:
            try:
                from ._input import ForegroundInput
                if virtual_mouse:
                    from ._cursor import VirtualCursor
                    self.virtual_cursor = VirtualCursor(self.root, _protect_window)
                self.input_hook = ForegroundInput(self.hwnd, virtual_mouse=virtual_mouse)
                if self.virtual_cursor:
                    self.input_hook.cursor_hwnd = self.virtual_cursor.hwnd
                self.input_hook.start()
                self.input_status.configure(text="Experimental input: ON%s — Ctrl+Alt+F12 disables"
                    % (" + virtual mouse" if virtual_mouse else ""))
                self.root.after(10, self._drain_input)
            except Exception:
                self._close()
                raise
        if desktop_cursor:
            try:
                from ._desktop_cursor import DesktopCursor
                self.desktop_cursor = DesktopCursor(self.root, self.hwnd, _protect_window,
                    self._desktop_disabled,
                    lambda: bool(self.virtual_cursor and self.virtual_cursor.position is not None))
                self.desktop_cursor.start()
                if not self.desktop_cursor.active:
                    raise RuntimeError('Desktop cursor mode could not be started')
                self.input_status.configure(text="Desktop cursor: ON — Ctrl+Alt+F12 restores")
            except Exception:
                self._close()
                raise
        self.root.after(60, self._drain_events)
        self.root.deiconify()
        self.command.focus_set()

    def _desktop_disabled(self, reason):
        self._disable_input()
        if not self._destroyed:
            self.input_status.configure(text=reason)

    def _close_desktop_cursor(self):
        controller, self.desktop_cursor = self.desktop_cursor, None
        if controller:
            controller.close()

    def _disable_input(self, _event=None):
        hook, self.input_hook = self.input_hook, None
        if hook:
            hook.disable("Interception disabled")
            hook.close()
        self._close_virtual_cursor()
        self._close_desktop_cursor()
        try:
            self.input_status.configure(text="Cursor restored / input interception: OFF")
        except tk.TclError:
            pass
        return "break"

    def _drain_input(self):
        hook = self.input_hook
        if self._destroyed or hook is None:
            return
        hook.heartbeat = time.monotonic()
        try:
            for _ in range(128):
                event = hook.events.get_nowait()
                # Never replay stale text into a window that lost foreground.
                if hook.user.GetForegroundWindow() == self.hwnd:
                    self._replay_input(event)
        except queue.Empty:
            pass
        except tk.TclError:
            hook.disable("Widget processing failed; interception disabled")
        try:
            self._sync_virtual_cursor()
        except tk.TclError:
            hook.disable("Cursor rendering failed; interception disabled")
        if not hook.enabled:
            hook.close()
            self._close_virtual_cursor()
            self.input_status.configure(text=getattr(hook, 'reason', 'Input interception: OFF'))
            self.input_hook = None
            return
        self.root.after(10, self._drain_input)

    def _sync_virtual_cursor(self):
        if self.virtual_cursor is None:
            return
        hook = self.input_hook
        position = (hook.pointer_position if hook and hook.enabled
                    and hook.user.GetForegroundWindow() == self.hwnd else None)
        if position is None:
            self.virtual_cursor.hide()
            if self._hover_widget:
                self._hover_widget.event_generate('<Leave>')
                self._hover_widget = None
            for widget in self._mouse_targets.values():
                if widget:
                    widget.event_generate('<Leave>')
            self._mouse_targets.clear()
            grabbed = self.root.grab_current()
            if grabbed and grabbed.winfo_toplevel() is self.root:
                grabbed.grab_release()
        else:
            changed = self.virtual_cursor.position != position
            self.virtual_cursor.show(position)
            if changed:
                widget = self.root.winfo_containing(*position)
                if widget is not self._hover_widget:
                    if self._hover_widget:
                        self._hover_widget.event_generate('<Leave>')
                    if widget:
                        widget.event_generate('<Enter>')
                    self._hover_widget = widget
                if widget:
                    widget.event_generate('<Motion>', x=position[0]-widget.winfo_rootx(),
                                          y=position[1]-widget.winfo_rooty())

    def _close_virtual_cursor(self):
        if self.virtual_cursor:
            try:
                self.virtual_cursor.close()
            except tk.TclError:
                pass
            finally:
                self.virtual_cursor = None
        self._mouse_targets.clear()
        self._hover_widget = None

    def _replay_input(self, event):
        if event[0] == 'key':
            _, key, text, shift, control = event
            widget = self.root.focus_get()
            if widget not in (self.command, self.output):
                return
            if control:
                actions = {67: '<<Copy>>', 86: '<<Paste>>', 88: '<<Cut>>',
                           90: '<<Undo>>', 89: '<<Redo>>'}
                if key == 65:
                    if widget is self.command:
                        widget.selection_range(0, 'end')
                    else:
                        widget.tag_add('sel', '1.0', 'end-1c')
                elif key in actions:
                    widget.event_generate(actions[key])
                else:
                    return
            else:
                names = {8: 'BackSpace', 9: 'Tab', 13: 'Return', 27: 'Escape',
                         33: 'Prior', 34: 'Next', 35: 'End', 36: 'Home',
                         37: 'Left', 38: 'Up', 39: 'Right', 40: 'Down',
                         45: 'Insert', 46: 'Delete'}
                if key in names:
                    widget.event_generate('<KeyPress>', keysym=names[key], state=1 if shift else 0)
                    widget.event_generate('<KeyRelease>', keysym=names[key], state=1 if shift else 0)
                elif text and widget is self.command and str(widget.cget('state')) == 'normal':
                    if widget.selection_present():
                        widget.delete('sel.first', 'sel.last')
                    widget.insert('insert', text)
            self.root.after_idle(self._copy_selection, widget)
            return
        if event[0] == 'motion':
            _, buttons, x, y = event
            for button in buttons:
                widget = self._mouse_targets.get(button)
                if widget:
                    widget.event_generate('<B%d-Motion>' % button,
                        x=x-widget.winfo_rootx(), y=y-widget.winfo_rooty(),
                        state={1: 256, 2: 512, 3: 1024}[button])
            return
        _, button, down, x, y, delta = event
        widget = self.root.winfo_containing(x, y)
        if down:
            self._mouse_targets[button] = widget
            if widget in (self.command, self.output):
                widget.focus_set()
        elif button:
            widget = self._mouse_targets.pop(button, widget)
        if widget is None:
            return
        position = dict(x=x-widget.winfo_rootx(), y=y-widget.winfo_rooty())
        if delta:
            widget.event_generate('<MouseWheel>', delta=delta, **position)
        elif button:
            widget.event_generate('<ButtonPress-%d>' % button if down else
                                  '<ButtonRelease-%d>' % button, **position)

    def _selection_changed(self, event):
        self.root.after_idle(self._copy_selection, event.widget)

    def _copy_selection(self, widget):
        """Copy only this widget's selection, leaving empty selections alone."""
        try:
            if isinstance(widget, tk.Text):
                if not widget.tag_ranges("sel"):
                    return
                selected = widget.get("sel.first", "sel.last")
            else:
                if not widget.selection_present():
                    return
                selected = widget.get()[widget.index("sel.first"):widget.index("sel.last")]
            if selected:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected)
        except tk.TclError:
            # Windows may temporarily lock the clipboard, or a widget may close.
            return

    def _append(self, value, tag=None):
        self.output.configure(state="normal")
        self.output.insert("end", value, tag)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _send_raw(self, statement):
        if self.process.poll() is not None:
            raise RuntimeError("PowerShell process already exited")
        self.process.stdin.write(statement + "\n")
        self.process.stdin.flush()

    def _submit(self, _event=None):
        if self.pending_marker is not None or self.closed:
            return "break"
        cmd = self.command.get().strip()
        self.command.delete(0, "end")
        if not cmd:
            return "break"

        self._append("PS> " + cmd + "\n", "prompt")
        marker = "__PROTECTED_PS_END_" + uuid.uuid4().hex + "__"
        encoded = base64.b64encode(cmd.encode("utf-16le")).decode("ascii")
        # Dot-source the script block so $variables and Set-Location persist.
        statement = (
            "try { . ([scriptblock]::Create([Text.Encoding]::Unicode.GetString("
            "[Convert]::FromBase64String('" + encoded + "')))) } "
            "catch { Write-Error $_ }; "
            "[Console]::Out.WriteLine(); "
            "[Console]::Out.WriteLine('" + marker + "')"
        )
        self.pending_marker = marker
        self.command.configure(state="disabled")
        try:
            self._send_raw(statement)
        except Exception as exc:
            self.pending_marker = None
            self._append("Ошибка отправки команды: " + str(exc) + "\n")
            self.command.configure(state="normal")
        return "break"

    def _reader(self):
        try:
            for line in self.process.stdout:
                self.events.put(("line", line))
        except (OSError, ValueError) as exc:
            self.events.put(("line", "Ошибка чтения: " + str(exc) + "\n"))
        finally:
            self.events.put(("exit", None))

    def _drain_events(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "line":
                    if self.pending_marker is not None and value.strip() == self.pending_marker:
                        self.pending_marker = None
                        self.command.configure(state="normal")
                        self.command.focus_set()
                    else:
                        self._append(value)
                else:
                    self._append("\n[PowerShell завершён]\n", "info")
                    self.command.configure(state="disabled")
                    self.pending_marker = None
                    self.closed = True
                    self._disable_input()
                    self.input_status.configure(text="Input interception: OFF")
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(60, self._drain_events)

    def _close(self):
        if self._destroyed:
            return
        if self.input_hook:
            self.input_hook.close()
            self.input_hook = None
        self._close_virtual_cursor()
        self._close_desktop_cursor()
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
        self._destroyed = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._close()


def launch(title="Window Manager", *, experimental_input=True, virtual_mouse=True,
           desktop_cursor=True):
    """Show a PowerShell command window with WDA_EXCLUDEFROMCAPTURE.

    Runs the Tk event loop until the window closes; call from the main thread.
    Windows 10 build 19041+ or Windows 11 required.
    experimental_input=True enables limited foreground keyboard/button hooks.
    virtual_mouse=True also enables a client-only virtual pointer and input hooks.
    desktop_cursor=True temporarily blanks system cursor roles and displays an
    excluded overlay over this window, PyCharm and VS Code. Disable with False.
    Ctrl+Alt+F12 restores cursors and disables interception.
    This is not protection against all observers.
    """
    if sys.platform != "win32":
        raise OSError("mathcraft works only on Windows")
    if sys.getwindowsversion().build < 19041:
        raise OSError("WDA_EXCLUDEFROMCAPTURE requires Windows 10 version 2004+ or Windows 11")
    # LL mouse coordinates are per-monitor-aware physical screen coordinates.
    # Match the GUI and hook worker without changing the host process DPI mode.
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    previous = None
    if experimental_input or virtual_mouse or desktop_cursor:
        previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        _Terminal(title, experimental_input=experimental_input, virtual_mouse=virtual_mouse,
                  desktop_cursor=desktop_cursor).run()
    finally:
        if previous:
            user.SetThreadDpiAwarenessContext(previous)
