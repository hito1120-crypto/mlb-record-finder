"""Simplified, independent WAR (Wins Above Replacement) calculation.

IMPORTANT: this is this project's own simplified re-implementation of the
public FanGraphs WAR (fWAR) methodology, NOT a reproduction of it. Numbers
here will NOT match fangraphs.com or Baseball-Reference (bWAR). Known
simplifications versus fWAR:
  - wOBA/FIP constants are only transcribed for 2015-2026 (see
    transform/war_constants.py) -- other seasons are unsupported for now.
  - FIP is computed at the league level as "every pitcher's innings, summed"
    rather than fWAR's "runs allowed by real games" league average; the two
    are very close in practice (FIP is calibrated to average to lgERA) but
    not bit-for-bit identical.
  - Baserunning value is stolen-base value only (runSB*SB + runCS*CS) --
    no wSB/wGDP/UBR-style "extra bases taken" component (Baseball Reference
    Runs, BsR, is out of scope here).
  - Fielding value is Statcast's own Outs Above Average -> runs conversion
    (statcast_oaa.fielding_runs_prevented) for 2016+. For 2015 (the one
    season in this project's supported range with no OAA data) fielding
    value is treated as 0 -- see season_war_leaderboard()'s docstring.
  - Park factor is a single-season, unregressed "(runs/game at home) /
    (runs/game on road)" ratio from Retrosheet Game Logs -- see
    query/park_factor.py's module docstring for the exact method and its
    known noise (cross-checked there against Lahman's own multi-year BPF).
  - Replacement level and the batting/pitching WAR pool split are both
    derived from the single published .294 replacement win% anchor via a
    50/50 split assumption -- see replacement_runs_pools()'s docstring.
  - A season with no Lahman data yet (i.e. still in progress -- Lahman is
    only published after a season ends) falls back to a PROVISIONAL
    leaderboard built from live MLB Stats API totals instead -- see
    _season_source() and ingest/mlb_stats_api.py's module docstring. Park
    factor is neutral (1.0) for such a season, since Retrosheet Game Logs
    (this project's park-factor source) also isn't published yet either.
Every entry point below returns its numbers accompanied by the caveat
string SIMPLIFIED_WAR_NOTICE so callers (CLI, NL2SQL) can surface it.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from ingest import mlb_stats_api
from query import park_factor
from transform.war_constants import (
    POSITION_ADJUSTMENT_PER_600PA,
    REPLACEMENT_LEVEL_WIN_PCT,
    constants_for_season,
)

SIMPLIFIED_WAR_NOTICE = (
    "This WAR is this tool's own simplified estimate, independently derived "
    "from public wOBA/FIP methodology -- it is NOT FanGraphs (fWAR) or "
    "Baseball-Reference (bWAR) and will not match those sites."
)

PROVISIONAL_WAR_NOTICE = (
    "This season has no Lahman data yet (Lahman is only published after a "
    "season ends), so these numbers are a PROVISIONAL in-progress-season "
    "estimate built from live MLB Stats API totals as of the last data "
    "refresh -- not a final, Lahman-verified season line. Fielding value "
    "(Statcast OAA) is still included where available; park factor is "
    "neutral (1.0) for provisional seasons, since Retrosheet Game Logs "
    "(this project's park-factor source) also isn't published for a season "
    "still in progress."
)


def _season_source(con: duckdb.DuckDBPyConnection, year: int) -> str:
    """'lahman' if `year` has real Lahman data loaded; otherwise 'mlbapi',
    auto-fetching+caching that season from the MLB Stats API on demand if it
    isn't already cached (see ingest/mlb_stats_api.py's module docstring for
    why Lahman can't ever cover a season still in progress)."""
    n = con.execute("SELECT COUNT(*) FROM lahman_teams WHERE yearID = ?", [year]).fetchone()[0]
    if n > 0:
        return "lahman"
    mlb_stats_api.ensure_season_loaded(con, year)
    return "mlbapi"


