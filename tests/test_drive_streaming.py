"""Tests for the streaming wrapper that makes 'no disk' uploads work.

The Drive API integration itself (folder resolution, upload) is tested
manually against a real Drive account - that's where the value is. The
local logic worth testing is the byte iterator wrapper.
"""
from drive_client import _IterBytesIO


def test_read_single_chunk_smaller_than_buffer():
    src = iter([b"hello world"])
    s = _IterBytesIO(src)
    assert s.read(5) == b"hello"
    assert s.read(20) == b" world"
    assert s.read(20) == b""  # EOF


def test_read_across_chunks():
    src = iter([b"abc", b"def", b"ghi"])
    s = _IterBytesIO(src)
    assert s.read(7) == b"abcdefg"
    assert s.read(2) == b"hi"
    assert s.read(1) == b""


def test_read_exact_chunk_boundary():
    src = iter([b"abc", b"def"])
    s = _IterBytesIO(src)
    assert s.read(3) == b"abc"
    assert s.read(3) == b"def"
    assert s.read(3) == b""


def test_read_all():
    src = iter([b"abc", b"def", b"ghi"])
    s = _IterBytesIO(src)
    assert s.read(-1) == b"abcdefghi"


def test_read_all_after_partial():
    src = iter([b"abc", b"def", b"ghi"])
    s = _IterBytesIO(src)
    assert s.read(2) == b"ab"
    assert s.read(-1) == b"cdefghi"


def test_empty_stream():
    s = _IterBytesIO(iter([]))
    assert s.read(10) == b""


def test_readable():
    s = _IterBytesIO(iter([b"x"]))
    assert s.readable() is True
