"""
USDA reefer-availability data acquisition.

This file is the ONLY place that touches the USDA API. It is deliberately
isolated so the API contract is independent of the chart-generation logic.

Source: USDA AMS Specialty Crops Program via Socrata at agtransport.usda.gov,
dataset acar-e3r8 — "Refrigerated Truck Rates and Availability". It carries
the weekly 1-5 truck availability scale (1=Surplus -> 5=Shortage) by origin
region, which is what the map consumes.

`fetch_data()` returns a pandas DataFrame with AT LEAST these columns:
    Week (int), Month (int), Quarter (int), Year (int),
    Region (str), Availability (float)

`Region` is already bucketed into the canonical USDA AMS shipping regions
(Indiana -> Great Lakes, Pennsylvania -> Mid-Atlantic, and so on) because the
heat map colors whole shipping districts, not individual reporting origins.

A CSV fallback is retained for local runs without network. Set DATA_SOURCE=api
(the workflow does this by default) to use the live Socrata API.
"""
from __future__ import annotations
import os
import sys
import time
from datetime import date
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Toggle: "api" hits the Socrata endpoint, anything else falls back to the
# bundled CSV. Controlled by env var DATA_SOURCE so the workflow can flip
# it without a code change.
# ---------------------------------------------------------------------------
DATA_SOURCE = os.environ.get("DATA_SOURCE", "csv").strip().lower()

# App token is OPTIONAL — the dataset is public; the token only raises rate
# limits. In GitHub it is mapped from the SOCRATA_APP_TOKEN repo secret.
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN") or None

# Path to the committed CSV fallback (relative to repo root).
CSV_FALLBACK_PATH = os.environ.get(
    "CSV_FALLBACK_PATH", "data/refrigerated_truck_rates_and_availability.csv"
)

REQUIRED_COLUMNS = ["Week", "Month", "Quarter", "Year", "Region", "Availability"]

# Socrata dataset: Refrigerated Truck Rates and Availability.
#   https://agtransport.usda.gov/Truck/Refrigerated-Truck-Rates-and-Availability/acar-e3r8/data
SOCRATA_DOMAIN = "agtransport.usda.gov"
DATASET_ID = "acar-e3r8"
API_URL = f"https://{SOCRATA_DOMAIN}/resource/{DATASET_ID}.json"
PAGE_SIZE = 10_000

# The chart reports a Q4 (Oct-Dec) four-year average.
QUARTER = 4
QUARTER_MONTHS = (10, 11, 12)
N_YEARS = 4


def latest_complete_year(today: date | None = None) -> int:
    """Most recent year whose Q4 has finished.

    Q4 of year Y only closes on 31 Dec, so at any point during year Y the
    newest complete Q4 is Y-1. Pinning the window this way keeps a mid-quarter
    rebuild from averaging in a partial December.
    """
    today = today or date.today()
    return today.year - 1


def year_window(today: date | None = None) -> tuple[int, int]:
    """(first, last) year of the rolling N_YEARS Q4 window.

    Override with YEAR_MIN / YEAR_MAX env vars to rebuild a fixed historical
    window (useful for reproducing a published chart)."""
    last = int(os.environ.get("YEAR_MAX") or latest_complete_year(today))
    first = int(os.environ.get("YEAR_MIN") or (last - (N_YEARS - 1)))
    if first > last:
        raise ValueError(f"YEAR_MIN ({first}) is after YEAR_MAX ({last}).")
    return first, last


YEAR_MIN, YEAR_MAX = year_window()
YEARS = list(range(YEAR_MIN, YEAR_MAX + 1))


_GREAT_LAKES = {"GREAT LAKES", "MICHIGAN", "WISCONSIN", "MINNESOTA",
                "OHIO", "INDIANA", "ILLINOIS"}
_MID_ATLANTIC = {"MID-ATLANTIC", "PENNSYLVANIA", "NEW JERSEY", "DELAWARE",
                 "MARYLAND", "VIRGINIA", "WEST VIRGINIA"}
_SOUTHEAST = {"SOUTHEAST", "NORTH CAROLINA", "SOUTH CAROLINA", "GEORGIA",
              "ALABAMA", "TENNESSEE", "KENTUCKY"}
_PNW = {"PNW", "PACIFIC NORTHWEST", "WASHINGTON", "OREGON", "IDAHO"}

