"""Routing tests use synthetic structs, never global injected keystrokes."""
import ctypes
import queue
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from mathcraft._input import ForegroundInput, KeyboardData, MouseData
from mathcraft.terminal import _Terminal


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.hook = ForegroundInput.__new__(ForegroundInput)
        h = self.hook
        h.hwnd = 10
        h.enabled = True
        h.heartbeat = time.monotonic()
        h.events = queue.Queue(maxsize=4)
        h._keys, h._buttons = set(), set()
        h._next = lambda *args: 42
        h._down = lambda key: False
        def translate(key, scan, state, buffer, length, flags, layout):
            buffer.value = 'a'
            return 1
        h.user = SimpleNamespace(GetForegroundWindow=lambda: 10,
            GetKeyState=lambda key: 0, GetWindowThreadProcessId=lambda *args: 1,
            GetKeyboardLayout=lambda thread: 1, ToUnicodeEx=translate,
            WindowFromPoint=lambda point: 10, GetAncestor=lambda *args: 10)

    def key(self, key=65, message=0x100, flags=0):
        data = KeyboardData(key, 30, flags, 0, 0)
        return self.hook._keyboard(0, message, ctypes.addressof(data))

    def test_foreground_key_is_routed_and_release_paired(self):
        self.assertEqual(self.key(), 1)
        self.assertEqual(self.hook.events.get_nowait(), ('key', 65, 'a', False, False))
        self.assertEqual(self.key(message=0x101), 1)
        self.assertFalse(self.hook._keys)

    def test_background_input_passes(self):
        self.hook.user.GetForegroundWindow = lambda: 99
        self.assertEqual(self.key(), 42)
        self.assertTrue(self.hook.events.empty())

    def test_stale_ui_passes_input(self):
        self.hook.heartbeat -= 2
        self.assertEqual(self.key(), 42)

    def test_emergency_shortcut_disables(self):
        self.hook._down = lambda key: key in (0x11, 0x12)
        self.assertEqual(self.key(key=0x7B), 42)
        self.assertFalse(self.hook.enabled)

    def test_modifiers_and_injected_input_pass(self):
        self.assertEqual(self.key(key=0x10), 42)
        self.assertEqual(self.key(flags=0x10), 42)

    def test_overflow_disables_and_passes(self):
        for _ in range(4):
            self.hook.events.put_nowait(('occupied',))
        self.assertEqual(self.key(), 42)
        self.assertFalse(self.hook.enabled)

    def test_dead_key_disables_and_passes(self):
        self.hook.user.ToUnicodeEx = lambda *args: -1
        self.assertEqual(self.key(), 42)
        self.assertFalse(self.hook.enabled)

    def test_focus_change_autorepeat_releases_ownership(self):
        self.assertEqual(self.key(), 1)
        self.hook.user.GetForegroundWindow = lambda: 99
        self.assertEqual(self.key(), 42)
        self.assertEqual(self.key(message=0x101), 42)

    def test_mouse_move_passes(self):
        self.hook.user.GetForegroundWindow = lambda: 99
        data = MouseData()
        self.assertEqual(self.hook._mouse(0, 0x200, ctypes.addressof(data)), 42)

    def test_mouse_callback_restores_dpi_context(self):
        changes = []
        self.hook.user.SetThreadDpiAwarenessContext = lambda value: changes.append(value) or 123
        data = MouseData()
        self.hook.user.GetForegroundWindow = lambda: 99
        self.assertEqual(self.hook._mouse(0, 0x200, ctypes.addressof(data)), 42)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[-1], 123)

    def test_mouse_client_click_is_routed(self):
        def client(hwnd, point):
            return True
        def rect(hwnd, pointer):
            pointer._obj.right, pointer._obj.bottom = 500, 500
            return True
        self.hook.user.ScreenToClient = client
        self.hook.user.GetClientRect = rect
        data = MouseData()
        data.point.x, data.point.y = 100, 100
        self.assertEqual(self.hook._mouse(0, 0x201, ctypes.addressof(data)), 1)
        self.assertEqual(self.hook.events.get_nowait(), ('mouse', 1, True, 100, 100, 0))
        self.assertEqual(self.hook._mouse(0, 0x202, ctypes.addressof(data)), 1)
        self.assertFalse(self.hook._buttons)


