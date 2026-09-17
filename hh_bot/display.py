"""Enable sharp rendering on high-DPI Windows displays before creating Tk."""
import ctypes
import sys


def enable_dpi_awareness() -> None:
    if sys.platform != 'win32':
        return
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        if setter(ctypes.c_void_p(-4)) or ctypes.get_last_error() == 5:
            return  # Already configured by a manifest / host.
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def scaled_size(window, width: int, height: int) -> str:
    factor = max(1.0, window.winfo_fpixels('1i') / 96.0)
    return f'{round(width * factor)}x{round(height * factor)}'
