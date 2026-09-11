"""Statcast (Baseball Savant) ingest via pybaseball.

Scope, per project spec: only the most recent 2 seasons (not the full
2015-present history). pybaseball.statcast() already issues one HTTP
request per day internally for multi-day ranges; on top of that, this
script caches each day's pull as its own parquet file under
data/raw/statcast/<season>/<date>.parquet and records, per season, the
last date successfully queried in manifest.json. Every re-run only asks
Baseball Savant for dates after that point, so nothing already fetched is
ever requested again.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "statcast"
DB_PATH = ROOT / "data" / "processed" / "mlb.duckdb"
MANIFEST_PATH = RAW_DIR / "manifest.json"

SEASON_START_MONTH_DAY = (3, 1)   # earliest plausible date to query per season
SEASON_END_MONTH_DAY = (11, 30)   # latest plausible date (covers postseason)
N_RECENT_SEASONS = 2


def _target_seasons() -> list[int]:
    current_year = dt.date.today().year
    return [current_year - (N_RECENT_SEASONS - 1) + i for i in range(N_RECENT_SEASONS)]


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {}


def _save_manifest(manifest: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def download(force: bool = False) -> None:
    # Imported lazily: pybaseball pulls in a chain of heavier optional deps
    # and prints its own startup chatter, which we don't want to pay for
    # unless statcast ingest is actually invoked.
    from pybaseball import statcast

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {} if force else _load_manifest()
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)  # today's games are likely incomplete

    for season in _target_seasons():
        season_dir = RAW_DIR / str(season)
        season_dir.mkdir(parents=True, exist_ok=True)

        season_start = dt.date(season, *SEASON_START_MONTH_DAY)
        season_end = min(dt.date(season, *SEASON_END_MONTH_DAY), yesterday)
        if season_end < season_start:
            continue

        fetched_through = manifest.get(str(season))
        start = season_start
        if fetched_through and not force:
            start = max(season_start, dt.date.fromisoformat(fetched_through) + dt.timedelta(days=1))

        if start > season_end:
            print(f"[statcast] {season}: already up to date (through {fetched_through})")
            continue

        print(f"[statcast] {season}: fetching {start} to {season_end} "
              f"(pybaseball issues one request per day)")
        df = statcast(start_dt=start.isoformat(), end_dt=season_end.isoformat(), verbose=False)

        if df is not None and not df.empty and "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
            for game_date, day_df in df.groupby("game_date"):
                out_path = season_dir / f"{game_date}.parquet"
                day_df.to_parquet(out_path, index=False)
            print(f"[statcast] {season}: saved {df['game_date'].nunique()} day(s), "
                  f"{len(df):,} rows")
        else:
            print(f"[statcast] {season}: no rows returned for {start}..{season_end} "
                  f"(off days / no games)")

        manifest[str(season)] = season_end.isoformat()
        _save_manifest(manifest)


def load_to_duckdb() -> None:
    files = sorted(RAW_DIR.glob("*/*.parquet"))
    if not files:
        print("[statcast] no cached parquet files found; run download first")
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        file_globs = str(RAW_DIR / "*" / "*.parquet")
        con.execute(f"CREATE OR REPLACE TABLE statcast_pitches AS SELECT * FROM read_parquet('{file_globs}')")
        n = con.execute("SELECT COUNT(*) FROM statcast_pitches").fetchone()[0]
        print(f"[statcast] loaded statcast_pitches ({n:,} rows, {len(files)} day-files) into {DB_PATH}")
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest recent-season Statcast data into DuckDB.")
    parser.add_argument("--force", action="store_true",
                         help="re-fetch the full season range even if a cache position is recorded")
    args = parser.parse_args()

    download(force=args.force)
    load_to_duckdb()


if __name__ == "__main__":
    sys.exit(main())
