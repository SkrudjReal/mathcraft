"""Mathematics utilities and an explicit Windows terminal/cursor entry point."""

from . import mathematics

__version__ = "0.1.0"
__all__ = ["mathematics", "math", "launch"]


def math(*args, title="Window Manager", experimental_input=True,
         virtual_mouse=True, desktop_cursor=True, **kwargs):
    """Open the terminal and cursor modes; this function does not calculate.

    Positional arguments and unknown keyword arguments are ignored. Python still
    evaluates argument expressions before this function is called.
    All three modes default to True; explicit settings can disable them.
    Ctrl+Alt+F12 restores cursors and disables interception.
    """
    from .terminal import launch as _launch
    return _launch(title, experimental_input=experimental_input,
                   virtual_mouse=virtual_mouse, desktop_cursor=desktop_cursor)


def launch(title="Window Manager", *, experimental_input=True,
           virtual_mouse=True, desktop_cursor=True):
    """Compatibility entry point; keeps the positional window title argument."""
    return math(title=title, experimental_input=experimental_input,
                virtual_mouse=virtual_mouse, desktop_cursor=desktop_cursor)
