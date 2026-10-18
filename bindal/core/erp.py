"""Moved to bindal/portals/erp.py; this name stays so older imports keep working."""
import sys

from ..portals import erp as _module

sys.modules[__name__] = _module
