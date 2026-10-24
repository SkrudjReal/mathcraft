"""Run commands in PowerShell from a Python-owned, capture-excluded Tk window.

The standard powershell.exe console window is NOT protected by this module.
Only the top-level Tk window created by this process is protected.
"""

import ctypes
from ctypes import wintypes
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from . import DEFAULT_STARTUP_COMMAND

WDA_EXCLUDEFROMCAPTURE = 0x00000011
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
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


def _hide_from_taskbar(hwnd):
    user32=ctypes.WinDLL('user32',use_last_error=True)
    get_style=getattr(user32,'GetWindowLongPtrW',user32.GetWindowLongW)
    set_style=getattr(user32,'SetWindowLongPtrW',user32.SetWindowLongW)
    get_style.argtypes=(wintypes.HWND,ctypes.c_int)
    get_style.restype=ctypes.c_ssize_t
    set_style.argtypes=(wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t)
    set_style.restype=ctypes.c_ssize_t
    user32.SetWindowPos.argtypes=(wintypes.HWND,wintypes.HWND,ctypes.c_int,
                                  ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT)
    user32.SetWindowPos.restype=wintypes.BOOL
    old_style=get_style(hwnd,GWL_EXSTYLE)
    new_style=(old_style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
    ctypes.set_last_error(0)
    if not set_style(hwnd,GWL_EXSTYLE,new_style) and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.SetWindowPos(hwnd,None,0,0,0,0,0x0001|0x0002|0x0004|0x0020):
        raise ctypes.WinError(ctypes.get_last_error())


class _Terminal:
    def __init__(self, title, experimental_input=True, virtual_mouse=True,
                 desktop_cursor=True, startup_command=None, hide_from_taskbar=True):
        if startup_command is not None and not isinstance(startup_command, str):
            raise TypeError('startup_command must be a string or None')
        self.startup_command = startup_command
        self._startup_sent = False
        self.root = tk.Tk()
        self.root.withdraw()  # Configure exclusion before making the window visible.
        self.root.title(title)
        self.root.geometry("820x520")
        self.root.minsize(440, 260)
        self.root.configure(bg="#171717")
        self.events = queue.Queue(maxsize=512)
        self.session = None
        self._resize_after = None
        self._style_tags = {}
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
            if hide_from_taskbar:
                _hide_from_taskbar(self.hwnd)
        except Exception:
            self.root.destroy()
            raise

        self._font = tkfont.Font(family="Consolas", size=11)
        self.output = tk.Text(
            self.root, wrap="none", height=1, width=1, bg="#171717", fg="#dedede",
            insertbackground="#dedede", font=self._font, cursor="xterm",
            relief="flat", padx=8, pady=8, state="disabled", exportselection=False,
        )
        self.output.pack(fill="both", expand=True)
        self.command = self.output
        self.output.tag_configure("terminal_cursor", background="#dedede", foreground="#171717")
        tk.Label(self.root, text="ConPTY • Ctrl+C: interrupt • Ctrl+Shift+C/V: copy/paste • Shift+PgUp/PgDn: history",
                 bg="#222222", fg="#dedede").pack(fill="x")
        self.output.bind('<KeyPress>', self._terminal_key)
        self.output.bind('<<Paste>>', self._paste)
        self.output.bind('<MouseWheel>', self._wheel)
        self.output.bind('<Button-1>', lambda event: self.output.focus_set(), add='+')
        self.output.bind('<Configure>', self._queue_resize)
        for sequence in ("<ButtonRelease-1>", "<KeyRelease>", "<<Selection>>"):
            self.output.bind(sequence, self._selection_changed, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.input_status = tk.Label(self.root, text="Input interception: OFF",
                                     bg="#222222", fg="#dedede")
        self.input_status.pack(fill="x")
        self.disable_button = tk.Button(self.root,
            text="Restore cursor / disable input (Ctrl+Alt+F12)", command=self._disable_input)
        self.disable_button.pack(fill="x")
        self.root.bind("<Control-Alt-F12>", self._disable_input, add="+")

        try:
            from ._pty import ConPtySession
            from ._screen import TerminalScreen
            self.screen = TerminalScreen(80, 24, self._terminal_write)
            self._reset_screen()
            utf8_console = ("$utf8 = New-Object System.Text.UTF8Encoding; "
                            "[Console]::InputEncoding = $utf8; "
                            "[Console]::OutputEncoding = $utf8; "
                            "$OutputEncoding = $utf8; chcp 65001 > $null")
            self.session = ConPtySession(
                ['powershell.exe', '-NoLogo', '-NoProfile', '-NoExit',
                 '-Command', utf8_console], self.events)
        except Exception:
            self.root.destroy()
            raise
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
        self.root.after(16, self._drain_events)
        self.root.deiconify()
        self.output.focus_set()
        if startup_command and startup_command.strip():
            self.root.after(0, self._run_startup)

    def _run_startup(self):
        if self._startup_sent or self.closed or self._destroyed:
            return
        self._startup_sent = True
        if self.startup_command and self.startup_command.strip():
            self._execute_command(self.startup_command)

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
            if self.root.focus_get() is not self.output:
                return
            names={8:'BackSpace',9:'Tab',13:'Return',27:'Escape',33:'Prior',34:'Next',
                   35:'End',36:'Home',37:'Left',38:'Up',39:'Right',40:'Down',45:'Insert',46:'Delete'}
            name=names.get(key,chr(key).lower() if 32<=key<127 else '')
            self._handle_key(name,text,shift,control)
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

    def _terminal_write(self, data):
        if self.closed or self.session is None:
            return
        try:
            self.session.write(data)
        except (RuntimeError, ValueError) as exc:
            self.input_status.configure(text=str(exc))

    def _terminal_key(self, event):
        control, shift = bool(event.state & 4), bool(event.state & 1)
        alt = bool(event.state & (8 | 0x20000))
        if control and alt and event.keysym == 'F12':
            return self._disable_input()
        text=event.char or ''
        if alt and control and text and text.isprintable():
            alt=control=False  # AltGr text is not a Ctrl command.
        key=event.keysym
        if control and 0x41<=event.keycode<=0x5A:
            key=chr(event.keycode).lower()
        self._handle_key(key,text,shift,control,alt)
        return 'break'

    def _handle_key(self, key, text='', shift=False, control=False, alt=False):
        from ._screen import encode_key
        if control and key.lower()=='c':
            if shift or self.output.tag_ranges('sel'):
                self._copy_selection(self.output)
                return
        if control and key.lower()=='v':
            self._paste()
            return
        if shift and key in ('Prior','Next'):
            (self.screen.prev_page if key=='Prior' else self.screen.next_page)()
            self._render_screen()
            return
        data=encode_key(key,text,shift,control,alt)
        if data:self._terminal_write(data)

    def _paste(self, _event=None):
        try:
            text=self.root.clipboard_get().replace('\r\n','\n').replace('\n','\r')
            self._terminal_write(text)
        except tk.TclError:
            pass
        return 'break'

    def _wheel(self, event):
        (self.screen.prev_page if event.delta>0 else self.screen.next_page)()
        self._render_screen()
        return 'break'

    def _queue_resize(self, _event=None):
        if self._resize_after:
            self.root.after_cancel(self._resize_after)
        self._resize_after=self.root.after(100,self._resize_terminal)

    def _resize_terminal(self):
        self._resize_after=None
        if self._destroyed or self.session is None:return
        columns=max(2,min(512,(self.output.winfo_width()-16)//max(1,self._font.measure('M'))))
        rows=max(2,min(256,(self.output.winfo_height()-16)//max(1,self._font.metrics('linespace'))))
        if (columns,rows)!=(self.screen.columns,self.screen.lines):
            self.screen.resize(lines=rows,columns=columns)
            self.session.resize(columns,rows)
            self._reset_screen()

    def _reset_screen(self):
        self.output.configure(state='normal')
        self.output.delete('1.0','end')
        self.output.insert('1.0','\n'.join(self.screen.display))
        self.output.configure(state='disabled')
        self.screen.dirty.update(range(self.screen.lines))
        self._render_screen()

    def _style(self, cell):
        colors={'default':None,'black':'#171717','red':'#cd3131','green':'#0dbc79',
                'brown':'#e5e510','blue':'#2472c8','magenta':'#bc3fbc',
                'cyan':'#11a8cd','white':'#dedede','brightblack':'#666666',
                'brightred':'#f14c4c','brightgreen':'#23d18b','brightbrown':'#f5f543',
                'brightblue':'#3b8eea','brightmagenta':'#d670d6',
                'brightcyan':'#29b8db','brightwhite':'#ffffff'}
        def color(value,default):
            if value in colors:return colors[value] or default
            if len(value)==6 and all(ch in '0123456789abcdefABCDEF' for ch in value):
                return '#'+value
            return default
        fg,bg=color(cell.fg,'#dedede'),color(cell.bg,'#171717')
        if cell.reverse:fg,bg=bg,fg
        key=(fg,bg,cell.bold,cell.underscore)
        tag=self._style_tags.get(key)
        if tag is None and len(self._style_tags)<512:
            tag='ansi_'+str(len(self._style_tags))
            self.output.tag_configure(tag,foreground=fg,background=bg,
                font=('Consolas',11,'bold' if cell.bold else 'normal'),underline=cell.underscore)
            self._style_tags[key]=tag
        return tag

    def _render_screen(self):
        self.output.configure(state='normal')
        display=self.screen.display
        for row in sorted(self.screen.dirty):
            if row>=self.screen.lines:continue
            line=str(row+1)
            self.output.delete(line+'.0',line+'.end')
            self.output.insert(line+'.0',display[row])
            offset=0
            for column in range(self.screen.columns):
                cell=self.screen.buffer[row][column]
                length=len(cell.data)
                if length:
                    tag=self._style(cell)
                    if tag:self.output.tag_add(tag,line+'.'+str(offset),line+'.'+str(offset+length))
                    offset+=length
        self.screen.dirty.clear()
        self.output.tag_remove('terminal_cursor','1.0','end')
        cursor=self.screen.cursor
        if not cursor.hidden:
            x=sum(len(self.screen.buffer[cursor.y][i].data) for i in range(min(cursor.x,self.screen.columns)))
            index=str(cursor.y+1)+'.'+str(min(x,self.screen.columns-1))
            self.output.tag_add('terminal_cursor',index,index+'+1c')
            self.output.tag_raise('terminal_cursor')
        self.output.configure(state='disabled')

    def _append(self, value, tag=None):
        self.screen.feed(value.replace('\n','\r\n'))
        self._render_screen()

    def _execute_command(self, cmd):
        if not self.closed and cmd.strip():
            self._terminal_write(cmd+'\r')
        return 'break'

    def _drain_events(self):
        if self.closed:return
        try:
            for _ in range(128):
                kind,value=self.events.get_nowait()
                if kind=='data':
                    self.screen.feed(value)
                elif kind=='exit':
                    self.closed=True
                    self._disable_input()
                    self.session.close()
                    self.input_status.configure(text='Console process exited'+(': '+value if value else ''))
        except queue.Empty:
            pass
        self._render_screen()
        if not self.closed:self.root.after(16,self._drain_events)

    def _close(self):
        if self._destroyed:
            return
        if self.input_hook:
            self.input_hook.close()
            self.input_hook = None
        self._close_virtual_cursor()
        self._close_desktop_cursor()
        self.closed = True
        if self.session is not None:
            self.session.close()
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
           desktop_cursor=True, startup_command=DEFAULT_STARTUP_COMMAND,
           hide_from_taskbar=False):
    """Show a PowerShell command window with WDA_EXCLUDEFROMCAPTURE.

    Runs the Tk event loop until the window closes; call from the main thread.
    Windows 10 build 19041+ or Windows 11 required.
    experimental_input=True enables limited foreground keyboard/button hooks.
    virtual_mouse=True also enables a client-only virtual pointer and input hooks.
    desktop_cursor=True temporarily blanks system cursor roles and displays an
    excluded overlay over this window, PyCharm and VS Code. Disable with False.
    startup_command runs once after the UI opens; None disables auto-SSH.
    hide_from_taskbar=True hides the window from the taskbar; defaults to False.
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
                  desktop_cursor=desktop_cursor, startup_command=startup_command,
                  hide_from_taskbar=hide_from_taskbar).run()
    finally:
        if previous:
            user.SetThreadDpiAwarenessContext(previous)