def compute_wraa(stats: dict, year: int) -> dict:
    """Batting value: wOBA and Weighted Runs Above Average for one player-season.

    `stats` must have (at least) AB, H, "2B", "3B", HR, BB, HBP, SF -- the raw
    Lahman batting columns summed across all of a player's stints in `year`.
    IBB defaults to 0 if absent/None (pre-1955 Lahman batting has no IBB column).
    """
    c = constants_for_season(year)

    singles = stats["H"] - stats["2B"] - stats["3B"] - stats["HR"]
    ibb = stats.get("IBB") or 0
    hbp = stats.get("HBP") or 0
    sf = stats.get("SF") or 0
    unintentional_bb = stats["BB"] - ibb

    # Standard wOBA denominator: AB + uBB + SF + HBP (IBB and sac bunts excluded).
    woba_pa = stats["AB"] + unintentional_bb + sf + hbp
    if woba_pa <= 0:
        return {"woba_pa": 0, "wOBA": None, "wRAA": 0.0}

    woba_num = (
        c["wBB"] * unintentional_bb
        + c["wHBP"] * hbp
        + c["w1B"] * singles
        + c["w2B"] * stats["2B"]
        + c["w3B"] * stats["3B"]
        + c["wHR"] * stats["HR"]
    )
    woba = woba_num / woba_pa
    wraa = (woba - c["wOBA"]) / c["wOBAScale"] * woba_pa
    return {"woba_pa": woba_pa, "wOBA": woba, "wRAA": wraa}


def _fip_runs(hr: float, bb: float, hbp: float, so: float, ip: float, cfip: float) -> float:
    if ip <= 0:
        return cfip
    return (13 * hr + 3 * (bb + hbp) - 2 * so) / ip + cfip


def compute_pitching_value(stats: dict, year: int, league_totals: dict) -> dict:
    """Pitching value: FIP and Runs Above Average for one player-season, relative
    to the league-average FIP computed from `league_totals` (that season's
    summed HR/BB/HBP/SO/IPouts across all pitchers -- see
    league_totals_for_season below).

    `stats` must have IPouts, HR, BB, HBP, SO summed across the player's stints.
    """
    c = constants_for_season(year)

    ip = stats["IPouts"] / 3
    if ip <= 0:
        return {"IP": 0, "FIP": None, "RAA": 0.0}

    fip = _fip_runs(stats["HR"], stats["BB"], stats.get("HBP") or 0, stats["SO"], ip, c["cFIP"])

    lg_ip = league_totals["IPouts"] / 3
    lg_fip = _fip_runs(
        league_totals["HR"], league_totals["BB"], league_totals.get("HBP") or 0,
        league_totals["SO"], lg_ip, c["cFIP"],
    )

    # Runs Above Average: lower FIP than league = positive value. Scaled by
    # innings pitched (per-9-innings rate difference x innings actually thrown).
    raa = (lg_fip - fip) / 9 * ip
    return {"IP": ip, "FIP": fip, "league_FIP": lg_fip, "RAA": raa}


def league_totals_for_season(con: duckdb.DuckDBPyConnection, year: int,
                              source: str = "lahman") -> dict:
    """Sums the FIP-input columns across every pitcher in `year`, for use as
    compute_pitching_value's league-average baseline. source="mlbapi" reads
    from the MLB Stats API fallback tables instead of Lahman (see
    _season_source)."""
    table, year_col = ("lahman_pitching", "yearID") if source == "lahman" else ("mlbapi_pitching", "season")
    row = con.execute(
        f"""
        SELECT SUM(IPouts) AS IPouts, SUM(HR) AS HR, SUM(BB) AS BB,
               SUM(HBP) AS HBP, SUM(SO) AS SO
        FROM {table}
        WHERE {year_col} = ?
        """,
        [year],
    ).fetchone()
    cols = ["IPouts", "HR", "BB", "HBP", "SO"]
    return dict(zip(cols, row))


def batting_wraa_leaderboard(con: duckdb.DuckDBPyConnection, year: int,
                              min_pa: int = 100, limit: int = 50) -> pd.DataFrame:
    """Batting wRAA leaderboard for one season (stints summed per player)."""
    df = con.execute(
        """
        SELECT
            p.full_name,
            b.yearID AS season,
            STRING_AGG(DISTINCT b.teamID, '/') AS teams,
            SUM(b.AB) AS AB, SUM(b.H) AS H, SUM(b."2B") AS "2B", SUM(b."3B") AS "3B",
            SUM(b.HR) AS HR, SUM(b.BB) AS BB, SUM(b.IBB) AS IBB, SUM(b.HBP) AS HBP,
            SUM(b.SF) AS SF
        FROM lahman_batting b
        JOIN v_people p ON b.playerID = p.playerID
        WHERE b.yearID = ?
        GROUP BY p.full_name, b.yearID
        """,
        [year],
    ).fetchdf()
    if df.empty:
        return df

    results = df.apply(lambda row: compute_wraa(row.to_dict(), year), axis=1, result_type="expand")
    df = pd.concat([df, results], axis=1)
    df = df[df["woba_pa"] >= min_pa]
    return df.sort_values("wRAA", ascending=False).head(limit).reset_index(drop=True)


