import unittest
from mathcraft._screen import TerminalScreen, encode_key


class ScreenTests(unittest.TestCase):
    def test_cursor_queries_are_answered(self):
        replies=[]
        screen=TerminalScreen(40,10,replies.append)
        screen.feed('abc\x1b[6n')
        self.assertEqual(replies,['\x1b[1;4R'])

    def test_prompt_without_newline_is_visible(self):
        screen=TerminalScreen(40,10,lambda value:None)
        screen.feed('Password: ')
        self.assertTrue(screen.display[0].startswith('Password: '))

    def test_carriage_return_overwrites(self):
        screen=TerminalScreen(40,10,lambda value:None)
        screen.feed('working\rdone\x1b[K')
        self.assertEqual(screen.display[0].strip(),'done')

    def test_input_sequences(self):
        self.assertEqual(encode_key('Return'), '\r')
        self.assertEqual(encode_key('Up'), '\x1b[A')
        self.assertEqual(encode_key('c', control=True), '\x03')
        self.assertEqual(encode_key('a', text='я'), 'я')
        self.assertEqual(encode_key('Tab',shift=True), '\x1b[Z')


if __name__=='__main__':unittest.main()
