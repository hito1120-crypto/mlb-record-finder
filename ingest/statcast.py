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

Also fetches the Outs Above Average (OAA) fielding leaderboard
(pybaseball.statcast_outs_above_average). Like Sprint Speed this is a
precomputed leaderboard rather than a pitch-level column, but unlike Sprint
Speed it is published per (season, POSITION): the same player gets a
different OAA depending on which position's fielding chances are counted
(a 2B who also plays some LF has one number under "2B" and another under
"OF"), so the queried position is part of the cache key and is stored as
its own `pos` column. Two further quirks, both confirmed against the live
leaderboard: the CSV carries no attempts/opportunities count, so unlike
Sprint Speed's min_opp the attempts threshold cannot be re-applied at query
time and is fixed here at Savant's "qualified" cutoff; and catchers are not
covered at all (pybaseball raises ValueError for pos=2).

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

OAA_DIR = RAW_DIR / "oaa"
# Canonical position code -> the `pos` argument pybaseball expects. Catcher
# is deliberately absent (see module docstring). "IF"/"OF"/"ALL" are the
# leaderboard's own aggregate buckets, not derivable by summing the
# individual positions, so they are fetched as their own rows.
OAA_POSITIONS = {
    "1B": 3,
    "2B": 4,
    "3B": 5,
    "SS": 6,
    "LF": 7,
    "CF": 8,
    "RF": 9,
    "IF": "IF",
    "OF": "OF",
    "ALL": "all",
}
# Unlike Sprint Speed's min_opp this can't be relaxed at query time -- the
# leaderboard CSV has no attempts column -- so we cache Savant's own
# "qualified" leaderboard.
OAA_FETCH_MIN_ATT = "q"
OAA_COLUMN_RENAMES = {
    "display_team_name": "team",
    "primary_pos_formatted": "primary_position",
    "year": "season",
}
# Savant returns these as strings like "89%" / "-2%"; stored as plain numbers.
OAA_RATE_COLUMNS = {
    "actual_success_rate_formatted": "actual_success_rate",
    "adj_estimated_success_rate_formatted": "adj_estimated_success_rate",
    "diff_success_rate_formatted": "diff_success_rate",
}

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
    download_oaa(force=force)
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


def _with_full_name(df: pd.DataFrame) -> pd.DataFrame:
    """Savant leaderboards ship the player name in a column literally called
    "last_name, first_name", which is awkward to query -- normalize it to a
    plain full_name column up front."""
    name_col = "last_name, first_name"
    if name_col not in df.columns:
        return df
    parts = df[name_col].str.split(", ", n=1, expand=True)
    df = df.copy()
    df["full_name"] = parts[1] + " " + parts[0]
    return df.drop(columns=[name_col])


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

        df = _with_full_name(df)
        df["season"] = season

        df.to_parquet(dest, index=False)
        print(f"[statcast] sprint speed {season}: saved {len(df)} players")


def download_oaa(force: bool = False) -> None:
    from pybaseball import statcast_outs_above_average

    OAA_DIR.mkdir(parents=True, exist_ok=True)
    for season in _target_seasons():
        for code, pos in OAA_POSITIONS.items():
            dest = OAA_DIR / f"{season}_{code}.parquet"
            if dest.exists() and not force:
                print(f"[statcast] OAA {season} {code}: already cached, skipping")
                continue
            try:
                df = statcast_outs_above_average(
                    season, pos, min_att=OAA_FETCH_MIN_ATT, view="Fielder"
                )
            except Exception as exc:  # same failure modes as sprint speed above
                print(f"[statcast] OAA {season} {code}: fetch failed ({exc})")
                continue
            if df is None or df.empty:
                print(f"[statcast] OAA {season} {code}: no data yet "
                      f"(season may not have started)")
                continue

            df = _with_full_name(df)
            df = df.rename(columns=OAA_COLUMN_RENAMES)
            for src, dest_col in OAA_RATE_COLUMNS.items():
                if src in df.columns:
                    df[dest_col] = pd.to_numeric(
                        df[src].astype(str).str.rstrip("%"), errors="coerce"
                    )
                    df = df.drop(columns=[src])
            df["season"] = season
            df["pos"] = code

            df.to_parquet(dest, index=False)
            print(f"[statcast] OAA {season} {code}: saved {len(df)} players")


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

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    sprint_files = sorted(SPRINT_SPEED_DIR.glob("*.parquet"))
    if not sprint_files:
        print("[statcast] no cached sprint-speed parquet files found; run download first")
    else:
        con = duckdb.connect(str(DB_PATH))
        try:
            file_globs = str(SPRINT_SPEED_DIR / "*.parquet")
            con.execute(f"CREATE OR REPLACE TABLE statcast_sprint_speed AS SELECT * FROM read_parquet('{file_globs}')")
            n = con.execute("SELECT COUNT(*) FROM statcast_sprint_speed").fetchone()[0]
            print(f"[statcast] loaded statcast_sprint_speed ({n:,} rows, {len(sprint_files)} season-files) into {DB_PATH}")
        finally:
            con.close()

    oaa_files = sorted(OAA_DIR.glob("*.parquet"))
    if not oaa_files:
        print("[statcast] no cached OAA parquet files found; run download first")
    else:
        con = duckdb.connect(str(DB_PATH))
        try:
            file_globs = str(OAA_DIR / "*.parquet")
            # union_by_name: the per-position files are unioned together, so
            # be tolerant if Savant ever adds a column mid-season.
            con.execute(
                f"CREATE OR REPLACE TABLE statcast_oaa AS "
                f"SELECT * FROM read_parquet('{file_globs}', union_by_name=true)"
            )
            n = con.execute("SELECT COUNT(*) FROM statcast_oaa").fetchone()[0]
            print(f"[statcast] loaded statcast_oaa ({n:,} rows, "
                  f"{len(oaa_files)} season/position-files) into {DB_PATH}")
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
