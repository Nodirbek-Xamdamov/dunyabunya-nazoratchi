"""Excel file with several sheets (daily summary + intervals), with the same formula-injection protection
as core.export."""

from __future__ import annotations

from io import BytesIO

import pandas as pd

from core.export import sanitize


def workbook(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            safe = sanitize(frame)
            safe.to_excel(writer, index=False, sheet_name=name[:31])
            sheet = writer.sheets[name[:31]]
            for index, column in enumerate(safe.columns, start=1):
                lengths = safe[column].astype(str).str.len()
                width = min(50, max(10, int(lengths.quantile(0.9)) + 2 if len(safe) else 10, len(str(column)) + 2))
                sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
            sheet.freeze_panes = "A2"
    return buffer.getvalue()
