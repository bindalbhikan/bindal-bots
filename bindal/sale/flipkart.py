"""Moved to bindal/portals/flipkart.py; this name stays so older imports keep working."""
import sys

from ..portals import flipkart as _module

sys.modules[__name__] = _module
