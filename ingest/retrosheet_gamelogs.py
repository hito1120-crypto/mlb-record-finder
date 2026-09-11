"""Retrosheet Game Logs ingest.

Source: https://www.retrosheet.org/gamelogs/index.html

Retrosheet publishes one gamelog file per season (gl<year>.zip -> gl<year>.txt)
plus a combined archive covering all seasons at once (gl1871_2025.zip). We use
the combined archive for the bulk historical load (one HTTP request instead of
150+), then fall back to fetching individual per-season files directly for any
season not yet folded into that combined archive (e.g. the current season,
while it's still in progress). Either way the cache is keyed per season file,
so nothing is ever re-downloaded once it is on disk -- use --force to refresh.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import sys
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "retrosheet"
DB_PATH = ROOT / "data" / "processed" / "mlb.duckdb"

COMBINED_ARCHIVE_URL = "https://www.retrosheet.org/gamelogs/gl1871_2025.zip"
COMBINED_ARCHIVE_LAST_YEAR = 2025
FIRST_YEAR = 1871
YEAR_ZIP_TEMPLATE = "https://www.retrosheet.org/gamelogs/gl{year}.zip"

# Columns we keep, by 0-based position in the Retrosheet gamelog record, per
# https://www.retrosheet.org/gamelogs/glfields.txt
COLUMNS = {
    0: "date",
    1: "game_num",
    2: "day_of_week",
    3: "visiting_team",
    4: "visiting_league",
    5: "visiting_game_number",
    6: "home_team",
    7: "home_league",
    8: "home_game_number",
    9: "visiting_score",
    10: "home_score",
    11: "length_outs",
    12: "day_night",
    16: "park_id",
    17: "attendance",
    18: "time_minutes",
}


def _expected_years() -> list[int]:
    last_year = min(dt.date.today().year, COMBINED_ARCHIVE_LAST_YEAR + 5)
    return list(range(FIRST_YEAR, last_year + 1))


def _cached_years() -> set[int]:
    years = set()
    for p in RAW_DIR.glob("gl[12][0-9][0-9][0-9].txt"):
        try:
            years.add(int(p.stem[2:]))
        except ValueError:
            pass
    return years


def download(force: bool = False) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cached = set() if force else _cached_years()

    combined_years = set(range(FIRST_YEAR, COMBINED_ARCHIVE_LAST_YEAR + 1))
    if not combined_years.issubset(cached):
        print("[retrosheet] fetching combined historical archive "
              f"({FIRST_YEAR}-{COMBINED_ARCHIVE_LAST_YEAR}, one-time download)...")
        try:
            resp = requests.get(COMBINED_ARCHIVE_URL, timeout=120)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for name in zf.namelist():
                    if not name.startswith("gl") or not name.endswith(".txt"):
                        continue
                    stem = name[2:-4]
                    if not stem.isdigit():
                        continue  # skip special series files (glas.txt, glws.txt, ...)
                    dest = RAW_DIR / name
                    with zf.open(name) as src, open(dest, "wb") as out:
                        out.write(src.read())
            print(f"[retrosheet] extracted combined archive into {RAW_DIR}")
        except requests.RequestException as exc:
            print(f"[retrosheet] combined archive download failed: {exc}")
    else:
        print(f"[retrosheet] combined archive years ({FIRST_YEAR}-{COMBINED_ARCHIVE_LAST_YEAR}) "
              f"already cached, skipping")

    # Seasons after the combined archive's coverage (current/in-progress season,
    # or any new season Retrosheet has since published individually).
    cached = _cached_years()
    for year in range(COMBINED_ARCHIVE_LAST_YEAR + 1, dt.date.today().year + 1):
        if year in cached and not force:
            continue
        url = YEAR_ZIP_TEMPLATE.format(year=year)
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 404:
                continue  # not published yet
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                name = f"gl{year}.txt"
                with zf.open(name) as src, open(RAW_DIR / name, "wb") as out:
                    out.write(src.read())
            print(f"[retrosheet] fetched season {year} ({url})")
        except requests.RequestException as exc:
            print(f"[retrosheet] could not fetch season {year}: {exc}")


def load_to_duckdb() -> None:
    files = sorted(RAW_DIR.glob("gl[12][0-9][0-9][0-9].txt"))
    if not files:
        print("[retrosheet] no cached game log files found; run download first")
        return

    frames = []
    for path in files:
        year = int(path.stem[2:])
        df = pd.read_csv(
            path,
            header=None,
            usecols=list(COLUMNS.keys()),
            names=None,
            dtype=str,
        )
        df = df.rename(columns=dict(zip(sorted(COLUMNS.keys()), [COLUMNS[k] for k in sorted(COLUMNS.keys())])))
        df["season"] = year
        frames.append(df)

    all_games = pd.concat(frames, ignore_index=True)
    for col in ("visiting_score", "home_score", "length_outs", "attendance", "time_minutes", "season"):
        all_games[col] = pd.to_numeric(all_games[col], errors="coerce")
    all_games["date"] = pd.to_datetime(all_games["date"], format="%Y%m%d", errors="coerce")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute("CREATE OR REPLACE TABLE retrosheet_gamelogs AS SELECT * FROM all_games")
        print(f"[retrosheet] loaded retrosheet_gamelogs ({len(all_games):,} rows, "
              f"{all_games['season'].min():.0f}-{all_games['season'].max():.0f}) into {DB_PATH}")
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Retrosheet game logs into DuckDB.")
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    args = parser.parse_args()

    download(force=args.force)
    load_to_duckdb()


if __name__ == "__main__":
    sys.exit(main())
