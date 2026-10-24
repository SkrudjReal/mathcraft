import unittest


class FakeCursorAPI:
    def __init__(self, fail_snapshot=None, fail_replace=None):
        from mathcraft._cursor_scheme import CURSOR_IDS
        self.roles = {key: ('original', key) for key in CURSOR_IDS}
        self.fail_snapshot = fail_snapshot
        self.fail_replace = fail_replace
        self.resets = 0
        self.destroyed = []

    def snapshot(self, key):
        if key == self.fail_snapshot:
            raise RuntimeError('snapshot failed')
        return self.roles[key]

    def transparent(self):
        return ('transparent',)

    def replace(self, cursor, key):
        if key == self.fail_replace and cursor == ('transparent',):
            raise RuntimeError('replacement failed')
        self.roles[key] = cursor

    def destroy(self, cursor):
        self.destroyed.append(cursor)

    def reset_scheme(self):
        self.resets += 1


class DesktopCursorTests(unittest.TestCase):
    def test_cursor_scheme_module_exists(self):
        from mathcraft import _cursor_scheme
        self.assertTrue(callable(_cursor_scheme.CursorScheme))

    def test_cursor_scheme_hides_and_restores_all_roles(self):
        from mathcraft._cursor_scheme import CursorScheme, CURSOR_IDS
        api = FakeCursorAPI()
        original = dict(api.roles)
        scheme = CursorScheme(api)
        scheme.hide()
        self.assertEqual(scheme.applied, set(CURSOR_IDS))
        self.assertTrue(all(value == ('transparent',) for value in api.roles.values()))
        scheme.restore()
        self.assertEqual(api.roles, original)
        scheme.restore()
        self.assertEqual(api.resets, 0)

    def test_partial_replacement_failure_rolls_back(self):
        from mathcraft._cursor_scheme import CursorScheme, CURSOR_IDS
        api = FakeCursorAPI(fail_replace=CURSOR_IDS[3])
        original = dict(api.roles)
        scheme = CursorScheme(api)
        with self.assertRaises(RuntimeError):
            scheme.hide()
        self.assertEqual(api.roles, original)
        self.assertFalse(scheme.saved)

    def test_snapshot_failure_never_modifies_cursor_roles(self):
        from mathcraft._cursor_scheme import CursorScheme, CURSOR_IDS
        api = FakeCursorAPI(fail_snapshot=CURSOR_IDS[3])
        original = dict(api.roles)
        with self.assertRaises(RuntimeError):
            CursorScheme(api).hide()
        self.assertEqual(api.roles, original)
        self.assertEqual(len(api.destroyed), 3)

    def test_failed_restore_uses_system_scheme_fallback(self):
        from mathcraft._cursor_scheme import CursorScheme, CURSOR_IDS
        api = FakeCursorAPI()
        scheme = CursorScheme(api)
        scheme.hide()
        replace = api.replace
        def fail_one_original(cursor, key):
            if key == CURSOR_IDS[0] and cursor[0] == 'original':
                raise RuntimeError('restore failed')
            replace(cursor, key)
        api.replace = fail_one_original
        scheme.restore()
        self.assertEqual(api.resets, 1)
        self.assertFalse(scheme.saved)

    def test_emergency_cleanup_restores_widget_cursors_in_order(self):
        from types import SimpleNamespace
        from mathcraft.terminal import _Terminal
        order = []
        terminal = _Terminal.__new__(_Terminal)
        terminal.input_hook = SimpleNamespace(
            disable=lambda reason: order.append('disable'),
            close=lambda: order.append('hook'))
        terminal.virtual_cursor = SimpleNamespace(close=lambda: order.append('virtual'))
        terminal.desktop_cursor = SimpleNamespace(close=lambda: order.append('desktop'))
        terminal._mouse_targets = {}
        terminal.input_status = SimpleNamespace(configure=lambda **kwargs: None)
        terminal._disable_input()
        self.assertEqual(order, ['disable', 'hook', 'virtual', 'desktop'])
        self.assertIsNone(terminal.input_hook)
        self.assertIsNone(terminal.virtual_cursor)
        self.assertIsNone(terminal.desktop_cursor)

    def test_exact_executable_matching(self):
        from mathcraft._desktop_cursor import allowed_executable
        for name in ('C:\\Apps\\PyCharm64.exe', 'Code.exe', 'code-insiders.exe', 'pycharm.exe'):
            self.assertTrue(allowed_executable(name), name)
        for name in ('not-code.exe', 'Code.exe.bak', 'some-pycharm64.exe', 'notepad.exe'):
            self.assertFalse(allowed_executable(name), name)

    def test_launch_enables_desktop_cursor_by_default(self):
        import inspect
        import mathcraft
        self.assertIs(inspect.signature(mathcraft.launch).parameters['desktop_cursor'].default, True)


if __name__ == '__main__':
    unittest.main()
