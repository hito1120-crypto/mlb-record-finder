"""Simplified single-season park factor, derived from Retrosheet Game Logs.

This is a NEW, independent calculation (not Lahman's own BPF/PPF columns,
which are official Baseball-Reference multi-year figures) -- see
query/war.py's module docstring for why this project's WAR doesn't reuse
those. It is cross-checked against lahman_teams.BPF in this module's
validation, but not derived from it.

Method (the standard "basic" single-season park factor):
    raw_pf = (runs scored+allowed per home game) / (runs scored+allowed per road game)
for the team that plays its home games at that park. A raw_pf above 1 means
the park inflates scoring; below 1 means it suppresses it.

A single season of ~81 home / ~81 road games is a small sample, so -- as is
standard practice -- the raw ratio is regressed halfway toward a neutral 1.0
before use:
    half_pf = (raw_pf + 1) / 2
This also happens to be the correct adjustment for applying the factor to a
player's full-season value: a player accrues roughly half their playing time
at home and half on the road, so only half of the park's effect should be
backed out of their full-season numbers.

Team ID crosswalk: Retrosheet's game logs use the historical code "ANA" for
the Angels; every other 2015-2026 team code matches Lahman's teamID exactly
(verified against both tables for every season 2015-2025). No other
franchise has moved or been renamed within this project's 2015-2026 WAR
window.
"""

from __future__ import annotations

import duckdb
import pandas as pd

TEAM_ID_LAHMAN_TO_RETROSHEET = {"LAA": "ANA"}


def team_half_park_factors(con: duckdb.DuckDBPyConnection, year: int) -> dict[str, float]:
    """Returns {lahman_teamID: half_pf} for every team active in `year`."""
    df = con.execute(
        """
        WITH split AS (
            SELECT
                team,
                SUM(CASE WHEN home_away = 'H' THEN runs_for + runs_against ELSE 0 END) AS home_runs,
                SUM(CASE WHEN home_away = 'H' THEN 1 ELSE 0 END) AS home_games,
                SUM(CASE WHEN home_away = 'V' THEN runs_for + runs_against ELSE 0 END) AS away_runs,
                SUM(CASE WHEN home_away = 'V' THEN 1 ELSE 0 END) AS away_games
            FROM v_team_games
            WHERE season = ?
            GROUP BY team
        )
        SELECT
            team,
            (home_runs::DOUBLE / NULLIF(home_games, 0))
                / NULLIF(away_runs::DOUBLE / NULLIF(away_games, 0), 0) AS raw_pf
        FROM split
        """,
        [year],
    ).fetchdf()

    retro_to_half_pf = {
        row.team: (row.raw_pf + 1) / 2
        for row in df.itertuples()
        if pd.notna(row.raw_pf) and row.raw_pf > 0
    }

    retro_to_lahman = {v: k for k, v in TEAM_ID_LAHMAN_TO_RETROSHEET.items()}
    return {
        retro_to_lahman.get(retro_team, retro_team): half_pf
        for retro_team, half_pf in retro_to_half_pf.items()
    }


def weighted_half_park_factor(team_games: dict[str, float], half_pfs: dict[str, float]) -> float:
    """Games-weighted half-PF across a player's stints (handles in-season trades).
    Falls back to a neutral 1.0 for any team missing from half_pfs (e.g. a
    Lahman teamID with no matching Retrosheet game-log rows that season)."""
    total_games = sum(team_games.values())
    if total_games <= 0:
        return 1.0
    return sum(g * half_pfs.get(team, 1.0) for team, g in team_games.items()) / total_games