def pitching_raa_leaderboard(con: duckdb.DuckDBPyConnection, year: int,
                              min_ip: float = 20.0, limit: int = 50) -> pd.DataFrame:
    """Pitching FIP-based Runs Above Average leaderboard for one season
    (stints summed per player)."""
    df = con.execute(
        """
        SELECT
            p.full_name,
            pit.yearID AS season,
            STRING_AGG(DISTINCT pit.teamID, '/') AS teams,
            SUM(pit.IPouts) AS IPouts, SUM(pit.HR) AS HR, SUM(pit.BB) AS BB,
            SUM(pit.HBP) AS HBP, SUM(pit.SO) AS SO
        FROM lahman_pitching pit
        JOIN v_people p ON pit.playerID = p.playerID
        WHERE pit.yearID = ?
        GROUP BY p.full_name, pit.yearID
        """,
        [year],
    ).fetchdf()
    if df.empty:
        return df

    league_totals = league_totals_for_season(con, year)
    results = df.apply(
        lambda row: compute_pitching_value(row.to_dict(), year, league_totals),
        axis=1, result_type="expand",
    )
    df = pd.concat([df, results], axis=1)
    df = df[df["IP"] >= min_ip]
    return df.sort_values("RAA", ascending=False).head(limit).reset_index(drop=True)


# --- Baserunning value (stolen-base value only) ------------------------------

def compute_baserunning_value(stats: dict, year: int) -> float:
    """Simplified baserunning value: the linear-weights run value of a
    player's stolen-base attempts (runSB per SB, runCS per CS -- both from
    the same season constants table as the wOBA weights). This does NOT
    include taking extra bases on hits, avoiding double plays, etc. (a full
    Baseball Reference Runs / BsR calculation) -- see this module's
    docstring."""
    c = constants_for_season(year)
    sb = stats.get("SB") or 0
    cs = stats.get("CS") or 0
    return c["runSB"] * sb + c["runCS"] * cs


# --- Fielding value (Statcast OAA, 2016+) ------------------------------------

def fielding_runs_for_season(con: duckdb.DuckDBPyConnection, year: int,
                              source: str = "lahman") -> pd.DataFrame:
    """Statcast's own outs-above-average -> runs conversion
    (fielding_runs_prevented), one row per playerID, for `year`.

    Returns an empty frame for years with no OAA data (statcast_oaa only
    covers 2016+): those players' fielding value is then implicitly 0 when
    left-joined in season_war_leaderboard, which is this project's chosen
    (documented, not "correct") treatment for 2015 -- OAA simply doesn't
    exist yet for that season, and Lahman's raw PO/A/E fielding stats don't
    support a comparably reliable per-play runs estimate without the
    shot-charting data that grounds OAA.

    source="mlbapi" (see _season_source) joins statcast_oaa directly on its
    own player_id (an MLB Advanced Media ID), matching the "mlbam_<id>"
    playerID scheme ingest/mlb_stats_api.py uses -- no Lahman crosswalk
    needed, since a rookie who debuted this season wouldn't be in
    lahman_people/player_id_lookup yet anyway."""
    if source != "lahman":
        return con.execute(
            """
            SELECT DISTINCT
                'mlbam_' || CAST(oaa.player_id AS VARCHAR) AS playerID,
                oaa.fielding_runs_prevented
            FROM statcast_oaa oaa
            WHERE oaa.season = ? AND oaa.pos = 'ALL'
            """,
            [year],
        ).fetchdf()
    return con.execute(
        """
        SELECT DISTINCT
            p.playerID,
            oaa.fielding_runs_prevented
        FROM statcast_oaa oaa
        JOIN player_id_lookup pl ON pl.key_mlbam = oaa.player_id
        JOIN v_people p ON p.retroID = pl.key_retro
        WHERE oaa.season = ? AND oaa.pos = 'ALL'
        """,
        [year],
    ).fetchdf()


# --- Positional adjustment (from Lahman Appearances) -------------------------

_POSITION_APPEARANCE_COLUMNS = {
    "C": "G_c", "1B": "G_1b", "2B": "G_2b", "3B": "G_3b", "SS": "G_ss",
    "LF": "G_lf", "CF": "G_cf", "RF": "G_rf", "DH": "G_dh",
}


