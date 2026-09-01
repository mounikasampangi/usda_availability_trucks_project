"""
Builds the Q4 reefer truck availability heat map as index.html.

Pulls Q4 (Oct-Dec) availability readings for the rolling four-year window via
scripts/fetch_data.py, averages them by shipping region / year / month, and
injects the result into heatmap_template.html.

The published page re-fetches Socrata itself on load, so this baked snapshot is
the fallback that keeps the map readable if that request fails (offline, a
corporate network blocking the endpoint, or a Socrata outage).
"""
from __future__ import annotations
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# fetch_data lives next to this file; support both `python scripts/build_chart.py`
# and `python -m scripts.build_chart`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_data as fd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "heatmap_template.html"
DEFAULT_OUTPUT = REPO_ROOT / "index.html"

# Month bucket keys, in calendar order. "all" is the whole-quarter rollup.
MONTH_KEYS = {10: "oct", 11: "nov", 12: "dec"}


def _stat(values: pd.Series) -> dict:
    """Mean availability and sample size for one region/period cell.

    n is carried through to the tooltip: several regions report only a handful
    of Q4 lanes, and a 2-observation average should not read the same as a
    600-observation one.
    """
    n = int(values.notna().sum())
    if n == 0:
        return {"mean": None, "n": 0}
    return {"mean": round(float(values.mean()), 3), "n": n}


def _cells(sub: pd.DataFrame) -> dict:
    """Full-quarter plus per-month stats for one region within one period."""
    out = {"all": _stat(sub["Availability"])}
    for month, key in MONTH_KEYS.items():
        out[key] = _stat(sub.loc[sub["Month"] == month, "Availability"])
    return out


def aggregate(df: pd.DataFrame) -> dict:
    regions = sorted(df["Region"].unique())
    if not regions:
        raise ValueError("No recognized shipping regions in the Q4 data.")

    stats: dict[str, dict[str, dict]] = {}
    stats["all"] = {r: _cells(df[df["Region"] == r]) for r in regions}
    for y in fd.YEARS:
        by_year = df[df["Year"] == y]
        stats[str(y)] = {
            r: _cells(by_year[by_year["Region"] == r]) for r in regions
        }

    # Tightest first (highest mean on the 1=surplus / 5=shortage scale) so the
    # chart view and the legend's "tightest region" callout agree.
    regions.sort(key=lambda r: stats["all"][r]["all"]["mean"], reverse=True)

    return {
        "meta": {
            "quarter": fd.QUARTER,
            "years": fd.YEARS,
            "built": date.today().isoformat(),
            "source": (
                f"USDA AMS Refrigerated Truck Rates and Availability "
                f"(Socrata {fd.SOCRATA_DOMAIN}/resource/{fd.DATASET_ID})"
            ),
        },
        "regions": regions,
        "stats": stats,
    }


# Matches the single-line `const DATA = {...};` injection point in the
# template. `[^;]*` is safe because JSON output never contains semicolons.
_DATA_RE = re.compile(r"const DATA = \{[^;]*\};")


def inject(template: str, data: dict) -> str:
    data_js = json.dumps(data, separators=(",", ":"))
    replacement = f"const DATA = {data_js};"
    new_text, count = _DATA_RE.subn(lambda _: replacement, template, count=1)
    if count != 1:
        raise RuntimeError(
            "Could not find 'const DATA = {...};' in heatmap_template.html. "
            "Has the template shape changed?"
        )
    return new_text


def main(out_path: str | None = None) -> None:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Template not found at {TEMPLATE_PATH}")

    df = fd.fetch_data()
    data = aggregate(df)
    html = inject(TEMPLATE_PATH.read_text(encoding="utf-8"), data)

    target = Path(out_path) if out_path else DEFAULT_OUTPUT
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")

    print(
        f"[build_chart] wrote {target} ({len(html):,} bytes, "
        f"{len(data['regions'])} regions, Q{fd.QUARTER} "
        f"{fd.YEAR_MIN}-{fd.YEAR_MAX})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
