"""CSV / Excel export with formula-injection protection.

A cell that starts with = + - @ (or tab / carriage return) could be executed as a formula by Excel;
such text gets a leading apostrophe. Messages that are just "+" are common in the groups.
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralize(value):
    if isinstance(value, str) and value.startswith(_DANGEROUS_PREFIXES):
        return "'" + value
    return value


def sanitize(frame: pd.DataFrame) -> pd.DataFrame:
    safe = frame.copy()
    safe.columns = [neutralize(str(column)) for column in safe.columns]
    for column in safe.columns:
        dtype = safe[column].dtype
        if pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
            safe[column] = safe[column].astype(object).map(neutralize)
    for column in safe.select_dtypes(include=["datetimetz"]).columns:
        safe[column] = safe[column].dt.tz_localize(None)
    return safe


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    # UTF-8 with BOM so that Excel opens Uzbek/Russian text correctly
    return sanitize(frame).to_csv(index=False).encode("utf-8-sig")


def to_xlsx_bytes(frame: pd.DataFrame, sheet_name: str = "Ma'lumot") -> bytes:
    buffer = BytesIO()
    safe = sanitize(frame)
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        safe.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        sheet = writer.sheets[sheet_name[:31]]
        for index, column in enumerate(safe.columns, start=1):
            width = min(60, max(10, int(safe[column].astype(str).str.len().quantile(0.9) if len(safe) else 10) + 2,
                                len(str(column)) + 2))
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
    return buffer.getvalue()
