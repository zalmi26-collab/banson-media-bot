"""Caption parser: turns user text into a structured destination."""
from __future__ import annotations

import re
from dataclasses import dataclass

CAPTION_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*$")


@dataclass(frozen=True)
class Destination:
    plot: int
    building: int
    apartment: int
    stage: int

    def as_path_key(self) -> tuple[int, int, int, int]:
        return (self.plot, self.building, self.apartment, self.stage)


class ParseError(Exception):
    """Caption did not match the expected format."""


def parse_caption(text: str | None) -> Destination | None:
    """Return Destination if the text is a valid routing, else None.

    Raises ParseError only when text looks like an attempt at routing but is malformed
    (contains slashes and digits but doesn't match the 4-part shape). Returns None for
    completely unrelated text so the caller can decide what to do.
    """
    if not text:
        return None

    stripped = text.strip()
    m = CAPTION_RE.match(stripped)
    if m:
        return Destination(
            plot=int(m.group(1)),
            building=int(m.group(2)),
            apartment=int(m.group(3)),
            stage=int(m.group(4)),
        )

    if "/" in stripped and any(c.isdigit() for c in stripped):
        raise ParseError(stripped)

    return None
