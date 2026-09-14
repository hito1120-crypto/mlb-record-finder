"""MLB Stats API ingest -- in-progress-season fallback for query/war.py.

Lahman (ingest/lahman.py) and Retrosheet Game Logs (ingest/retrosheet_gamelogs.py)
are both season-end snapshots: Lahman is published once a year, well after a
season finishes (the 2025 season's data wasn't published until January 2026),
and Retrosheet's per-season gamelog file for a season still in progress isn't
published until that season ends either. Neither source can ever answer "what
is this season's WAR so far" while the season is still being played.

MLB Stats API (statsapi.mlb.com) is a free, unauthenticated, official MLB
endpoint that *does* update live during the season, so this module fetches
just enough from it to unblock query/war.py for a season not yet in Lahman:
  - team games played (for replacement_runs_pools' avg_games)
  - every player's season-to-date hitting and pitching totals (playerPool=ALL,
    not the default "QUALIFIED" pool, so part-time players are included too --
    see fetch_player_stats' docstring)

This is intentionally a much thinner slice of data than Lahman -- no career
history, no fielding-by-position appearance counts, no franchise/park
metadata. It exists purely as query/war.py's provisional current-season
fallback, so it loads into its own mlbapi_teams / mlbapi_batting /
mlbapi_pitching tables rather than touching lahman_* -- appending live,
partial-season rows into the Lahman tables would silently corrupt every
other template that reads them (career totals, single-season achiever
lists, etc.), none of which know how to label a partial season as such.

Player IDs here are MLB Advanced Media IDs (numeric), a different scheme
from Lahman's playerID -- stored as "mlbam_<id>" strings so they can't
collide with a real Lahman playerID if ever compared side by side.

Caching follows this project's usual ingest convention (see ingest/lahman.py,
ingest/retrosheet_gamelogs.py): once a season is loaded, re-running does
nothing for that season unless force=True / --force is passed. For a season
still being played, that means the loaded snapshot is only as fresh as the
last (re-)ingest -- acceptable for this project's purposes, and consistent
with how every other ingest module here already treats "in progress" data
(e.g. retrosheet_gamelogs.py's per-season current-year fetch).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "processed" / "mlb.duckdb"

BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_ID_MLB = 1
LEAGUE_IDS_MLB = "103,104"  # AL, NL

# Generous upper bound on players-per-season -- a real MLB season tops out
# around 1,000-1,300 hitters/pitchers combined (playerPool=ALL, i.e. every
# September call-up and one-batter reliever included). fetch_player_stats
# raises if the API ever reports more totalSplits than this, rather than
# silently truncating the result.
STATS_FETCH_LIMIT = 3000


def fetch_team_games(season: int) -> pd.DataFrame:
    """One row per team: {team_id, G} (games played so far), from the
    regular-season standings endpoint."""
    resp = requests.get(
        f"{BASE_URL}/standings",
        params={"leagueId": LEAGUE_IDS_MLB, "season": season, "standingsTypes": "regularSeason"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = [
        {"team_id": tr["team"]["id"], "G": tr["gamesPlayed"]}
        for record in resp.json()["records"]
        for tr in record["teamRecords"]
    ]
    return pd.DataFrame(rows)


def fetch_team_abbreviations(season: int) -> dict[int, str]:
    """{team_id: abbreviation} (e.g. 147 -> "NYY") for every active MLB team."""
    resp = requests.get(f"{BASE_URL}/teams", params={"sportId": SPORT_ID_MLB, "season": season}, timeout=30)
    resp.raise_for_status()
    return {t["id"]: t["abbreviation"] for t in resp.json()["teams"]}


def fetch_player_stats(season: int, group: str) -> list[dict]:
    """Raw "splits" list from the season stats endpoint for group in
    {"hitting", "pitching"}. playerPool=ALL is required -- the endpoint's
    default (playerPool=QUALIFIED) silently drops every player who hasn't
    met that stat's qualifying PA/IP rate, which is most of a roster."""
    resp = requests.get(
        f"{BASE_URL}/stats",
        params={
            "stats": "season", "group": group, "season": season,
            "sportId": SPORT_ID_MLB, "limit": STATS_FETCH_LIMIT, "playerPool": "ALL",
        },
        timeout=60,
    )
    resp.raise_for_status()
    stat_block = resp.json()["stats"][0]
    splits = stat_block["splits"]
    total = stat_block.get("totalSplits", len(splits))
    if len(splits) < total:
        raise RuntimeError(
            f"MLB Stats API reported {total} {group} rows for {season} but only "
            f"returned {len(splits)} -- raise STATS_FETCH_LIMIT (currently {STATS_FETCH_LIMIT})"
        )
    return splits


