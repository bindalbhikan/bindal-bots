"""Moved to bindal/portals/srm.py; this name stays so older imports keep working."""
import sys

from ...portals import srm as _module

sys.modules[__name__] = _module
