"""Moved to bindal/portals/myntra.py; this name stays so older imports keep working."""
import sys

from ..portals import myntra as _module

sys.modules[__name__] = _module