def _build_batting_df(season: int, splits: list[dict], team_abbrev: dict[int, str]) -> pd.DataFrame:
    rows = []
    for s in splits:
        stat = s["stat"]
        player = s["player"]
        team = s.get("team") or {}
        rows.append({
            "season": season,
            "playerID": f"mlbam_{player['id']}",
            "full_name": player["fullName"],
            "teamID": team_abbrev.get(team.get("id"), team.get("name")),
            "G": stat.get("gamesPlayed") or 0,
            "AB": stat.get("atBats") or 0,
            "H": stat.get("hits") or 0,
            "2B": stat.get("doubles") or 0,
            "3B": stat.get("triples") or 0,
            "HR": stat.get("homeRuns") or 0,
            "BB": stat.get("baseOnBalls") or 0,
            "IBB": stat.get("intentionalWalks") or 0,
            "HBP": stat.get("hitByPitch") or 0,
            "SF": stat.get("sacFlies") or 0,
            "SB": stat.get("stolenBases") or 0,
            "CS": stat.get("caughtStealing") or 0,
            "primary_position": (s.get("position") or {}).get("abbreviation"),
        })
    return pd.DataFrame(rows)


def _build_pitching_df(season: int, splits: list[dict], team_abbrev: dict[int, str]) -> pd.DataFrame:
    rows = []
    for s in splits:
        stat = s["stat"]
        player = s["player"]
        team = s.get("team") or {}
        rows.append({
            "season": season,
            "playerID": f"mlbam_{player['id']}",
            "full_name": player["fullName"],
            "teamID": team_abbrev.get(team.get("id"), team.get("name")),
            "G": stat.get("gamesPitched") or 0,
            "IPouts": stat.get("outs") or 0,
            "HR": stat.get("homeRuns") or 0,
            "BB": stat.get("baseOnBalls") or 0,
            "HBP": stat.get("hitBatsmen") or 0,
            "SO": stat.get("strikeOuts") or 0,
        })
    return pd.DataFrame(rows)


_TABLE_SCHEMAS = {
    "mlbapi_teams": '(season INTEGER, team_id INTEGER, teamID VARCHAR, G INTEGER)',
    "mlbapi_batting": (
        '(season INTEGER, playerID VARCHAR, full_name VARCHAR, teamID VARCHAR, '
        'G INTEGER, AB INTEGER, H INTEGER, "2B" INTEGER, "3B" INTEGER, HR INTEGER, '
        'BB INTEGER, IBB INTEGER, HBP INTEGER, SF INTEGER, SB INTEGER, CS INTEGER, '
        'primary_position VARCHAR)'
    ),
    "mlbapi_pitching": (
        '(season INTEGER, playerID VARCHAR, full_name VARCHAR, teamID VARCHAR, '
        'G INTEGER, IPouts INTEGER, HR INTEGER, BB INTEGER, HBP INTEGER, SO INTEGER)'
    ),
}


def _replace_season(con: duckdb.DuckDBPyConnection, table: str, season: int, df: pd.DataFrame) -> None:
    con.execute(f"CREATE TABLE IF NOT EXISTS {table} {_TABLE_SCHEMAS[table]}")
    con.execute(f"DELETE FROM {table} WHERE season = ?", [season])
    cols = ", ".join(f'"{c}"' if c in ("2B", "3B") else c for c in df.columns)
    con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM df")


def is_season_loaded(con: duckdb.DuckDBPyConnection, season: int) -> bool:
    exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'mlbapi_teams'"
    ).fetchone()[0]
    if not exists:
        return False
    return con.execute("SELECT COUNT(*) FROM mlbapi_teams WHERE season = ?", [season]).fetchone()[0] > 0


def load_season(con: duckdb.DuckDBPyConnection, season: int) -> None:
    """Fetches and (re)loads one season's team games / hitting / pitching
    totals from the MLB Stats API into mlbapi_teams / mlbapi_batting /
    mlbapi_pitching, replacing any existing rows for that season."""
    print(f"[mlb_stats_api] fetching season {season} from {BASE_URL} ...")
    team_abbrev = fetch_team_abbreviations(season)
    teams_df = fetch_team_games(season)
    teams_df["season"] = season
    teams_df["teamID"] = teams_df["team_id"].map(team_abbrev)
    teams_df = teams_df[["season", "team_id", "teamID", "G"]]

    batting_df = _build_batting_df(season, fetch_player_stats(season, "hitting"), team_abbrev)
    pitching_df = _build_pitching_df(season, fetch_player_stats(season, "pitching"), team_abbrev)

    _replace_season(con, "mlbapi_teams", season, teams_df)
    _replace_season(con, "mlbapi_batting", season, batting_df)
    _replace_season(con, "mlbapi_pitching", season, pitching_df)
    print(
        f"[mlb_stats_api] loaded season {season}: {len(teams_df)} teams, "
        f"{len(batting_df)} hitters, {len(pitching_df)} pitchers"
    )


def ensure_season_loaded(con: duckdb.DuckDBPyConnection, season: int, force: bool = False) -> None:
    """Loads `season` from the MLB Stats API unless it's already cached in
    mlbapi_teams (matching every other ingest module's cache-unless-force
    convention). Called automatically by query/war.py when a season has no
    Lahman data yet."""
    if not force and is_season_loaded(con, season):
        return
    load_season(con, season)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest one season from the MLB Stats API.")
    parser.add_argument("--season", type=int, required=True, help="season year, e.g. 2026")
    parser.add_argument("--force", action="store_true", help="re-fetch even if already cached")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_season_loaded(con, args.season, force=args.force)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
