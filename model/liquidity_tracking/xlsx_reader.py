from __future__ import annotations

import math
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def column_number(name: str) -> int:
    result = 0
    for char in name.upper():
        if not "A" <= char <= "Z":
            raise ValueError(f"Invalid Excel column: {name}")
        result = result * 26 + ord(char) - 64
    return result


def column_name(number: int) -> str:
    if number < 1:
        raise ValueError("Excel columns are one-based")
    result = ""
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result


def cell_parts(address: str) -> tuple[int, int]:
    match = re.fullmatch(r"\$?([A-Za-z]+)\$?(\d+)", address.strip())
    if not match:
        raise ValueError(f"Invalid cell address: {address}")
    return int(match.group(2)), column_number(match.group(1))


def range_parts(address: str) -> tuple[int, int, int, int]:
    start, _, end = address.partition(":")
    start_row, start_col = cell_parts(start)
    end_row, end_col = cell_parts(end or start)
    return min(start_row, end_row), min(start_col, end_col), max(start_row, end_row), max(start_col, end_col)


def _normalize(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def _rels_name(part: str) -> str:
    p = PurePosixPath(part)
    return str(p.parent / "_rels" / f"{p.name}.rels")


def _relationships(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    rels_part = _rels_name(part)
    if rels_part not in archive.namelist():
        return {}
    root = ET.fromstring(archive.read(rels_part))
    output: dict[str, str] = {}
    for rel in root.findall("pr:Relationship", NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            output[rel_id] = _normalize(part, target)
    return output


def _clean_number(raw: str) -> int | float:
    value = float(raw)
    return int(value) if value.is_integer() else value


def _excel_datetime(value: float, date_1904: bool) -> str:
    epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
    result = epoch + timedelta(days=float(value))
    if result.time() == datetime.min.time():
        return result.date().isoformat()
    return result.isoformat(timespec="seconds")


def _looks_like_date_format(code: str) -> bool:
    stripped = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code.lower())
    return bool(re.search(r"(^|[^a-z])[ymdhis]+([^a-z]|$)", stripped))


@dataclass(frozen=True)
class CellValue:
    value: Any
    formula: str | None = None
    error: str | None = None


class XlsxReader:
    """Small read-only XLSX reader focused on cached formula values and date fidelity."""

    def __init__(self, workbook: str | Path):
        self.path = Path(workbook).resolve()
        self.archive = zipfile.ZipFile(self.path)
        self.sheet_parts: dict[str, str] = {}
        self.shared_strings: list[str] = []
        self.date_styles: set[int] = set()
        self.date_1904 = False
        self._load_metadata()

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> "XlsxReader":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _load_metadata(self) -> None:
        workbook_part = "xl/workbook.xml"
        root = ET.fromstring(self.archive.read(workbook_part))
        properties = root.find("main:workbookPr", NS)
        self.date_1904 = bool(properties is not None and properties.attrib.get("date1904") in {"1", "true"})
        rels = _relationships(self.archive, workbook_part)
        for sheet in root.findall("main:sheets/main:sheet", NS):
            rel_id = sheet.attrib.get(f"{{{NS['r']}}}id", "")
            part = rels.get(rel_id)
            if part:
                self.sheet_parts[sheet.attrib.get("name", "")] = part
        if "xl/sharedStrings.xml" in self.archive.namelist():
            strings = ET.fromstring(self.archive.read("xl/sharedStrings.xml"))
            for item in strings.findall("main:si", NS):
                self.shared_strings.append("".join(node.text or "" for node in item.findall(".//main:t", NS)))
        if "xl/styles.xml" in self.archive.namelist():
            self._load_date_styles(ET.fromstring(self.archive.read("xl/styles.xml")))

    def _load_date_styles(self, root: ET.Element) -> None:
        builtin_dates = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
        custom: dict[int, str] = {}
        for fmt in root.findall("main:numFmts/main:numFmt", NS):
            try:
                custom[int(fmt.attrib.get("numFmtId", "0"))] = fmt.attrib.get("formatCode", "")
            except ValueError:
                continue
        xfs = root.find("main:cellXfs", NS)
        if xfs is None:
            return
        for index, xf in enumerate(xfs.findall("main:xf", NS)):
            try:
                fmt_id = int(xf.attrib.get("numFmtId", "0"))
            except ValueError:
                continue
            if fmt_id in builtin_dates or _looks_like_date_format(custom.get(fmt_id, "")):
                self.date_styles.add(index)

    def _decode_cell(self, cell: ET.Element) -> CellValue:
        cell_type = cell.attrib.get("t", "n")
        style = int(cell.attrib.get("s", "0") or 0)
        formula_node = cell.find("main:f", NS)
        formula = formula_node.text if formula_node is not None else None
        if cell_type == "inlineStr":
            value = "".join(node.text or "" for node in cell.findall(".//main:t", NS))
            return CellValue(value=value, formula=formula)
        value_node = cell.find("main:v", NS)
        if value_node is None or value_node.text is None:
            return CellValue(value=None, formula=formula)
        raw = value_node.text
        if cell_type == "s":
            try:
                return CellValue(value=self.shared_strings[int(raw)], formula=formula)
            except (ValueError, IndexError):
                return CellValue(value=None, formula=formula, error=f"bad_shared_string:{raw}")
        if cell_type == "b":
            return CellValue(value=raw == "1", formula=formula)
        if cell_type == "e":
            return CellValue(value=None, formula=formula, error=raw)
        if cell_type in {"str", "d"}:
            return CellValue(value=raw, formula=formula)
        try:
            number = _clean_number(raw)
            if style in self.date_styles and math.isfinite(float(number)):
                return CellValue(value=_excel_datetime(float(number), self.date_1904), formula=formula)
            return CellValue(value=number, formula=formula)
        except ValueError:
            return CellValue(value=raw, formula=formula)

    def read_cells(self, sheet: str, addresses: Iterable[str]) -> dict[str, CellValue]:
        targets = {address.replace("$", "").upper() for address in addresses}
        if sheet not in self.sheet_parts:
            raise KeyError(f"Sheet not found: {sheet}")
        output: dict[str, CellValue] = {}
        with self.archive.open(self.sheet_parts[sheet]) as source:
            for _, element in ET.iterparse(source, events=("end",)):
                if element.tag.rsplit("}", 1)[-1] == "c":
                    address = element.attrib.get("r", "").upper()
                    if address in targets:
                        output[address] = self._decode_cell(element)
                        if len(output) == len(targets):
                            element.clear()
                            break
                    element.clear()
        return output

    def read_range(self, sheet: str, address: str, include_formula: bool = False) -> list[list[Any]]:
        min_row, min_col, max_row, max_col = range_parts(address)
        targets = {
            f"{column_name(col)}{row}"
            for row in range(min_row, max_row + 1)
            for col in range(min_col, max_col + 1)
        }
        cells = self.read_cells(sheet, targets)
        rows: list[list[Any]] = []
        for row in range(min_row, max_row + 1):
            values: list[Any] = []
            for col in range(min_col, max_col + 1):
                cell = cells.get(f"{column_name(col)}{row}", CellValue(None))
                values.append(
                    {"value": cell.value, "formula": cell.formula, "error": cell.error}
                    if include_formula
                    else cell.value
                )
            rows.append(values)
        return rows

    def read_rect(self, sheet: str, min_row: int, min_col: int, max_row: int, max_col: int) -> list[list[Any]]:
        return self.read_range(sheet, f"{column_name(min_col)}{min_row}:{column_name(max_col)}{max_row}")
