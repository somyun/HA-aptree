"""Dependency-free extraction of numeric cells from the bill PDF."""

from __future__ import annotations

import re
import zlib
from typing import Any

MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_STREAMS = 400
COLUMN_GAP = -500

_STREAM = re.compile(rb"(?<!end)stream\r?\n")
_SHOW = re.compile(rb"\(((?:[^()\\]|\\.)*)\)|(-?\d+(?:\.\d+)?|-?\.\d+)", re.S)
_TOKEN = re.compile(
    rb"\((?:[^()\\]|\\.)*\)"
    rb"|\[(?:\((?:[^()\\]|\\.)*\)|[^\]()])*\]"
    rb"|[^\s()\[\]]+",
    re.S,
)
_OCTAL = re.compile(rb"\\([0-7]{1,3})")
_ESCAPE = re.compile(rb"\\(.)", re.S)

Run = tuple[float, float, str]


def _unescape(value: bytes) -> str:
    value = _OCTAL.sub(lambda match: bytes([int(match.group(1), 8) & 255]), value)
    return _ESCAPE.sub(rb"\1", value).decode("latin-1")


def _show(operand: bytes) -> str:
    """Decode a Tj/TJ operand, keeping column gaps as spaces."""
    parts: list[str] = []
    for match in _SHOW.finditer(operand):
        if match.group(1) is not None:
            parts.append(_unescape(match.group(1)))
        elif float(match.group(2)) <= COLUMN_GAP:
            parts.append(" ")
    return "".join(parts)


def extract_text(data: bytes) -> list[list[Run]]:
    """Return positioned text runs for each page content stream."""
    if not data.startswith(b"%PDF-"):
        raise ValueError("not a PDF")
    pages: list[list[Run]] = []
    for index, match in enumerate(_STREAM.finditer(data)):
        if index >= MAX_STREAMS:
            break
        try:
            content = zlib.decompressobj().decompress(
                data[match.end() :], MAX_STREAM_BYTES
            )
        except zlib.error:
            continue
        if b"BT" in content and b"Tj" in content and b" Tm" in content:
            pages.append(_text_runs(content))
    return pages


def _text_runs(content: bytes) -> list[Run]:
    runs: list[Run] = []
    stack: list[bytes] = []
    a, d, x, y, leading = 1.0, 1.0, 0.0, 0.0, 0.0
    for match in _TOKEN.finditer(content):
        token = match.group(0)
        if token[:1] in b"([+-.0123456789/":
            stack.append(token)
            del stack[:-16]
            continue
        try:
            if token == b"Tm":
                values = [float(item) for item in stack[-6:]]
                a, d, x, y = values[0], values[3], values[4], values[5]
            elif token in (b"Td", b"TD"):
                tx, ty = float(stack[-2]), float(stack[-1])
                x, y = x + tx * a, y + ty * d
                if token == b"TD":
                    leading = -ty
            elif token == b"TL":
                leading = float(stack[-1])
            elif token == b"T*":
                y -= leading * d
            elif token in (b"Tj", b"TJ"):
                text = _show(stack[-1])
                if text.strip():
                    runs.append((round(x, 1), round(y, 1), text.strip()))
        except (ValueError, IndexError):
            pass
        stack.clear()
    return runs


def _numbers(text: str) -> list[int | float]:
    values: list[int | float] = []
    for part in re.findall(r"-?(?:\d[\d,]*(?:\.\d+)?|\.\d+)", text):
        number = float(part.replace(",", ""))
        values.append(int(number) if number.is_integer() else number)
    return values


def _row(runs: list[Run], y: float, x1: float, x2: float) -> list[int | float]:
    cells = sorted(run for run in runs if abs(run[1] - y) <= 1.5 and x1 <= run[0] <= x2)
    return [number for cell in cells for number in _numbers(cell[2])]


def _bracketed(
    runs: list[Run], y: float, x1: float, x2: float
) -> list[int | float]:
    """Return the compared values; the bill prints them in parentheses."""
    cells = sorted(
        run
        for run in runs
        if abs(run[1] - y) <= 1.5
        and x1 <= run[0] <= x2
        and re.fullmatch(r"\([-\d,.]+\)", run[2])
    )
    return [number for cell in cells for number in _numbers(cell[2])]


def _comparison(
    runs: list[Run], x1: float, x2: float, y: float
) -> dict[str, int | float]:
    values = _bracketed(runs, y, x1, x2)
    if len(values) != 2:
        return {}
    return {"averageUsage": values[0], "averageAmount": values[1]}


def parse_bill(data: bytes) -> dict[str, Any]:
    """Read the values that only the printed bill contains.

    Labels use subset fonts without a Unicode map, so cells are located by the
    fixed layout of the form. Coordinates were verified against several bills;
    anything that does not match is omitted rather than guessed.
    """
    pages = extract_text(data)
    if len(pages) < 2:
        raise ValueError("bill pages are missing")
    front, back = pages[0], pages[1]
    title = _row(front, 549.9, 25, 100)
    total = _row(back, 509.4, 290, 380)
    if len(title) != 2 or not 1 <= title[1] <= 12 or len(total) != 1:
        raise ValueError("bill layout is not recognized")

    result: dict[str, Any] = {
        "month": f"{int(title[0]):04d}-{int(title[1]):02d}",
        "total": total[0],
    }
    for key, values in (
        ("sameAreaAverage", _bracketed(back, 500.0, 440, 520)),
        ("sameAreaMax", _row(back, 530.1, 535, 600)),
        ("sameAreaMin", _row(back, 515.9, 535, 600)),
    ):
        if len(values) == 1:
            result[key] = values[0]
    for key, x1, x2, y in (
        ("electricity", 170, 290, 320.3),
        ("water", 440, 570, 320.3),
        ("heating", 160, 290, 143.7),
    ):
        if comparison := _comparison(back, x1, x2, y):
            result[key] = comparison
    readings = {}
    for key, y in (
        ("electricity", 106.3),
        ("water", 90.4),
        ("hot_water", 58.7),
        ("heating", 42.8),
    ):
        values = _row(front, y, 60, 170)
        if len(values) == 2 and values[0] >= values[1]:
            readings[key] = {"current": values[0], "previous": values[1]}
    if readings:
        result["readings"] = readings
    fees = _row(front, 438.5, 140, 300)
    if len(fees) == 2:
        result["climate_fee"], result["fuel_adjustment"] = fees
    return result