# Canonical USDA AMS shipping regions, in the order the map legend lists them.
# Anything normalize_region() cannot place (Canada, "OTHER", blank) is dropped
# rather than guessed at — a mis-bucketed origin would silently recolor a
# whole district.
KNOWN_REGIONS: list[str] = [
    "Arizona", "California", "Colorado", "Florida", "Great Lakes",
    "Mexico-Arizona", "Mexico-California", "Mexico-New Mexico", "Mexico-Texas",
    "Mid-Atlantic", "New York", "PNW", "Southeast", "Texas",
]


def normalize_region(raw: str) -> str | None:
    """Map raw Socrata region strings to the canonical names the chart uses.
    Returns None for rows whose region we don't recognize so the caller can
    drop them."""
    u = (raw or "").strip().upper()
    if not u:
        return None
    if u.startswith("MEXICO-CALIFORNIA"):
        return "Mexico-California"
    if u.startswith("MEXICO-ARIZONA"):
        return "Mexico-Arizona"
    if u.startswith("MEXICO-TEXAS"):
        return "Mexico-Texas"
    if u.startswith("MEXICO-NEW MEXICO") or u.startswith("MEXICO-NM"):
        return "Mexico-New Mexico"
    if u.startswith("CALIFORNIA"):
        return "California"
    if u in _PNW:
        return "PNW"
    if u == "ARIZONA":
        return "Arizona"
    if u == "COLORADO":
        return "Colorado"
    if u == "FLORIDA":
        return "Florida"
    if u == "NEW YORK":
        return "New York"
    if u == "TEXAS":
        return "Texas"
    if u in _GREAT_LAKES:
        return "Great Lakes"
    if u in _MID_ATLANTIC:
        return "Mid-Atlantic"
    if u in _SOUTHEAST:
        return "Southeast"
    return None


def _get_with_retry(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    max_retries: int = 5,
) -> requests.Response:
    retryable = (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.ChunkedEncodingError,
    )
    for attempt in range(max_retries + 1):
        try:
            return requests.get(url, params=params, headers=headers, timeout=timeout)
        except retryable as e:
            if attempt == max_retries:
                raise
            backoff = 2 ** (attempt + 1)
            print(
                f"    {type(e).__name__} on attempt {attempt + 1}/{max_retries + 1}; "
                f"retrying in {backoff}s ...",
                file=sys.stderr, flush=True,
            )
            time.sleep(backoff)
    raise RuntimeError("unreachable")


def _months_from(raw: pd.DataFrame) -> pd.Series:
    """Reporting month per row.

    Prefer the dataset's own `month` column: it is the month AMS attributes the
    marketing week to, which is NOT always the calendar month of `date`. A week
    that opens in late December carries month 12 on a January date, and 572 of
    the Q4 2022-2025 rows disagree this way. Deriving the month from `date`
    would drop those weeks out of Q4 entirely.

    Parsing `date` is only the fallback, in case the column is ever renamed.
    """
    if "month" in raw.columns:
        months = pd.to_numeric(raw["month"], errors="coerce")
        if months.notna().any():
            return months
    if "date" in raw.columns:
        return pd.to_datetime(raw["date"], errors="coerce").dt.month
    raise ValueError(
        "Socrata response has neither a `month` nor a `date` column; cannot "
        f"derive the Oct/Nov/Dec split. Columns: {list(raw.columns)}"
    )


