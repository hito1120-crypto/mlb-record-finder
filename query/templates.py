"""The five Phase 1 record-finder query templates.

Every function takes an open duckdb connection (with transform/schema.sql
already applied) plus plain Python parameters, and returns a pandas
DataFrame. cli.py is responsible for prompting for parameters, calling
these, and rendering/translating the result -- these functions know
nothing about i18n or the terminal.
"""

from __future__ import annotations

import duckdb
import pandas as pd

# Only these Lahman batting columns may be used as achievement thresholds
# (templates 3 and 5). Column names can't be bound as SQL parameters, so we
# validate against this whitelist before interpolating them into a query.
ALLOWED_THRESHOLD_STATS = {
    "HR": "HR",
    "SB": "SB",
    "3B": '"3B"',
    "2B": '"2B"',
    "H": "H",
    "RBI": "RBI",
    "R": "R",
    "BB": "BB",
    "SO": "SO",
}


def homerun_search(con: duckdb.DuckDBPyConnection, min_exit_velo: float,
                    min_distance: float, limit: int = 50) -> pd.DataFrame:
    """Template 1: Statcast home runs at or above given exit velocity + distance."""
    return con.execute(
        """
        SELECT
            player_name,
            game_date,
            away_team,
            home_team,
            launch_speed  AS exit_velocity_mph,
            hit_distance_sc AS distance_ft,
            launch_angle  AS launch_angle_deg
        FROM v_statcast_home_runs
        WHERE launch_speed >= ?
          AND hit_distance_sc >= ?
        ORDER BY exit_velocity_mph DESC, distance_ft DESC
        LIMIT ?
        """,
        [min_exit_velo, min_distance, limit],
    ).fetchdf()


def team_career_hr_ranking(con: duckdb.DuckDBPyConnection, franch_id: str,
                            limit: int = 20) -> pd.DataFrame:
    """Template 2: all-time HR ranking for players who played for a given franchise
    (aggregated across every teamID/season the franchise has used, e.g. Brooklyn
    and LA both count for franchID 'LAD')."""
    return con.execute(
        """
        SELECT
            p.full_name,
            b.franchID,
            SUM(b.HR) AS career_hr_with_team,
            MIN(b.yearID) AS first_year,
            MAX(b.yearID) AS last_year
        FROM v_batting_by_franchise b
        JOIN v_people p ON b.playerID = p.playerID
        WHERE b.franchID = ?
        GROUP BY p.full_name, b.franchID
        ORDER BY career_hr_with_team DESC
        LIMIT ?
        """,
        [franch_id, limit],
    ).fetchdf()


def _season_stat_achievers(con: duckdb.DuckDBPyConnection, year: int,
                            thresholds: dict[str, int], limit: int = 100) -> pd.DataFrame:
    for stat in thresholds:
        if stat not in ALLOWED_THRESHOLD_STATS:
            raise ValueError(f"unsupported stat: {stat}")

    select_cols = ", ".join(f"SUM({ALLOWED_THRESHOLD_STATS[s]}) AS {s.replace('3B','X3B').replace('2B','X2B')}"
                             for s in thresholds)
    having_clauses = " AND ".join(
        f"SUM({ALLOWED_THRESHOLD_STATS[s]}) >= ?" for s in thresholds
    )

    sql = f"""
        SELECT
            p.full_name,
            b.yearID AS season,
            STRING_AGG(DISTINCT b.teamID, '/') AS teams,
            {select_cols}
        FROM lahman_batting b
        JOIN v_people p ON b.playerID = p.playerID
        WHERE b.yearID = ?
        GROUP BY p.full_name, b.yearID
        HAVING {having_clauses}
        ORDER BY {list(thresholds.keys())[0].replace('3B','X3B').replace('2B','X2B')} DESC
        LIMIT ?
    """
    params = [year, *thresholds.values(), limit]
    return con.execute(sql, params).fetchdf()


def season_two_stat_achievers(con: duckdb.DuckDBPyConnection, year: int,
                               hr_min: int, sb_min: int, limit: int = 100) -> pd.DataFrame:
    """Template 3: players hitting >= hr_min HR AND >= sb_min SB in a single season."""
    return _season_stat_achievers(con, year, {"HR": hr_min, "SB": sb_min}, limit)


def season_three_stat_achievers(con: duckdb.DuckDBPyConnection, year: int,
                                 triple_min: int, hr_min: int, sb_min: int,
                                 limit: int = 100) -> pd.DataFrame:
    """Template 5: players hitting >= triple_min 3B AND >= hr_min HR AND >= sb_min SB
    in a single season (a three-way compound "power/speed/gap" achievement)."""
    return _season_stat_achievers(
        con, year, {"3B": triple_min, "HR": hr_min, "SB": sb_min}, limit
    )


def best_win_pct_window(con: duckdb.DuckDBPyConnection, window_games: int,
                         team: str | None = None, limit: int = 20) -> pd.DataFrame:
    """Template 4: best winning percentage over any N-consecutive-game window,
    within a single season, across all teams and all seasons (or one team if given).
    Windows never cross a season boundary."""
    window_games = int(window_games)
    if window_games < 1:
        raise ValueError("window_games must be >= 1")

    team_filter = "AND team = ?" if team else ""

    sql = f"""
        WITH ordered AS (
            SELECT
                team, season, date, win,
                ROW_NUMBER() OVER (PARTITION BY team, season ORDER BY date) AS game_no
            FROM v_team_games
            WHERE 1=1 {team_filter}
        ),
        windowed AS (
            SELECT
                team, season, game_no, date AS window_end_date,
                LAG(date, {window_games - 1}) OVER (PARTITION BY team, season ORDER BY date) AS window_start_date,
                SUM(win) OVER (
                    PARTITION BY team, season ORDER BY date
                    ROWS BETWEEN {window_games - 1} PRECEDING AND CURRENT ROW
                ) AS wins
            FROM ordered
        )
        SELECT
            team, season, window_start_date, window_end_date, wins,
            wins::DOUBLE / ? AS win_pct
        FROM windowed
        WHERE game_no >= ?
        ORDER BY win_pct DESC, wins DESC, season DESC
        LIMIT ?
    """
    # Placeholder order: optional team filter (inside the ordered CTE),
    # then the win_pct divisor, then the game_no >= window_games threshold,
    # then limit.
    params: list = []
    if team:
        params.append(team)
    params.extend([window_games, window_games, limit])
    return con.execute(sql, params).fetchdf()
