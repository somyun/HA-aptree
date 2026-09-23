"""Isolated APTREE website fetcher used by a short-lived child process."""

from __future__ import annotations

import json
import sys
from http.cookiejar import CookieJar
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from html_parser import parse_monthly_bill

REQUEST_TIMEOUT = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _request(opener, method: str, url: str, data: dict[str, str]) -> str:
    request = Request(
        url,
        data=urlencode(data).encode(),
        headers={"User-Agent": "HomeAssistant-HA-aptree/0.8.0"},
        method=method,
    )
    with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
        charset = response.headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def main() -> int:
    """Read credentials from stdin and emit only parsed billing JSON."""
    try:
        payload = json.load(sys.stdin)
        community = str(payload["community"])
        month = str(payload["month"])
        base = f"https://aptree.co.kr/home/user/{community}"
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        login = _request(
            opener,
            "POST",
            f"{base}/member/login.php",
            {
                "login_id": str(payload["username"]),
                "login_pw": str(payload["password"]),
                "login_id_save": "",
                "login_auto": "",
                "width": "1920",
                "height": "1080",
            },
        )
        if login.strip().lower() != "ok":
            raise PermissionError("authentication_failed")
        html = _request(
            opener,
            "POST",
            f"{base}/lib/cac.load_content.php",
            {"date": month},
        )
        result = parse_monthly_bill(html, month)
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        return 0
    except Exception as err:
        print(type(err).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())