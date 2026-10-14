"""Writing a workbook safely: save to a temp file, then swap it in."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def replace_atomically(save: Callable[[Path], None], path: Path) -> Path:
    """save(tmp) writes the new file; it only replaces `path` once it is complete,
    so a crash halfway never leaves a broken workbook behind."""
    path = Path(path)
    tmp = path.with_name(path.stem + "_tmp" + path.suffix)
    save(tmp)
    try:
        os.replace(tmp, path)
    except PermissionError:
        raise RuntimeError(f"{path.name} is open in Excel - close it and run again: {path}") from None
    return path