def fetch_from_api() -> pd.DataFrame:
    """Pull live Q4 data from the USDA AMS Socrata dataset acar-e3r8.

    Returns a flat DataFrame: one row per source observation, with the
    canonical Week/Month/Quarter/Year/Region/Availability columns."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN

    rows: list[dict[str, Any]] = []
    offset = 0
    # Server-side filter — quarter and year are their own columns, so Socrata
    # drops ~96% of the table before it hits the wire.
    where = f"quarter = {QUARTER} AND year BETWEEN {YEAR_MIN} AND {YEAR_MAX}"
    while True:
        params: dict[str, Any] = {
            "$limit": PAGE_SIZE,
            "$offset": offset,
            "$order": ":id",
            "$where": where,
        }
        print(f"[fetch_data] page offset={offset:>7} ",
              end="", file=sys.stderr, flush=True)
        r = _get_with_retry(API_URL, params=params, headers=headers, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(
                f"Socrata HTTP {r.status_code} for {DATASET_ID}: {r.text[:300]}"
            )
        batch = r.json()
        print(f"({len(batch):>5} rows)", file=sys.stderr)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += len(batch)
        if offset > 5_000_000:
            print("[fetch_data] safety cap hit; stopping.", file=sys.stderr)
            break

    if not rows:
        raise ValueError(
            f"Socrata returned 0 rows for {DATASET_ID} in Q{QUARTER} "
            f"{YEAR_MIN}-{YEAR_MAX}. Check that the dataset is still published "
            f"at {API_URL}."
        )

    raw = pd.DataFrame(rows)
    df = pd.DataFrame({
        "Week": pd.to_numeric(raw.get("week"), errors="coerce").astype("Int64"),
        "Month": pd.to_numeric(_months_from(raw), errors="coerce").astype("Int64"),
        "Quarter": pd.to_numeric(raw.get("quarter"), errors="coerce").astype("Int64"),
        "Year": pd.to_numeric(raw.get("year"), errors="coerce").astype("Int64"),
        "Region": raw["region"].map(normalize_region),
        "Availability": pd.to_numeric(raw.get("availability"), errors="coerce"),
    })
    return _normalize(df)


def fetch_from_csv() -> pd.DataFrame:
    """Fallback: read the committed CSV export, so the pipeline still runs
    (and CI still fails loudly on a schema change) without network access."""
    if not os.path.exists(CSV_FALLBACK_PATH):
        raise FileNotFoundError(
            f"CSV fallback not found at {CSV_FALLBACK_PATH}. Either commit the "
            f"CSV there, or set DATA_SOURCE=api."
        )
    raw = pd.read_csv(CSV_FALLBACK_PATH)
    df = pd.DataFrame({
        "Week": pd.to_numeric(raw.get("Week"), errors="coerce").astype("Int64"),
        "Month": pd.to_numeric(raw.get("Month"), errors="coerce").astype("Int64"),
        "Quarter": pd.to_numeric(raw.get("Quarter"), errors="coerce").astype("Int64"),
        "Year": pd.to_numeric(raw.get("Year"), errors="coerce").astype("Int64"),
        "Region": raw.get("Region", pd.Series(dtype=str)).map(normalize_region),
        "Availability": pd.to_numeric(raw.get("Availability"), errors="coerce"),
    })
    return _normalize(df)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce to the canonical schema and clip to the Q4 window, regardless of
    source. This is the contract the rest of the pipeline depends on — keep it
    strict so a bad API response fails loudly here, not silently in the map."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Data is missing required columns {missing}. "
            f"Got columns: {list(df.columns)}. "
            f"Adjust the parsing in fetch_from_api() to map to: "
            f"{REQUIRED_COLUMNS}"
        )
    out = df[REQUIRED_COLUMNS].copy()
    for col in ["Week", "Month", "Quarter", "Year"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    out["Availability"] = pd.to_numeric(out["Availability"], errors="coerce")
    out = out.dropna(subset=["Month", "Quarter", "Year", "Region", "Availability"])

    out = out[out["Quarter"] == QUARTER]
    out = out[out["Month"].isin(QUARTER_MONTHS)]
    out = out[out["Year"].between(YEAR_MIN, YEAR_MAX)]
    # The published scale is an integer 1-5; anything outside it is a data
    # error, not a reading.
    out = out[out["Availability"].between(1, 5)]

    if out.empty:
        raise ValueError(
            f"After normalization no Q{QUARTER} {YEAR_MIN}-{YEAR_MAX} rows "
            f"remain — check the source/parsing."
        )
    return out


def fetch_data() -> pd.DataFrame:
    """Single entry point used by build_chart.py."""
    src = "api" if DATA_SOURCE == "api" else "csv"
    print(f"[fetch_data] source = {src}, Q{QUARTER} {YEAR_MIN}-{YEAR_MAX}",
          file=sys.stderr)
    df = fetch_from_api() if src == "api" else fetch_from_csv()
    print(f"[fetch_data] {len(df):,} rows, "
          f"years {int(df.Year.min())}-{int(df.Year.max())}, "
          f"{df.Region.nunique()} regions", file=sys.stderr)
    return df


if __name__ == "__main__":
    # Quick local smoke test: python scripts/fetch_data.py
    d = fetch_data()
    print(d.head())
    print(d.dtypes)