def primary_positions_for_season(con: duckdb.DuckDBPyConnection, year: int) -> pd.DataFrame:
    """One row per player: their most-played position (by games, summed
    across stints) among the 9 position-adjustment categories, for `year`.
    A player with zero games at any of those (e.g. a pitcher who never
    batted) gets position=None, i.e. no positional adjustment either way."""
    cols_sql = ", ".join(f'SUM({col}) AS "{code}"' for code, col in _POSITION_APPEARANCE_COLUMNS.items())
    df = con.execute(
        f"""
        SELECT playerID, {cols_sql}
        FROM lahman_appearances
        WHERE yearID = ?
        GROUP BY playerID
        """,
        [year],
    ).fetchdf()
    if df.empty:
        df["primary_position"] = []
        return df

    position_cols = list(_POSITION_APPEARANCE_COLUMNS)
    games = df[position_cols]
    best = games.idxmax(axis=1)
    df["primary_position"] = best.where(games.max(axis=1) > 0, None)
    return df


def position_adjustment_runs(primary_position: str | None, pa: float) -> float:
    if not primary_position or pa <= 0:
        return 0.0
    return POSITION_ADJUSTMENT_PER_600PA.get(primary_position, 0.0) * pa / 600


# --- Replacement level --------------------------------------------------------

def replacement_runs_pools(con: duckdb.DuckDBPyConnection, year: int,
                            source: str = "lahman") -> dict:
    """Converts the published .294 replacement-level win% into a runs-per-PA
    (batting side) and runs-per-IP (pitching side) rate for `year`.

    Derivation: a team of replacement-level players would be expected to
    win REPLACEMENT_LEVEL_WIN_PCT of its games instead of .500. Over that
    season's actual average games-per-team (avg_games -- read from the data
    rather than hardcoded, so shortened seasons like 2020 scale correctly),
    that is a (0.5 - .294) * avg_games win deficit, converted to a runs
    deficit via that season's R/W constant. This project's own choice (not
    a published number) is to then split that runs pool 50/50 between
    batters and pitchers and spread each half evenly across the league's
    total batting PA / pitching IP that season -- FanGraphs instead uses an
    empirically-derived ~57/43 split and separate starter/reliever
    replacement levels, which this simplified version does not reproduce.

    source="mlbapi" (see _season_source) reads avg_games/n_teams and the PA/IP
    totals from the MLB Stats API fallback tables instead of Lahman -- for an
    in-progress season avg_games is then that season's games-played-so-far,
    which is exactly what's wanted: replacement level scales down along with
    however much of the season has actually been played, the same way it
    already scales down for a real shortened season like 2020.
    """
    c = constants_for_season(year)
    teams_table, batting_table, pitching_table, year_col = (
        ("lahman_teams", "lahman_batting", "lahman_pitching", "yearID") if source == "lahman"
        else ("mlbapi_teams", "mlbapi_batting", "mlbapi_pitching", "season")
    )
    teams_row = con.execute(
        f"SELECT COUNT(DISTINCT teamID) AS n_teams, AVG(G) AS avg_games FROM {teams_table} WHERE {year_col} = ?",
        [year],
    ).fetchone()
    n_teams, avg_games = teams_row

    win_deficit_per_team = (0.5 - REPLACEMENT_LEVEL_WIN_PCT) * avg_games
    total_runs_deficit = win_deficit_per_team * c["R_W"] * n_teams

    batting_row = con.execute(
        f"""
        SELECT SUM(AB) AS AB, SUM(BB) AS BB, SUM(IBB) AS IBB,
               SUM(HBP) AS HBP, SUM(SF) AS SF
        FROM {batting_table} WHERE {year_col} = ?
        """,
        [year],
    ).fetchone()
    ab, bb, ibb, hbp, sf = (v or 0 for v in batting_row)
    total_batting_pa = ab + (bb - ibb) + hbp + sf

    total_pitching_ip = con.execute(
        f"SELECT SUM(IPouts) FROM {pitching_table} WHERE {year_col} = ?", [year]
    ).fetchone()[0] / 3

    batting_pool = total_runs_deficit * 0.5
    pitching_pool = total_runs_deficit * 0.5
    return {
        "runs_per_pa": batting_pool / total_batting_pa,
        "runs_per_ip": pitching_pool / total_pitching_ip,
    }