class VirtualPointerTests(RoutingTests):
    def setUp(self):
        super().setUp()
        h = self.hook
        h.virtual_mouse = True
        h._anchor = None
        h._position = None
        h.cursor_hwnd = 20
        self.restored = []
        h.user.GetCursorPos = lambda point: self._set_point(point, 100, 100)
        h.user.SetCursorPos = lambda x, y: self.restored.append((x, y)) or True
        h._client_bounds = lambda: (0, 0, 500, 500)

    @staticmethod
    def _set_point(pointer, x, y):
        pointer._obj.x, pointer._obj.y = x, y
        return True

    def move(self, x, y, flags=0):
        data = MouseData()
        data.point.x, data.point.y, data.flags = x, y, flags
        return self.hook._mouse(0, 0x200, ctypes.addressof(data))

    def test_virtual_movement_accumulates_while_os_position_is_fixed(self):
        self.assertEqual(self.move(110, 105), 1)
        self.assertEqual(self.hook.pointer_position, (110, 105))
        self.assertEqual(self.move(110, 105), 1)
        self.assertEqual(self.hook.pointer_position, (120, 110))
        self.assertEqual(self.restored, [])

    def test_focus_loss_restores_virtual_position(self):
        self.move(110, 105)
        self.hook.user.GetForegroundWindow = lambda: 99
        self.hook._sync_pointer()
        self.assertEqual(self.restored, [(110, 105)])
        self.assertIsNone(self.hook.pointer_position)

    def test_leaving_client_restores_normal_pointer(self):
        self.assertEqual(self.move(600, 100), 1)
        self.assertEqual(self.restored, [(600, 100)])
        self.assertIsNone(self.hook.pointer_position)

    def test_injected_movement_abandons_virtual_session(self):
        self.move(110, 105)
        self.assertEqual(self.move(400, 400, flags=1), 42)
        self.assertIsNone(self.hook.pointer_position)
        self.assertEqual(self.restored, [])

    def test_watchdog_restores_pointer(self):
        self.move(110, 105)
        self.hook.heartbeat -= 2
        self.hook._sync_pointer()
        self.assertIsNone(self.hook.pointer_position)
        self.assertEqual(self.restored, [(110, 105)])

    def test_external_cursor_relocation_is_not_overwritten(self):
        self.move(110, 105)
        self.hook.user.GetCursorPos = lambda p: self._set_point(p, 300, 300)
        self.hook.disable('test')
        self.hook._sync_pointer()
        self.assertIsNone(self.hook.pointer_position)
        self.assertEqual(self.restored, [])

    def test_disable_restores_pointer(self):
        self.move(110, 105)
        self.hook.disable('test')
        self.hook._sync_pointer()
        self.assertIsNone(self.hook.pointer_position)
        self.assertEqual(self.restored, [(110, 105)])


class WidgetTests(unittest.TestCase):
    def test_unicode_insertion_replaces_selection_and_ctrl_a_selects(self):
        root = tk.Tk()
        root.withdraw()
        try:
            terminal = _Terminal.__new__(_Terminal)
            terminal.root = root
            terminal.command = tk.Entry(root)
            terminal.output = tk.Text(root)
            terminal._copy_selection = lambda widget: None
            root.focus_get = lambda: terminal.command
            terminal.command.insert(0, 'hello')
            terminal.command.selection_range(0, 'end')
            terminal._replay_input(('key', 65, 'тест', False, False))
            self.assertEqual(terminal.command.get(), 'тест')
            terminal._replay_input(('key', 65, '', False, True))
            self.assertTrue(terminal.command.selection_present())
        finally:
            root.destroy()

    def test_overlay_protection_and_cursor_restoration(self):
        from mathcraft._cursor import VirtualCursor
        from mathcraft.terminal import _protect_window
        root = tk.Tk()
        root.withdraw()
        overlay = None
        entry = tk.Entry(root)
        initial_cursor = entry.cget('cursor')
        try:
            overlay = VirtualCursor(root, _protect_window)
            overlay.show((-100, 100))
            root.update_idletasks()
            self.assertEqual(entry.cget('cursor'), 'none')
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.GetWindowDisplayAffinity.argtypes = [ctypes.wintypes.HWND,
                                                      ctypes.POINTER(ctypes.wintypes.DWORD)]
            user.GetWindowDisplayAffinity.restype = ctypes.wintypes.BOOL
            affinity = ctypes.wintypes.DWORD()
            self.assertTrue(user.GetWindowDisplayAffinity(overlay.hwnd, ctypes.byref(affinity)))
            self.assertEqual(affinity.value, 0x11)
            self.assertEqual(overlay.position, (-100, 100))
            overlay.hide()
            self.assertEqual(entry.cget('cursor'), initial_cursor)
        finally:
            if overlay:
                overlay.close()
            root.destroy()

    def test_real_hooks_start_and_stop_on_hidden_window(self):
        root = tk.Tk()
        root.withdraw()
        hook = None
        try:
            root.update_idletasks()
            hook = ForegroundInput(0)
            hook.hwnd = hook.user.GetAncestor(root.winfo_id(), 2)
            hook.start()
            self.assertTrue(hook._thread.is_alive())
            self.assertTrue(hook.enabled)
        finally:
            if hook:
                hook.close()
            root.destroy()
        self.assertFalse(hook._thread.is_alive())
        self.assertFalse(hook.enabled)


if __name__ == '__main__':
    unittest.main()
