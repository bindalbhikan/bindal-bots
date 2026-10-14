import re

import pytest

from bindal.core import telegram
from bindal.core.config import flag
from bindal.core.drive import DriveError, DriveFile


def balanced(part: str) -> bool:
    stack = []
    for m in telegram.TAG.finditer(part):
        name = m.group(2).lower()
        if m.group(1):
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack


def test_short_message_is_one_part():
    assert telegram.split_html("<b>hi</b>") == ["<b>hi</b>"]


def test_long_pre_block_is_split_with_balanced_tags():
    rows = "\n".join(f"AWB{i:012d} Shadowfax 14-Oct Over &amp; more" for i in range(400))
    text = "<b>Pickup Pending</b>\n<pre>" + rows + "</pre>\n<i>legend</i>"
    parts = telegram.split_html(text, 1000)
    assert len(parts) > 5
    for part in parts:
        assert len(part) <= 1000
        assert balanced(part), part[:80]
        assert not re.search(r"&[a-z]*$", part.split("</")[0])  # no entity cut in half
    # nothing lost: the rows all come back once the added tags are removed
    joined = "".join(parts).replace("</pre><pre>", "")
    assert joined.count("AWB") == 400


def test_one_huge_line_is_cut():
    parts = telegram.split_html("<pre>" + "x" * 9000 + "</pre>", 4000)
    assert all(len(p) <= 4000 and balanced(p) for p in parts)
    assert sum(p.count("x") for p in parts) == 9000


@pytest.mark.parametrize("value,default,expected", [
    ("1", False, True), ("true", False, True), ("YES", False, True),
    ("0", True, False), ("false", True, False), ("off", True, False),
    ("", True, True), ("maybe", True, True), ("", False, False),
])
def test_flag(monkeypatch, value, default, expected):
    monkeypatch.setenv("X_FLAG", value)
    assert flag("X_FLAG", default) is expected


def test_upload_needs_a_download_first():
    drive = DriveFile("file-id", http=object())
    with pytest.raises(DriveError):
        drive.upload(__file__)
