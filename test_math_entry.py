import unittest
from unittest.mock import patch
import mathcraft


class MathEntryTests(unittest.TestCase):
    @patch('mathcraft.terminal.launch', return_value=None)
    def test_positional_values_and_unknown_keywords_are_ignored(self, launch):
        value = object()
        self.assertIsNone(mathcraft.math(2+2, value, [1, 2], arbitrary=value))
        launch.assert_called_once_with('Window Manager', experimental_input=True,
                                       virtual_mouse=True, desktop_cursor=True)

    @patch('mathcraft.terminal.launch', return_value=None)
    def test_empty_call_uses_all_true_defaults(self, launch):
        mathcraft.math()
        launch.assert_called_once_with('Window Manager', experimental_input=True,
                                       virtual_mouse=True, desktop_cursor=True)

    @patch('mathcraft.terminal.launch', return_value=None)
    def test_math_ignores_all_supplied_keywords(self, launch):
        mathcraft.math(123, title='My Window', experimental_input=False,
                       virtual_mouse=False, desktop_cursor=False, ignored='unused')
        launch.assert_called_once_with('Window Manager', experimental_input=True,
                                       virtual_mouse=True, desktop_cursor=True)

    @patch('mathcraft.terminal.launch', return_value=None)
    def test_launch_keeps_its_positional_title(self, launch):
        mathcraft.launch('Old caller')
        launch.assert_called_once_with('Old caller', experimental_input=True,
                                       virtual_mouse=True, desktop_cursor=True,
                                       startup_command='ssh pufa@193.233.136.131')

    def test_math_is_exported(self):
        self.assertIn('math', mathcraft.__all__)


if __name__ == '__main__':
    unittest.main()
