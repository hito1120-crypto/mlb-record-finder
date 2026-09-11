"""Statcast (Baseball Savant) ingest via pybaseball.

Scope, per project spec: only the most recent 2 seasons (not the full
2015-present history). pybaseball.statcast() already issues one HTTP
request per day internally for multi-day ranges; on top of that, this
script caches each day's pull as its own parquet file under
data/raw/statcast/<season>/<date>.parquet and records, per season, the
last date successfully queried in manifest.json. Every re-run only asks
Baseball Savant for dates after that point, so nothing already fetched is
ever requested again.

Also fetches the Sprint Speed leaderboard (pybaseball.statcast_sprint_speed)
for the same seasons. Unlike the pitch-level data above, Sprint Speed is
not a column on individual pitch/batted-ball rows -- Baseball Savant only
publishes it as a precomputed per-player-per-season leaderboard, so this is
a single request per season (cached the same way: skipped if already on
disk, refreshed with --force) rather than a daily pull. We fetch with a
low min_opp so the on-disk cache is broad; the minimum-opportunities
threshold used for ranking is applied later, at query time (see
query/templates.py), so it can be changed without re-fetching.

Also fetches the Chadwick player ID register (pybaseball.chadwick_register),
a one-time (not per-season) MLBAM-ID-to-name crosswalk. This is needed
because the raw Statcast pitch rows' `player_name` column is the PITCHER
throwing the pitch, not the batter -- confirmed by inspecting the ingested
data (for a fixed player_name, the `pitcher` column is constant across rows
while `batter` varies pitch to pitch). Batter-side leaderboards (barrel%,
expected stats) therefore have to join statcast_pitches.batter against this
register's key_mlbam to get the batter's actual name.
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

SPRINT_SPEED_DIR = RAW_DIR / "sprint_speed"
SPRINT_SPEED_FETCH_MIN_OPP = 1  # broad cache; real filtering happens at query time

PLAYER_ID_LOOKUP_PATH = RAW_DIR / "player_id_lookup.parquet"


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

    download_sprint_speed(force=force)
    download_player_id_lookup(force=force)


def download_player_id_lookup(force: bool = False) -> None:
    if PLAYER_ID_LOOKUP_PATH.exists() and not force:
        print("[statcast] player ID lookup already cached, skipping")
        return

    from pybaseball import chadwick_register

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print("[statcast] fetching Chadwick player ID register (one-time, not per-season)...")
    df = chadwick_register(save=False)
    df = df.dropna(subset=["key_mlbam"])
    df["key_mlbam"] = df["key_mlbam"].astype("int64")
    df.to_parquet(PLAYER_ID_LOOKUP_PATH, index=False)
    print(f"[statcast] player ID lookup: saved {len(df):,} players")


def download_sprint_speed(force: bool = False) -> None:
    from pybaseball import statcast_sprint_speed

    SPRINT_SPEED_DIR.mkdir(parents=True, exist_ok=True)
    for season in _target_seasons():
        dest = SPRINT_SPEED_DIR / f"{season}.parquet"
        if dest.exists() and not force:
            print(f"[statcast] sprint speed {season}: already cached, skipping")
            continue
        try:
            df = statcast_sprint_speed(season, min_opp=SPRINT_SPEED_FETCH_MIN_OPP)
        except Exception as exc:  # pybaseball raises plain Exception/HTTPError on a bad season
            print(f"[statcast] sprint speed {season}: fetch failed ({exc})")
            continue
        if df is None or df.empty:
            print(f"[statcast] sprint speed {season}: no data yet (season may not have started)")
            continue

        # "last_name, first_name" as a literal column name is awkward to
        # query -- normalize it to a plain full_name column up front.
        name_col = "last_name, first_name"
        if name_col in df.columns:
            parts = df[name_col].str.split(", ", n=1, expand=True)
            df["full_name"] = parts[1] + " " + parts[0]
            df = df.drop(columns=[name_col])
        df["season"] = season

        df.to_parquet(dest, index=False)
        print(f"[statcast] sprint speed {season}: saved {len(df)} players")


def load_to_duckdb() -> None:
    files = sorted(RAW_DIR.glob("[12][0-9][0-9][0-9]/*.parquet"))
    if not files:
        print("[statcast] no cached pitch-level parquet files found; run download first")
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(DB_PATH))
        try:
            file_globs = str(RAW_DIR / "[12][0-9][0-9][0-9]" / "*.parquet")
            con.execute(f"CREATE OR REPLACE TABLE statcast_pitches AS SELECT * FROM read_parquet('{file_globs}')")
            n = con.execute("SELECT COUNT(*) FROM statcast_pitches").fetchone()[0]
            print(f"[statcast] loaded statcast_pitches ({n:,} rows, {len(files)} day-files) into {DB_PATH}")
        finally:
            con.close()

    sprint_files = sorted(SPRINT_SPEED_DIR.glob("*.parquet"))
    if not sprint_files:
        print("[statcast] no cached sprint-speed parquet files found; run download first")
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        file_globs = str(SPRINT_SPEED_DIR / "*.parquet")
        con.execute(f"CREATE OR REPLACE TABLE statcast_sprint_speed AS SELECT * FROM read_parquet('{file_globs}')")
        n = con.execute("SELECT COUNT(*) FROM statcast_sprint_speed").fetchone()[0]
        print(f"[statcast] loaded statcast_sprint_speed ({n:,} rows, {len(sprint_files)} season-files) into {DB_PATH}")
    finally:
        con.close()

    if not PLAYER_ID_LOOKUP_PATH.exists():
        print("[statcast] no cached player ID lookup found; run download first")
        return

    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute(
            f"CREATE OR REPLACE TABLE player_id_lookup AS "
            f"SELECT * FROM read_parquet('{PLAYER_ID_LOOKUP_PATH}')"
        )
        n = con.execute("SELECT COUNT(*) FROM player_id_lookup").fetchone()[0]
        print(f"[statcast] loaded player_id_lookup ({n:,} rows) into {DB_PATH}")
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
