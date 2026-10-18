"""Moved to bindal/portals/ajio.py; this name stays so older imports keep working."""
import sys

from ...portals import ajio as _module

sys.modules[__name__] = _module
