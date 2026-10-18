"""Moved to bindal/portals/omsguru.py; this name stays so older imports keep working."""
import sys

from ..portals import omsguru as _module

sys.modules[__name__] = _module
