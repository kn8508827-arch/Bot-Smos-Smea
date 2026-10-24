"""Make Pillow shape Khmer text on servers without the system fribidi library.

Pillow's wheels include libraqm/HarfBuzz but load libfribidi at runtime. Railway's image
doesn't have it, so Khmer on picture cards came out as empty boxes. We ship
assets/lib/libfribidi.so.0 (GNU FriBidi, LGPL-2.1 — see assets/lib/LICENSE_FRIBIDI.txt)
and load it globally *before* PIL.ImageFont is imported; Pillow's later
dlopen("libfribidi.so.0") then finds the already-loaded copy.
"""
import ctypes
import ctypes.util
import logging
import os
import sys

logger = logging.getLogger(__name__)
_HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(_HERE, "assets", "lib", "libfribidi.so.0")


def preload():
    if not sys.platform.startswith("linux"):
        return False
    if ctypes.util.find_library("fribidi"):
        return True                      # the system already has it
    if not os.path.exists(BUNDLED):
        return False
    try:
        ctypes.CDLL(BUNDLED, mode=ctypes.RTLD_GLOBAL)
        return True
    except OSError as exc:
        logger.warning("bundled fribidi not loaded (%s)", exc)
        return False


PRELOADED = preload()
