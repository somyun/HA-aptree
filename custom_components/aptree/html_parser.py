"""Dependency-free HTML parsers for APTREE resident pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node"] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    @property
    def text(self) -> str:
        return _clean(" ".join([*self.fragments, *(child.text for child in self.children)]))

    def descendants(self, *, tag: str | None = None, cls: str | None = None):
        for child in self.children:
            if (tag is None or child.tag == tag) and (cls is None or cls in child.classes):
                yield child
            yield from child.descendants(tag=tag, cls=cls)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)
        if tag.lower() not in {"br", "img", "input", "meta", "link", "hr"}:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key: value or "" for key, value in attrs})
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].fragments.append(data)


def _tree(html: str) -> _Node:
    parser = _TreeParser()
    parser.feed(html)
    return parser.root


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("　", " ")).strip()


def _number(value: str) -> int | float | None:
    match = re.search(r"-?[\d,.]+", value.replace("–", ""))
    if not match:
        return None
    number = float(match.group(0).replace(",", ""))
    return int(number) if number.is_integer() else number


def _first(node: _Node, *, tag: str | None = None, cls: str | None = None):
    return next(node.descendants(tag=tag, cls=cls), None)


def _rows(table: _Node) -> list[list[str]]:
    result: list[list[str]] = []
    for row in table.descendants(tag="tr"):
        cells = [child.text for child in row.children if child.tag in {"th", "td"}]
        if cells:
            result.append(cells)
    return result


def _item_rows(area: _Node) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in area.descendants(tag="li"):
        label = _first(row, cls="text")
        fee = _first(row, cls="fee")
        if label is None or fee is None:
            continue
        title = _clean(label.text)
        if title == "생활폐기물수수":
            title = "음식물쓰레기 수수료"
        items.append({"title": title, "amount": _number(fee.text)})
    return items


def parse_monthly_bill(html: str, billing_month: str) -> dict[str, Any]:
    """Parse the HTML fragment returned by cac.load_content.php."""
    root = _tree(html)
    sections = list(root.descendants(cls="con"))
    if len(sections) < 3:
        raise ValueError("monthly bill sections are missing")

    grouped: dict[str, list[dict[str, Any]]] = {
        "discountList": [], "electricityList": [], "waterList": [], "etcList": []
    }
    bill_rows: list[dict[str, Any]] = []
    active_key: str | None = None
    title_to_key = {
        "할인합계": "discountList", "전기료": "electricityList",
        "수도료": "waterList", "관리비": "etcList",
    }
    for section_index, section in enumerate(sections):
        for child in section.children:
            if "tit_area" in child.classes:
                active_key = next(
                    (key for prefix, key in title_to_key.items() if prefix in child.text), None
                )
            elif "detail_area" in child.classes:
                items = _item_rows(child)
                if section_index == 0:
                    bill_rows.extend(items)
                elif active_key:
                    grouped[active_key].extend(items)

    total = next((i["amount"] for i in bill_rows if i["title"] == "당월부과금"), None)
    if total is None:
        raise ValueError("monthly total is missing")

    def comparison(items: list[dict[str, Any]], unit: str) -> dict[str, Any]:
        household = next((i for i in items if i["title"].startswith("세대 ")), {})
        match = re.search(rf"세대\s+([\d,.]+)\s*{unit}\b", household.get("title", ""), re.I)
        return {
            "usage": _number(match.group(1)) if match else None,
            "amount": household.get("amount"),
        }

    month = "".join(c for c in billing_month if c.isdigit())[:6]
    if len(month) != 6:
        raise ValueError("billing month is invalid")
    return {
        "billingMonth": month,
        "targetMonth": f"{month[:4]}-{month[4:]}-01",
        "totalAmount": {"amount": total},
        "unpaidAmount": {"amount": next((i["amount"] for i in bill_rows if i["title"] == "미납관리비"), 0)},
        "lateFee": {"amount": next((i["amount"] for i in bill_rows if i["title"] in {"미납연체료", "연체료"}), 0)},
        **grouped,
        "electricityComparison": comparison(grouped["electricityList"], "k"),
        "waterComparison": comparison(grouped["waterList"], "t"),
        "trashComparison": {"amount": next((i["amount"] for i in grouped["etcList"] if i["title"] == "음식물쓰레기 수수료"), None)},
    }


def parse_analysis_page(html: str) -> dict[str, Any]:
    """Parse usage history and newest same-area comparisons."""
    root = _tree(html)
    usage_rows: list[list[str]] | None = None
    comparison_rows: list[list[str]] | None = None
    for table in root.descendants(tag="table"):
        rows = _rows(table)
        if not rows:
            continue
        header = " ".join(rows[0])
        if "년월" in header and "전기" in header and "수도" in header:
            usage_rows = rows[1:]
        elif "동일면적 평균 사용량" in header:
            comparison_rows = rows[1:]
    if not usage_rows:
        raise ValueError("usage history table is missing")

    history: list[dict[str, Any]] = []
    for cells in usage_rows:
        if len(cells) < 3:
            continue
        month = "".join(c for c in cells[0] if c.isdigit())[:6]
        if len(month) == 6:
            history.append({"month": month, "electricityUsage": _number(cells[1]), "waterUsage": _number(cells[2])})
    history.sort(key=lambda item: item["month"])
    if not history:
        raise ValueError("usage history rows are missing")
    latest_month = history[-1]["month"]

    comparisons: dict[str, dict[str, Any]] = {}
    total_amount = None
    for cells in comparison_rows or []:
        if len(cells) < 3:
            continue
        if cells[0] == "전기":
            comparisons["electricityComparison"] = {"usage": _number(cells[1]), "averageUsage": _number(cells[2])}
        elif cells[0] == "수도":
            comparisons["waterComparison"] = {"usage": _number(cells[1]), "averageUsage": _number(cells[2])}
        elif cells[0] == "관리비":
            total_amount = _number(cells[1])
            comparisons["sameAreaAverage"] = _number(cells[2])

    return {
        "billingMonth": latest_month,
        "targetMonth": f"{latest_month[:4]}-{latest_month[4:]}-01",
        "yearlyAmountList": [{"month": item["month"], "amount": None} for item in history],
        "usageHistory": history,
        "totalAmount": {"amount": total_amount},
        **comparisons,
    }
