# 🚛 Truck Availability Tracker — automated reefer-capacity signals

> An automated pipeline that turns USDA refrigerated-truck data into a clear regional capacity signal, so a freight team can see where reefer capacity is tightening **before** it shows up in rates — and rebuilds itself on every run with zero manual work.

![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75?style=flat-square&logo=plotly&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

![Q3 reefer truck availability by region](reports/figures/preview.png)

## 📊 The business question
Reefer (refrigerated-truck) capacity swings hard by region and season. If you can see **which lanes are structurally short vs. loose**, you can pre-book capacity, renegotiate lanes, or shift volume before a rate spike hits. This project answers: *where is Q3 reefer capacity tight, where is it loose, and how has that changed year over year?*

## 🧠 Approach & trade-offs
USDA AMS availability data (a 1–5 index, where 5 = shortage and 1 = surplus) is aggregated to a **4-year Q3 average by region** and rendered as a diverging bar chart centered on "adequate (3)." I chose a diverging layout over a plain bar chart because the *direction* from adequate is the decision-relevant signal, not the raw number.

The pipeline ships with a **CSV fallback so it runs immediately**, and a clearly-marked API template (`fetch_from_api()`) for wiring the live USDA feed when its endpoint/auth are available. `_normalize()` enforces the `Week, Quarter, Year, Region, Availability` schema so a bad response **fails loudly in CI** instead of silently publishing a wrong chart — an intentional "fail fast" choice for anything that auto-deploys.

## 🔑 Key findings (4-year Q3 average)
- **Mexico–Texas sits in structural surplus every Q3** (~1.0–1.1, the floor of the scale) — a permanent condition worth planning capacity around, not a one-off good year.
- **Eastern lanes stay tight:** Indiana (3.94), Mid-Atlantic (3.70), and Florida (3.59) run consistently above "adequate," even after easing from near-crisis (~4.8) levels in Q3 2022.
- Western/central regions (California, PNW, Arizona, Great Lakes) cluster near balanced.

## ✅ Decision this supports
Pre-book or contract reefer capacity in the tight eastern lanes ahead of Q3, and lean on the reliable Mexico–Texas surplus when flexing volume — turning a seasonal scramble into a planned position.

## 🛠️ Stack
Python (pandas) · Plotly · GitHub Actions (scheduled + on-push rebuild) · GitHub Pages

## ▶️ Run it locally
```bash
pip install -r requirements.txt

# CSV mode (no key needed):
python scripts/build_chart.py dist/Q3_Availability_Graph.html

# API mode (after wiring fetch_from_api):
export USDA_API_KEY=your_key
DATA_SOURCE=api python scripts/build_chart.py dist/Q3_Availability_Graph.html
```
Then open `dist/Q3_Availability_Graph.html`.

## 📂 Data & license
Source: **USDA Agricultural Marketing Service (AMS)** — public refrigerated-truck rate & availability reports. Code released under the [MIT License](LICENSE).
