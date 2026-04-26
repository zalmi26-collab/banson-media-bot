from datetime import date

from filename import build_filename
from parser import Destination

DEST = Destination(1096, 6, 1, 18)
DAY = date(2026, 12, 29)


def test_basic():
    assert build_filename(DEST, DAY, "VID_1234.mp4", set()) == "1096-6-1-18 - 29.12.26.mp4"


def test_jpg():
    assert build_filename(DEST, DAY, "IMG_5678.JPG", set()) == "1096-6-1-18 - 29.12.26.jpg"


def test_no_extension():
    assert build_filename(DEST, DAY, "anonymous", set()) == "1096-6-1-18 - 29.12.26"


def test_none_original():
    assert build_filename(DEST, DAY, None, set()) == "1096-6-1-18 - 29.12.26"


def test_dedup_second():
    existing = {"1096-6-1-18 - 29.12.26.mp4"}
    assert build_filename(DEST, DAY, "x.mp4", existing) == "1096-6-1-18 - 29.12.26 (2).mp4"


def test_dedup_third():
    existing = {
        "1096-6-1-18 - 29.12.26.mp4",
        "1096-6-1-18 - 29.12.26 (2).mp4",
    }
    assert build_filename(DEST, DAY, "x.mp4", existing) == "1096-6-1-18 - 29.12.26 (3).mp4"


def test_dedup_case_insensitive():
    existing = {"1096-6-1-18 - 29.12.26.MP4"}
    assert build_filename(DEST, DAY, "x.mp4", existing) == "1096-6-1-18 - 29.12.26 (2).mp4"


def test_long_extension_treated_as_no_ext():
    assert build_filename(DEST, DAY, "file.archive7z", set()) == "1096-6-1-18 - 29.12.26"
