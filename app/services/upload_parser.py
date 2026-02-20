# app/services/upload_parser.py
from __future__ import annotations

import csv
from io import BytesIO
from typing import Any, Dict, List

import openpyxl


def normalize_header(h: Any) -> str:
    return str(h or "").strip().replace("\ufeff", "").strip()


def _guess_delimiter(sample: str) -> str:
    # tenta detectar ; ou ,
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        return dialect.delimiter
    except Exception:
        return ";" if sample.count(";") > sample.count(",") else ","


def parse_csv_bytes(raw: bytes, limit: int = 50000) -> List[Dict[str, Any]]:
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:2048]
    delim = _guess_delimiter(sample)

    reader = csv.DictReader(text.splitlines(), delimiter=delim)
    rows: List[Dict[str, Any]] = []

    for i, row in enumerate(reader, start=1):
        if i > limit:
            break
        clean = {normalize_header(k): (v if v is not None else "") for k, v in (row or {}).items()}
        if any(str(v).strip() for v in clean.values()):
            rows.append(clean)

    return rows


def parse_xlsx_bytes(raw: bytes, limit: int = 50000) -> List[Dict[str, Any]]:
    wb = openpyxl.load_workbook(BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active

    it = ws.iter_rows(values_only=True)
    try:
        headers_raw = next(it)
    except StopIteration:
        return []

    headers = [normalize_header(h) for h in (headers_raw or [])]

    rows: List[Dict[str, Any]] = []
    for idx, values in enumerate(it, start=1):
        if idx > limit:
            break

        row_dict: Dict[str, Any] = {}
        for col_i, val in enumerate(values):
            if col_i >= len(headers):
                continue
            key = headers[col_i]
            if not key:
                continue
            row_dict[key] = "" if val is None else str(val).strip()

        if any(str(v).strip() for v in row_dict.values()):
            rows.append(row_dict)

    return rows


def parse_upload_file(filename: str, raw: bytes, limit: int = 50000) -> List[Dict[str, Any]]:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return parse_csv_bytes(raw, limit=limit)
    if name.endswith(".xlsx"):
        return parse_xlsx_bytes(raw, limit=limit)
    raise ValueError("Formato inválido. Envie CSV ou XLSX.")