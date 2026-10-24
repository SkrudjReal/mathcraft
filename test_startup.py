import inspect
import unittest
from unittest.mock import Mock, patch
from mathcraft.terminal import _Terminal
import mathcraft.terminal as terminal


class StartupTests(unittest.TestCase):
    def make_terminal(self, command='ssh pufa@193.233.136.131'):
        instance = _Terminal.__new__(_Terminal)
        instance.startup_command = command
        instance._startup_sent = False
        instance.closed = False
        instance._destroyed = False
        instance._execute_command = Mock()
        return instance

    def test_default_ssh_command(self):
        self.assertEqual(inspect.signature(terminal.launch).parameters['startup_command'].default,
                         'ssh pufa@193.233.136.131')

    def test_startup_runs_once(self):
        instance = self.make_terminal()
        instance._run_startup()
        instance._run_startup()
        instance._execute_command.assert_called_once_with('ssh pufa@193.233.136.131')

    def test_startup_can_be_disabled(self):
        for value in (None, '', '   '):
            instance = self.make_terminal(value)
            instance._run_startup()
            instance._execute_command.assert_not_called()

    def test_closed_window_does_not_start_ssh(self):
        instance = self.make_terminal()
        instance.closed = True
        instance._run_startup()
        instance._execute_command.assert_not_called()

    def test_startup_transmits_exact_command_to_powershell(self):
        import base64
        import re
        instance = self.make_terminal()
        del instance._execute_command
        instance.pending_marker = None
        instance.command = Mock()
        instance._append = Mock()
        instance._send_raw = Mock()
        instance._run_startup()
        statement = instance._send_raw.call_args.args[0]
        encoded = re.search(r"FromBase64String\('([^']+)'\)", statement).group(1)
        self.assertEqual(base64.b64decode(encoded).decode('utf-16le'),
                         'ssh pufa@193.233.136.131')
        self.assertIsNotNone(instance.pending_marker)
        instance.command.configure.assert_called_once_with(state='disabled')

    def test_custom_startup_command_is_preserved(self):
        instance = self.make_terminal('Get-Date')
        instance._run_startup()
        instance._execute_command.assert_called_once_with('Get-Date')


if __name__ == '__main__':
    unittest.main()
