"""Retrosheet Play-by-Play (event file) ingest.

Source: https://www.retrosheet.org/events/index.html -- one zip per season
(<year>eve.zip), containing one raw event file per home team
(e.g. 2024ANA.EVA). Each event file lists, in order, every plate appearance
and baserunning event (steal, pickoff, wild pitch, ...) for every game that
team hosted that season, encoded in Retrosheet's compact play-by-play
grammar (see https://www.retrosheet.org/eventfile.htm for the full spec).

Regular-season games only (the {year}eve.zip archives do not include
postseason play; that is out of scope here, same as Phase 1's game logs
which do cover it -- see the coverage caveat added to the README).

Unlike the Game Logs (one row per game), this gives one row per plate
appearance / baserunning event, with the batter, the base/out state before
and after, the score, and how many runs scored on the play -- which is what
lets us search across plays *within* a game (e.g. "leadoff HR AND walk-off
HR in the same game") and build a run-expectancy / win-probability model
(see transform/schema.sql).

No C toolchain required: Chadwick's `cwevent` (the reference C tool for this
same job) needs a C compiler to build, which this project deliberately
avoids depending on for portability. Instead, _parser.py below is a
from-scratch Python implementation of the same event-string grammar,
covering the play types that matter for this project's templates. See
_parser.py's module docstring for exactly what is (and isn't) handled, and
verify_playbyplay.py for the cross-check against Retrosheet's own Game Logs
that this parser's run totals are run against.

Caching: same pattern as ingest/retrosheet_gamelogs.py -- each season's raw
event files are cached under data/raw/retrosheet_pbp/<year>/ and never
re-downloaded once present; use --force to refresh. Parsing (which is pure
Python and non-trivial) happens at load_to_duckdb() time, reading from the
cached raw files, not at download time.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _playbyplay_parser import parse_event_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "retrosheet_pbp"
DB_PATH = ROOT / "data" / "processed" / "mlb.duckdb"

EVENTS_URL_TEMPLATE = "https://www.retrosheet.org/events/{year}eve.zip"

# Per the task: start with a manageable recent window rather than the full
# archive (Retrosheet's play-by-play coverage goes back to 1921, with
# earlier decades much less complete than the modern era -- see the README
# caveat). This can be widened later by passing --start-year explicitly.
DEFAULT_SEASONS_BACK = 5


def _default_years() -> list[int]:
    current_year = dt.date.today().year
    return list(range(current_year - DEFAULT_SEASONS_BACK + 1, current_year + 1))


def _cached_years() -> set[int]:
    years = set()
    if not RAW_DIR.exists():
        return years
    for p in RAW_DIR.iterdir():
        if p.is_dir() and p.name.isdigit() and any(p.glob("*.EV?")):
            years.add(int(p.name))
    return years


def download(years: list[int] | None = None, force: bool = False) -> None:
    years = years if years is not None else _default_years()
    cached = set() if force else _cached_years()

    for year in years:
        if year in cached:
            print(f"[retrosheet-pbp] {year}: already cached, skipping")
            continue

        url = EVENTS_URL_TEMPLATE.format(year=year)
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 404:
                print(f"[retrosheet-pbp] {year}: not published yet ({url})")
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"[retrosheet-pbp] {year}: download failed: {exc}")
            continue

        season_dir = RAW_DIR / str(year)
        season_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            n = 0
            for name in zf.namelist():
                if not (name.endswith(".EVA") or name.endswith(".EVN")):
                    continue
                with zf.open(name) as src, open(season_dir / name, "wb") as out:
                    out.write(src.read())
                n += 1
        print(f"[retrosheet-pbp] {year}: extracted {n} team event file(s) ({url})")


def load_to_duckdb() -> None:
    season_dirs = sorted(p for p in RAW_DIR.glob("[12][0-9][0-9][0-9]") if p.is_dir())
    if not season_dirs:
        print("[retrosheet-pbp] no cached event files found; run download first")
        return

    all_rows = []
    for season_dir in season_dirs:
        season = int(season_dir.name)
        event_files = sorted(season_dir.glob("*.EV?"))
        season_rows = 0
        for path in event_files:
            text = path.read_text(encoding="latin-1")
            rows = parse_event_file(text, season=season)
            all_rows.extend(rows)
            season_rows += len(rows)
        print(f"[retrosheet-pbp] {season}: parsed {season_rows:,} play(s) "
              f"from {len(event_files)} team file(s)")

    if not all_rows:
        print("[retrosheet-pbp] no plays parsed; nothing to load")
        return

    df = pd.DataFrame(all_rows)
    df["game_date"] = pd.to_datetime(df["game_date"], format="%Y/%m/%d", errors="coerce")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute("CREATE OR REPLACE TABLE retrosheet_playbyplay AS SELECT * FROM df")
        n = con.execute("SELECT COUNT(*) FROM retrosheet_playbyplay").fetchone()[0]
        n_games = con.execute("SELECT COUNT(DISTINCT game_id) FROM retrosheet_playbyplay").fetchone()[0]
        print(f"[retrosheet-pbp] loaded retrosheet_playbyplay ({n:,} rows, "
              f"{n_games:,} games) into {DB_PATH}")
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Retrosheet play-by-play event files into DuckDB.")
    parser.add_argument("--force", action="store_true", help="re-download even if cached")
    parser.add_argument("--start-year", type=int, default=None,
                         help="first season to fetch (default: last %d seasons)" % DEFAULT_SEASONS_BACK)
    parser.add_argument("--end-year", type=int, default=None,
                         help="last season to fetch (default: current year)")
    args = parser.parse_args()

    years = None
    if args.start_year is not None:
        end_year = args.end_year or dt.date.today().year
        years = list(range(args.start_year, end_year + 1))

    download(years=years, force=args.force)
    load_to_duckdb()


if __name__ == "__main__":
    sys.exit(main())
