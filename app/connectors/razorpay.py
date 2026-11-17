"""Live mode: pull the settlement recon straight from Razorpay instead of an uploaded file.

Writes the same columns the upload uses (app.sample.RP_COLUMNS), so the loader, engine and
everything after it run unchanged: a new source, not a new pipeline.

    items = fetch_recon(key_id, key_secret, 2026, 10)
    write_csv(items, Path("razorpay_settlements.csv"))
"""

import calendar
import csv
import json
from pathlib import Path

import httpx

from app.sample import RP_COLUMNS

URL = "https://api.razorpay.com/v1/settlements/recon/combined"
PAGE = 1000


class ConnectorError(Exception):
    """Razorpay couldn't be read. Message is user-facing."""


def fetch_recon(
    key_id: str, key_secret: str, year: int, month: int, client: httpx.Client | None = None
) -> list[dict]:
    """Every recon item settled in the month, one day at a time, paging by `skip`."""
    client = client or httpx.Client(timeout=20)
    items: list[dict] = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        skip = 0
        while True:
            params = {"year": year, "month": month, "day": day, "count": PAGE, "skip": skip}
            try:
                r = client.get(URL, params=params, auth=(key_id, key_secret))
            except httpx.HTTPError as e:
                raise ConnectorError(f"Couldn't reach Razorpay: {e}") from e
            if r.status_code == 401:
                raise ConnectorError("Razorpay rejected the API key. Check the key id and secret.")
            if r.status_code == 429:
                raise ConnectorError("Razorpay is rate limiting requests. Try again in a minute.")
            if r.status_code != 200:
                detail = r.json().get("error", {}).get("description", r.text[:200])
                raise ConnectorError(f"Razorpay returned {r.status_code}: {detail}")
            page = r.json().get("items", [])
            items += page
            if len(page) < PAGE:
                break
            skip += PAGE
    return items


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dict | list):
        return json.dumps(v)
    return str(v)


def write_csv(items: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(RP_COLUMNS)
        for it in items:
            w.writerow([_cell(it.get(c)) for c in RP_COLUMNS])
