"""Update the Gantt sheet with Drive folder links after media upload.

The sheet has a 3-level merged header:
  Row 1: plot number (e.g., 1096)         — merged across all that plot's buildings
  Row 2: building number (e.g., 6)        — merged across all that building's apts
  Row 3: apartment number (e.g., 1)       — merged across that apt's 4 sub-columns
  Row 4: per-apt sub-headers              — מתוכנן | בפועל | מבצע | תיעוד  (repeated)

Stage rows start at row 5 — stage N is at row ``N + 4``.

Per upload we write the folder Drive URL into the תיעוד cell of (plot, bldg, apt, stage).
Folder URL is preferred over file URL: it remains valid for subsequent uploads to the
same destination, so the cell becomes a permanent pointer to "everything for this stage".

The (plot, building, apartment) → column-index map is read once from rows 1-3 and
cached in memory. The sheet structure rarely changes; if it does, restart the bot.
"""
from __future__ import annotations

import logging
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SHEETS_HEADER_ROWS = 4  # rows 1..4 are headers; data starts at row 5
TIEVUD_OFFSET_IN_APT_BLOCK = 3  # 4th sub-column (index 3): מתוכנן, בפועל, מבצע, תיעוד


class SheetsClient:
    def __init__(self, credentials, spreadsheet_id: str, sheet_name: str = "גיליון1"):
        self._svc = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._spreadsheet_id = spreadsheet_id
        self._sheet_name = sheet_name
        self._column_map: Optional[dict[tuple[int, int, int], int]] = None

    def _load_column_map(self) -> dict[tuple[int, int, int], int]:
        """Walk header rows 1-3 left-to-right, propagating merged values forward,
        and return ``{(plot, building, apt): tievud_col_index}``.

        A cell that's part of a merge range only carries its value in the first
        column of the range; the rest are blank in the values response. So we
        forward-fill along the row to recover (plot, building) for each apt-cell.
        """
        result = self._svc.spreadsheets().values().get(
            spreadsheetId=self._spreadsheet_id,
            range=f"{self._sheet_name}!1:3",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 3:
            raise RuntimeError("Gantt sheet is missing the 3 header rows")

        plot_row, bldg_row, apt_row = rows[0], rows[1], rows[2]
        ncols = max(len(plot_row), len(bldg_row), len(apt_row))

        plot_filled = _forward_fill(plot_row, ncols)
        bldg_filled = _forward_fill(bldg_row, ncols)

        mapping: dict[tuple[int, int, int], int] = {}
        for col_idx in range(ncols):
            apt_val = apt_row[col_idx] if col_idx < len(apt_row) else ""
            if not apt_val.strip():
                continue
            try:
                plot = int(plot_filled[col_idx])
                bldg = int(bldg_filled[col_idx])
                apt = int(apt_val)
            except (ValueError, TypeError):
                continue
            tievud_col = col_idx + TIEVUD_OFFSET_IN_APT_BLOCK
            mapping[(plot, bldg, apt)] = tievud_col

        return mapping

    def set_documentation_link(
        self,
        *,
        plot: int,
        building: int,
        apartment: int,
        stage: int,
        url: str,
    ) -> bool:
        """Write ``url`` into the תיעוד cell for (plot, bldg, apt, stage).

        Returns True if the cell was written, False if the destination doesn't
        exist in the sheet (e.g., a (plot, bldg, apt) combination not represented
        in the headers, or a stage row missing). Logs a warning in that case so
        we don't fail the WhatsApp upload just because the sheet is out of sync.
        """
        if self._column_map is None:
            self._column_map = self._load_column_map()

        col_idx = self._column_map.get((plot, building, apartment))
        if col_idx is None:
            log.warning(
                "no sheet column for plot=%s building=%s apt=%s — skipping link write",
                plot, building, apartment,
            )
            return False

        row = stage + SHEETS_HEADER_ROWS  # stage 1 → row 5
        cell = f"{self._sheet_name}!{_col_letter(col_idx)}{row}"

        try:
            self._svc.spreadsheets().values().update(
                spreadsheetId=self._spreadsheet_id,
                range=cell,
                valueInputOption="USER_ENTERED",  # so URLs become clickable hyperlinks
                body={"values": [[url]]},
            ).execute()
        except HttpError as e:
            log.warning("sheets write failed for %s: %s", cell, e)
            return False

        log.info("wrote sheet cell %s for plot=%s/bldg=%s/apt=%s/stage=%s",
                 cell, plot, building, apartment, stage)
        return True


def _forward_fill(row: list, length: int) -> list[str]:
    """Replace every blank cell with the most recent non-blank to its left."""
    out: list[str] = []
    last = ""
    for i in range(length):
        v = row[i] if i < len(row) else ""
        if v and v.strip():
            last = v
        out.append(last)
    return out


def _col_letter(idx: int) -> str:
    """0-indexed column → A1 letter (e.g., 0→A, 26→AA, 702→AAA)."""
    s = ""
    n = idx
    while True:
        s = chr(65 + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            return s
