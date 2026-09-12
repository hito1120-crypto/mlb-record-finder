"""The record-finder query templates (Phase 1's five, plus three Statcast
leaderboard templates added afterward).

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


# --- Statcast leaderboard templates (added after Phase 1) --------------------
#
# These three read directly from the already-ingested statcast_pitches /
# statcast_sprint_speed / player_id_lookup tables (see ingest/statcast.py) --
# no new per-query network calls.
#
# IMPORTANT: statcast_pitches.player_name is the PITCHER throwing the pitch,
# not the batter (verified against the ingested data: for a fixed
# player_name, `pitcher` is constant across rows while `batter` varies).
# The two batter-side leaderboards below (barrel/hard-hit%, expected stats)
# therefore join on the numeric `batter` MLBAM ID against player_id_lookup
# (ingest/statcast.py's Chadwick register pull) to get the batter's name,
# rather than grouping by player_name.
#
# The MLBAM-assigned launch_speed_angle column is used as-is
# for barrel classification rather than reimplementing Statcast's own
# speed/angle sweet-spot rule: confirmed by inspecting the ingested data
# (SELECT launch_speed_angle, MIN/MAX/AVG(launch_speed), ...) that
# launch_speed_angle = 6 rows have exit velocity 97.5-122.9 mph (avg ~105)
# and launch angle 6-47 deg (avg ~26) -- i.e. exactly Baseball Savant's
# documented "Barrel" bucket. The full 1-6 scale (from that same inspection)
# is: 1=Weak, 2=Topped, 3=Under, 4=Flare/Burner, 5=Solid Contact, 6=Barrel.
BARREL_LAUNCH_SPEED_ANGLE = 6
HARD_HIT_MIN_EXIT_VELO = 95.0

# Standard at-bat exclusions (events that count as a plate appearance but
# not an at-bat), used to compute BA/SLG/xBA/xSLG. wOBA uses woba_denom
# instead, which Statcast already assigns per PA.
NON_AB_EVENTS = (
    "walk", "intent_walk", "hit_by_pitch",
    "sac_fly", "sac_fly_double_play", "sac_bunt", "sac_bunt_double_play",
    "catcher_interf", "truncated_pa",
)
HIT_BASES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}


def barrel_hard_hit_ranking(con: duckdb.DuckDBPyConnection, season: int,
                             min_batted_balls: int = 50, limit: int = 50) -> pd.DataFrame:
    """Template 6: Barrel% and Hard-Hit% (exit velocity >= 95 mph) ranking
    for a given season, among players with at least min_batted_balls batted
    balls tracked by Statcast."""
    return con.execute(
        f"""
        SELECT
            (pl.name_first || ' ' || pl.name_last) AS player_name,
            COUNT(*) AS batted_balls,
            SUM(CASE WHEN sp.launch_speed_angle = {BARREL_LAUNCH_SPEED_ANGLE} THEN 1 ELSE 0 END) AS barrels,
            SUM(CASE WHEN sp.launch_speed_angle = {BARREL_LAUNCH_SPEED_ANGLE} THEN 1 ELSE 0 END)::DOUBLE
                / COUNT(*) AS barrel_pct,
            SUM(CASE WHEN sp.launch_speed >= {HARD_HIT_MIN_EXIT_VELO} THEN 1 ELSE 0 END)::DOUBLE
                / COUNT(*) AS hard_hit_pct,
            AVG(sp.launch_speed) AS avg_exit_velo
        FROM statcast_pitches sp
        JOIN player_id_lookup pl ON sp.batter = pl.key_mlbam
        WHERE sp.game_year = ?
          AND sp.launch_speed IS NOT NULL
        GROUP BY sp.batter, pl.name_first, pl.name_last
        HAVING COUNT(*) >= ?
        ORDER BY barrel_pct DESC
        LIMIT ?
        """,
        [season, min_batted_balls, limit],
    ).fetchdf()


def expected_vs_actual_ranking(con: duckdb.DuckDBPyConnection, season: int,
                                min_pa: int = 100, limit: int = 50,
                                luck_threshold: float = 0.015) -> pd.DataFrame:
    """Template 7: xwOBA/xBA/xSLG ranking for a season vs. the player's actual
    wOBA/BA/SLG, flagging whether they're over- or under-performing their
    batted-ball quality (min_pa = minimum plate appearances)."""
    non_ab_list = ", ".join(f"'{e}'" for e in NON_AB_EVENTS)
    hit_bases_case = " ".join(f"WHEN events = '{ev}' THEN {n}" for ev, n in HIT_BASES.items())
    is_hit_list = ", ".join(f"'{ev}'" for ev in HIT_BASES)

    sql = f"""
        WITH pa AS (
            SELECT
                sp.batter,
                (pl.name_first || ' ' || pl.name_last) AS player_name,
                CASE WHEN sp.events NOT IN ({non_ab_list}) THEN 1 ELSE 0 END AS is_ab,
                CASE WHEN sp.events IN ({is_hit_list}) THEN 1 ELSE 0 END AS is_hit,
                CASE {hit_bases_case} ELSE 0 END AS bases,
                sp.estimated_ba_using_speedangle,
                sp.estimated_slg_using_speedangle,
                sp.woba_denom,
                sp.woba_value,
                sp.estimated_woba_using_speedangle
            FROM statcast_pitches sp
            JOIN player_id_lookup pl ON sp.batter = pl.key_mlbam
            WHERE sp.game_year = ?
              AND sp.events IS NOT NULL
        )
        SELECT
            player_name,
            COUNT(*) AS pa,
            SUM(is_ab) AS ab,
            SUM(is_hit)::DOUBLE / NULLIF(SUM(is_ab), 0) AS ba,
            AVG(CASE WHEN is_ab = 1 THEN COALESCE(estimated_ba_using_speedangle, 0) END) AS xba,
            SUM(bases)::DOUBLE / NULLIF(SUM(is_ab), 0) AS slg,
            AVG(CASE WHEN is_ab = 1 THEN COALESCE(estimated_slg_using_speedangle, 0) END) AS xslg,
            SUM(woba_value)::DOUBLE / NULLIF(SUM(woba_denom), 0) AS woba,
            AVG(CASE WHEN woba_denom = 1 THEN estimated_woba_using_speedangle END) AS xwoba
        FROM pa
        GROUP BY batter, player_name
        HAVING COUNT(*) >= ?
        ORDER BY xwoba DESC
        LIMIT ?
    """
    df = con.execute(sql, [season, min_pa, limit]).fetchdf()
    if df.empty:
        return df

    df["ba_diff"] = df["ba"] - df["xba"]
    df["slg_diff"] = df["slg"] - df["xslg"]
    df["woba_diff"] = df["woba"] - df["xwoba"]

    def _luck_flag(diff: float) -> str:
        if pd.isna(diff):
            return ""
        if diff > luck_threshold:
            return "overperforming"
        if diff < -luck_threshold:
            return "underperforming"
        return "as expected"

    df["luck"] = df["woba_diff"].apply(_luck_flag)
    return df


def sprint_speed_ranking(con: duckdb.DuckDBPyConnection, season: int,
                          min_opportunities: int = 10, limit: int = 50) -> pd.DataFrame:
    """Template 8: Sprint Speed ranking for a season (min_opportunities =
    Statcast's minimum number of "competitive run" opportunities)."""
    return con.execute(
        """
        SELECT full_name, team, position, age, competitive_runs, sprint_speed
        FROM statcast_sprint_speed
        WHERE season = ?
          AND competitive_runs >= ?
        ORDER BY sprint_speed DESC
        LIMIT ?
        """,
        [season, min_opportunities, limit],
    ).fetchdf()


# --- Retrosheet play-by-play templates (Phase 2) -----------------------------
#
# Both templates read from transform/schema.sql's v_run_expectancy /
# v_win_probability / v_play_leverage views, which are themselves built on
# ingest/retrosheet_playbyplay.py's parsed play-by-play data. See that
# schema's comments for exactly how run expectancy, win probability, and
# leverage index are computed (all approximations -- there isn't enough
# ingested play-by-play history yet for an empirical win-probability table).

def leadoff_and_walkoff_hr_games(con: duckdb.DuckDBPyConnection,
                                  limit: int = 50) -> pd.DataFrame:
    """Template 9: games where the very first plate appearance of the game
    was a home run ("leadoff home run") AND the game ended on a walk-off home
    run (bottom of the 9th or later, by a team that was tied or trailing
    before that at-bat and takes the lead on it)."""
    return con.execute(
        """
        WITH leadoff AS (
            SELECT DISTINCT game_id, batter_id
            FROM retrosheet_playbyplay
            WHERE is_first_play_of_game AND event_type = 'home_run'
        ),
        walkoff AS (
            SELECT
                game_id, season, game_date, home_team, visiting_team,
                inning AS walkoff_inning, batter_id AS walkoff_batter_id,
                home_score_after, away_score_after
            FROM retrosheet_playbyplay
            WHERE is_last_play_of_game
              AND event_type = 'home_run'
              AND batting_team_home
              AND inning >= 9
              AND home_score_before <= away_score_before
              AND home_score_after > away_score_after
        )
        SELECT
            w.game_id,
            w.season,
            w.game_date,
            w.home_team,
            w.visiting_team,
            ln.full_name AS leadoff_batter,
            w.walkoff_inning,
            wn.full_name AS walkoff_batter,
            w.home_score_after AS final_home_score,
            w.away_score_after AS final_away_score
        FROM walkoff w
        JOIN leadoff l ON l.game_id = w.game_id
        LEFT JOIN v_playbyplay_names ln ON ln.retroID = l.batter_id
        LEFT JOIN v_playbyplay_names wn ON wn.retroID = w.walkoff_batter_id
        ORDER BY w.game_date DESC
        LIMIT ?
        """,
        [limit],
    ).fetchdf()


def highest_leverage_plays(con: duckdb.DuckDBPyConnection,
                            start_date: str | None = None, end_date: str | None = None,
                            player_name: str | None = None, limit: int = 50) -> pd.DataFrame:
    """Template 10: the individual plate appearances with the largest
    win-probability swing (leverage index), optionally restricted to a date
    range (start_date/end_date as "YYYY-MM-DD") and/or a batter name
    (case-insensitive substring match)."""
    conditions = []
    params: list = []
    if start_date:
        conditions.append("game_date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("game_date <= ?")
        params.append(end_date)
    if player_name:
        conditions.append("batter_name ILIKE ?")
        params.append(f"%{player_name}%")
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            game_id, game_date, home_team, visiting_team, inning,
            batter_name, event_type, event_raw,
            win_prob_home_before, win_prob_home_after, leverage_index
        FROM v_play_leverage
        {where_clause}
        ORDER BY leverage_index DESC
        LIMIT ?
    """
    params.append(limit)
    return con.execute(sql, params).fetchdf()


# --- Statcast bat-tracking / arm-angle templates (Phase 4) -------------------
#
# bat_speed / swing_length (and, for a later template, arm_angle) are plain
# columns on statcast_pitches already -- Baseball Savant added them to the
# same pitch-level CSV export that ingest/statcast.py pulls via
# pybaseball.statcast(), so no new ingest step or API call was needed; simply
# re-running ingest/statcast.py's load_to_duckdb() against the already-cached
# parquet files picked them up. Confirmed via a throwaway pybaseball venv
# (see the Phase 4 investigation) that both columns are non-null only on
# pitches where the batter actually took a swing (~46% of pitches in a
# sampled day), which is why min_swings below counts swings, not pitches.

def bat_speed_ranking(con: duckdb.DuckDBPyConnection, season: int,
                       min_swings: int = 50, limit: int = 50) -> pd.DataFrame:
    """Template 11: Bat Speed / Swing Length ranking for a season, aggregated
    per batter (joined against player_id_lookup the same way
    barrel_hard_hit_ranking is, since statcast_pitches.player_name is the
    pitcher, not the batter -- see that template's module note above)."""
    return con.execute(
        """
        SELECT
            (pl.name_first || ' ' || pl.name_last) AS player_name,
            COUNT(*) AS swings,
            AVG(sp.bat_speed) AS avg_bat_speed,
            MAX(sp.bat_speed) AS max_bat_speed,
            AVG(sp.swing_length) AS avg_swing_length
        FROM statcast_pitches sp
        JOIN player_id_lookup pl ON sp.batter = pl.key_mlbam
        WHERE sp.game_year = ?
          AND sp.bat_speed IS NOT NULL
          AND sp.swing_length IS NOT NULL
        GROUP BY sp.batter, pl.name_first, pl.name_last
        HAVING COUNT(*) >= ?
        ORDER BY avg_bat_speed DESC
        LIMIT ?
        """,
        [season, min_swings, limit],
    ).fetchdf()


# --- Statcast fielding (OAA) template (Phase 4) ------------------------------
#
# Outs Above Average is a precomputed Baseball Savant leaderboard, not a
# pitch-level column, so it arrives via ingest/statcast.py's download_oaa()
# rather than out of statcast_pitches. The one thing to know when reading
# the query below: `pos` is the position the leaderboard was *queried* for
# (which set of fielding chances the OAA counts), while `primary_position`
# is where that player mostly played. They often differ -- a 2B who spent
# time in left shows up under both "2B" and "OF" with different OAA values
# -- which is why pos is a filter column and not just cosmetic. See that
# ingest module's docstring for the rest of the leaderboard's quirks
# (no attempts column, catchers excluded).

def oaa_ranking(con: duckdb.DuckDBPyConnection, season: int, pos: str = "ALL",
                 limit: int = 50) -> pd.DataFrame:
    """Template 12: Outs Above Average (OAA) fielding ranking for a season at
    a given position (pos is one of ingest.statcast.OAA_POSITIONS' canonical
    codes: 1B/2B/3B/SS/LF/CF/RF, the IF/OF aggregates, or ALL).

    Only the headline metrics are returned; the leaderboard's directional and
    batter-handedness breakdowns (outs_above_average_infront / _behind /
    _lateral_* / _rhh / _lhh) are ingested into statcast_oaa but deliberately
    left out of this ranking."""
    return con.execute(
        """
        SELECT
            full_name,
            team,
            primary_position,
            outs_above_average,
            fielding_runs_prevented,
            actual_success_rate,
            adj_estimated_success_rate,
            diff_success_rate
        FROM statcast_oaa
        WHERE season = ?
          AND pos = ?
        ORDER BY outs_above_average DESC, fielding_runs_prevented DESC
        LIMIT ?
        """,
        [season, pos.upper(), limit],
    ).fetchdf()