# --- Final assembly: total simplified WAR -------------------------------------

def _team_game_weights(con: duckdb.DuckDBPyConnection, year: int, table: str,
                        weight_col: str) -> dict[str, dict[str, float]]:
    """{playerID: {teamID: weight}} for park-factor weighting across stints
    (table is lahman_batting or lahman_pitching; weight_col is G or IPouts)."""
    rows = con.execute(
        f"SELECT playerID, teamID, SUM({weight_col}) AS w FROM {table} "
        f"WHERE yearID = ? GROUP BY playerID, teamID",
        [year],
    ).fetchall()
    out: dict[str, dict[str, float]] = {}
    for player_id, team_id, w in rows:
        out.setdefault(player_id, {})[team_id] = w or 0
    return out


def season_war_leaderboard(con: duckdb.DuckDBPyConnection, year: int,
                            min_pa: int = 100, min_ip: float = 20.0,
                            limit: int = 50) -> pd.DataFrame:
    """Combined simplified-WAR leaderboard for one season: batting side
    (wRAA, park-adjusted, + baserunning + fielding + positional adjustment +
    batting replacement runs) plus pitching side (FIP RAA, park-adjusted, +
    pitching replacement runs), summed and converted to wins via R/W.

    A two-way player (e.g. Shohei Ohtani) gets both sides added together,
    same as official WAR does. Players who only qualify on one side (e.g. a
    pure pitcher with < min_pa plate appearances) still get that side's
    value; the other side is simply 0.
    """
    c = constants_for_season(year)
    source = _season_source(con, year)
    half_pfs = park_factor.team_half_park_factors(con, year)
    repl = replacement_runs_pools(con, year, source=source)

    if source == "lahman":
        positions = primary_positions_for_season(con, year).set_index("playerID")
        bat_team_weights = _team_game_weights(con, year, "lahman_batting", "G")
        pit_team_weights = _team_game_weights(con, year, "lahman_pitching", "IPouts")
    else:
        # MLB Stats API's season-aggregate endpoint doesn't expose per-team
        # stint splits, so park factor falls back to weighted_half_park_factor's
        # own neutral-1.0 default below -- moot anyway, since a provisional
        # (no-Lahman-yet) season also has no Retrosheet Game Logs to compute a
        # real park factor from in the first place (half_pfs is already {}).
        positions = con.execute(
            "SELECT playerID, primary_position FROM mlbapi_batting WHERE season = ?", [year],
        ).fetchdf().set_index("playerID")
        bat_team_weights = {}
        pit_team_weights = {}

    fielding = fielding_runs_for_season(con, year, source=source).set_index("playerID")

    if source == "lahman":
        batting = con.execute(
            """
            SELECT
                b.playerID, p.full_name,
                STRING_AGG(DISTINCT b.teamID, '/') AS teams,
                SUM(b.AB) AS AB, SUM(b.H) AS H, SUM(b."2B") AS "2B", SUM(b."3B") AS "3B",
                SUM(b.HR) AS HR, SUM(b.BB) AS BB, SUM(b.IBB) AS IBB, SUM(b.HBP) AS HBP,
                SUM(b.SF) AS SF, SUM(b.SB) AS SB, SUM(b.CS) AS CS
            FROM lahman_batting b
            JOIN v_people p ON b.playerID = p.playerID
            WHERE b.yearID = ?
            GROUP BY b.playerID, p.full_name
            """,
            [year],
        ).fetchdf()
    else:
        batting = con.execute(
            """
            SELECT
                playerID, full_name, teamID AS teams,
                AB, H, "2B", "3B", HR, BB, IBB, HBP, SF, SB, CS
            FROM mlbapi_batting
            WHERE season = ?
            """,
            [year],
        ).fetchdf()

    if source == "lahman":
        pitching = con.execute(
            """
            SELECT
                pit.playerID, p.full_name,
                STRING_AGG(DISTINCT pit.teamID, '/') AS teams,
                SUM(pit.IPouts) AS IPouts, SUM(pit.HR) AS HR, SUM(pit.BB) AS BB,
                SUM(pit.HBP) AS HBP, SUM(pit.SO) AS SO
            FROM lahman_pitching pit
            JOIN v_people p ON pit.playerID = p.playerID
            WHERE pit.yearID = ?
            GROUP BY pit.playerID, p.full_name
            """,
            [year],
        ).fetchdf()
    else:
        pitching = con.execute(
            """
            SELECT playerID, full_name, teamID AS teams, IPouts, HR, BB, HBP, SO
            FROM mlbapi_pitching
            WHERE season = ?
            """,
            [year],
        ).fetchdf()

    league_totals = league_totals_for_season(con, year, source=source)

    batting_rows = {}
    for row in batting.itertuples():
        stats = dict(zip(batting.columns, row[1:]))
        wraa_result = compute_wraa(stats, year)
        pa = wraa_result["woba_pa"]
        half_pf = park_factor.weighted_half_park_factor(
            bat_team_weights.get(row.playerID, {}), half_pfs
        )
        wraa_park_adj = wraa_result["wRAA"] / half_pf if half_pf else wraa_result["wRAA"]
        baserunning = compute_baserunning_value(stats, year)
        fielding_runs = fielding.loc[row.playerID, "fielding_runs_prevented"] \
            if row.playerID in fielding.index else 0.0
        primary_pos = positions.loc[row.playerID, "primary_position"] \
            if row.playerID in positions.index else None
        pos_adj = position_adjustment_runs(primary_pos, pa)
        repl_runs = repl["runs_per_pa"] * pa
        batting_side_runs = wraa_park_adj + baserunning + fielding_runs + pos_adj + repl_runs
        batting_rows[row.playerID] = {
            "full_name": row.full_name, "teams_batting": row.teams,
            "PA": pa, "wOBA": wraa_result["wOBA"], "wRAA": wraa_result["wRAA"],
            "wRAA_park_adj": wraa_park_adj, "baserunning_runs": baserunning,
            "fielding_runs": fielding_runs, "primary_position": primary_pos,
            "position_adj_runs": pos_adj, "batting_replacement_runs": repl_runs,
            "batting_side_runs": batting_side_runs,
        }

    pitching_rows = {}
    for row in pitching.itertuples():
        stats = dict(zip(pitching.columns, row[1:]))
        pitch_result = compute_pitching_value(stats, year, league_totals)
        ip = pitch_result["IP"]
        half_pf = park_factor.weighted_half_park_factor(
            pit_team_weights.get(row.playerID, {}), half_pfs
        )
        raa_park_adj = pitch_result["RAA"] / half_pf if half_pf else pitch_result["RAA"]
        repl_runs = repl["runs_per_ip"] * ip
        pitching_side_runs = raa_park_adj + repl_runs
        pitching_rows[row.playerID] = {
            "full_name": row.full_name, "teams_pitching": row.teams,
            "IP": ip, "FIP": pitch_result["FIP"], "pitching_RAA": pitch_result["RAA"],
            "pitching_RAA_park_adj": raa_park_adj, "pitching_replacement_runs": repl_runs,
            "pitching_side_runs": pitching_side_runs,
        }

    all_ids = set(batting_rows) | set(pitching_rows)
    combined = []
    for pid in all_ids:
        b = batting_rows.get(pid, {})
        p = pitching_rows.get(pid, {})
        full_name = b.get("full_name") or p.get("full_name")
        pa = b.get("PA", 0)
        ip = p.get("IP", 0)
        if pa < min_pa and ip < min_ip:
            continue
        batting_side_runs = b.get("batting_side_runs", 0.0)
        pitching_side_runs = p.get("pitching_side_runs", 0.0)
        total_war = (batting_side_runs + pitching_side_runs) / c["R_W"]
        combined.append({
            "full_name": full_name,
            "teams": b.get("teams_batting") or p.get("teams_pitching"),
            "PA": pa, "wRAA_park_adj": b.get("wRAA_park_adj", 0.0),
            "baserunning_runs": b.get("baserunning_runs", 0.0),
            "fielding_runs": b.get("fielding_runs", 0.0),
            "primary_position": b.get("primary_position"),
            "position_adj_runs": b.get("position_adj_runs", 0.0),
            "IP": ip, "FIP": p.get("FIP"),
            "pitching_RAA_park_adj": p.get("pitching_RAA_park_adj", 0.0),
            "WAR": total_war,
        })

    df = pd.DataFrame(combined)
    if not df.empty:
        df = df.sort_values("WAR", ascending=False).head(limit).reset_index(drop=True)
    # .attrs (not a real column) so callers can tell a provisional,
    # MLB-Stats-API-sourced leaderboard apart from a Lahman-backed one -- e.g.
    # cli.py checks this to decide whether to print a provisional-data notice.
    df.attrs["data_source"] = source
    return df
