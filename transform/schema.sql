-- Convenience views built on top of the raw tables that ingest/*.py load.
-- Safe to re-run: every statement is CREATE OR REPLACE.
-- Applied automatically by cli.py on startup, so it always reflects
-- whatever raw tables are currently present in the DuckDB file.

-- Player full names (Lahman People.csv has separate first/last name columns).
-- retroID is Lahman's crosswalk to the batter/pitcher IDs used throughout
-- Retrosheet's play-by-play data (e.g. retrosheet_playbyplay.batter_id),
-- which are a different ID scheme from playerID -- see v_playbyplay_names.
CREATE OR REPLACE VIEW v_people AS
SELECT
    playerID,
    nameFirst,
    nameLast,
    (nameFirst || ' ' || nameLast) AS full_name,
    birthYear,
    debut,
    finalGame,
    retroID
FROM lahman_people;

-- One row per franchise per season, with the franchise's stable ID/name so a
-- team can be tracked across relocations and renames (e.g. Brooklyn/LA
-- Dodgers both roll up under franchID 'LAD').
CREATE OR REPLACE VIEW v_team_seasons AS
SELECT
    t.yearID,
    t.teamID,
    t.franchID,
    t.name AS team_name,
    f.franchName
FROM lahman_teams t
JOIN lahman_teams_franchises f ON t.franchID = f.franchID;

-- The most recent name/teamID on record for each franchise -- used to build
-- a human-readable team picker in the CLI.
CREATE OR REPLACE VIEW v_franchise_current AS
SELECT franchID, team_name, teamID, franchName
FROM (
    SELECT
        franchID, team_name, teamID, franchName,
        ROW_NUMBER() OVER (PARTITION BY franchID ORDER BY yearID DESC) AS rn
    FROM v_team_seasons
)
WHERE rn = 1;

-- Batting rows (one per player/season/stint) tagged with the franchise they
-- played for, for all-time-by-franchise aggregations.
CREATE OR REPLACE VIEW v_batting_by_franchise AS
SELECT
    b.*,
    ts.franchID,
    ts.franchName
FROM lahman_batting b
JOIN v_team_seasons ts ON b.teamID = ts.teamID AND b.yearID = ts.yearID;

-- Statcast rows that are batted-ball home runs, with the exit-velocity /
-- distance fields already non-null.
CREATE OR REPLACE VIEW v_statcast_home_runs AS
SELECT *
FROM statcast_pitches
WHERE events = 'home_run'
  AND launch_speed IS NOT NULL
  AND hit_distance_sc IS NOT NULL;

-- Retrosheet game logs unpivoted to one row per team per game (rather than
-- one row per game with a home/visiting pair), with a win flag. This is the
-- shape template 4 (best N-game win-percentage window) needs.
CREATE OR REPLACE VIEW v_team_games AS
SELECT
    season,
    date,
    home_team AS team,
    visiting_team AS opponent,
    home_score AS runs_for,
    visiting_score AS runs_against,
    CASE WHEN home_score > visiting_score THEN 1 ELSE 0 END AS win,
    'H' AS home_away
FROM retrosheet_gamelogs
WHERE home_score IS NOT NULL AND visiting_score IS NOT NULL
UNION ALL
SELECT
    season,
    date,
    visiting_team AS team,
    home_team AS opponent,
    visiting_score AS runs_for,
    home_score AS runs_against,
    CASE WHEN visiting_score > home_score THEN 1 ELSE 0 END AS win,
    'V' AS home_away
FROM retrosheet_gamelogs
WHERE home_score IS NOT NULL AND visiting_score IS NOT NULL;

-- Retrosheet batter/pitcher IDs (e.g. retrosheet_playbyplay.batter_id, format
-- "troum001") are a different ID scheme from Lahman's playerID ("troutmi01"),
-- so play-by-play rows need this separate lookup rather than v_people's
-- playerID join used by the Lahman-based templates.
CREATE OR REPLACE VIEW v_playbyplay_names AS
SELECT retroID, full_name
FROM v_people
WHERE retroID IS NOT NULL;

-- --- Run expectancy / win probability / leverage (Phase 2) ------------------
--
-- Built entirely from retrosheet_playbyplay (see
-- ingest/retrosheet_playbyplay.py and ingest/_playbyplay_parser.py for how
-- that table's bases_before/bases_after/outs_before/runs_scored columns are
-- derived). All three views below are necessarily approximations -- see each
-- one's docstring for what's simplified and why.

-- Run Expectancy Matrix: for each of the 8 base states (bases_before, a
-- 3-character "1st/2nd/3rd occupied?" string like "101") x 3 out counts, the
-- average number of runs scored from that state through the end of the
-- half-inning, inclusive of the play that produced the state (the standard
-- "RE24" convention).
--
-- outs_before should always be 0/1/2 (a plate appearance can't start with 3
-- outs already recorded -- that ends the half-inning), so the WHERE clause
-- below is a defensive filter, not part of the intended 8x3 shape: a small
-- number of rows (well under 0.1% of the ingested play-by-play) carry
-- outs_before = 3, which traced back to a handful of remaining
-- inconsistencies in the ingested play-by-play data itself rather than a bug
-- in this view -- see ingest/_playbyplay_parser.py's module docstring.
CREATE OR REPLACE VIEW v_run_expectancy AS
WITH rest_of_inning AS (
    SELECT
        bases_before,
        outs_before,
        SUM(runs_scored) OVER (
            PARTITION BY game_id, inning, batting_team_home
            ORDER BY play_seq
            ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
        ) AS runs_rest_of_half_inning
    FROM retrosheet_playbyplay
    WHERE outs_before IN (0, 1, 2)
)
SELECT
    bases_before AS bases_state,
    outs_before AS outs,
    AVG(runs_rest_of_half_inning) AS run_expectancy,
    COUNT(*) AS n_plays
FROM rest_of_inning
GROUP BY bases_before, outs_before
ORDER BY outs, bases_state;

-- Total runs scored by one team in one half-inning -- the run-environment
-- input (mean and variance) for the win-probability approximation below.
CREATE OR REPLACE VIEW v_half_inning_runs AS
SELECT game_id, inning, batting_team_home, SUM(runs_scored) AS runs
FROM retrosheet_playbyplay
GROUP BY game_id, inning, batting_team_home;

-- Win probability (approximate): P(home team wins) at a given point in the
-- game, modeled -- not looked up from an empirical table, since one season
-- of play-by-play data is too little to bin reliably by
-- inning x score-diff x outs x base-state -- as a projected final score
-- differential with normally-distributed remaining uncertainty, collapsed to
-- a probability via a logistic CDF (chosen over the normal CDF purely
-- because DuckDB has no erf()/normal-CDF builtin -- a logistic curve is a
-- standard, close stand-in), matched to the same variance via the
-- sqrt(3)/pi scale conversion below):
--
--   projected_diff = (home_score - away_score at this instant)
--                     + [batting team's v_run_expectancy for its current
--                        base/out state, signed for home/away]
--                     + [average runs per half-inning, x each team's number
--                        of remaining FULL half-innings this regulation
--                        (9-inning) game, signed for home/away]
--   win_prob_home  = logistic(projected_diff / scale(variance))
--
-- Known limitations (acceptable for an approximate leverage-index ranking,
-- not for precision win-probability work):
--   - No home-field-advantage term -- first pitch of a scoreless game
--     computes to exactly 0.5, not the empirical ~54%.
--   - Extra innings (inning > 9) are treated as if inning 9 were still in
--     progress (effective_inning is capped at 9), which under-counts how
--     little of the game is actually left the deeper extra innings go.
--   - is_last_play_of_game is used to force win_prob_home_after to exactly
--     1.0/0.0 from the final score (see COALESCE below), which is what
--     makes a walk-off/final-out play register its full leverage swing
--     regardless of what the general model would have said.
CREATE OR REPLACE VIEW v_win_probability AS
WITH mu AS (
    SELECT run_expectancy AS mu_inning
    FROM v_run_expectancy WHERE bases_state = '000' AND outs = 0
),
var_inning AS (
    SELECT VARIANCE(runs) AS var_inning FROM v_half_inning_runs
),
-- Each play contributes two rows to evaluate the model at: the state just
-- before the play, and the state the play resolves into (either later in
-- the same half-inning, or -- if outs reach 3 -- the start of the next
-- half-inning with bases reset and the batting team flipped).
states AS (
    SELECT game_id, play_seq, 'before' AS which,
           inning, batting_team_home, outs_before AS outs, bases_before AS bases,
           home_score_before AS home_score, away_score_before AS away_score
    FROM retrosheet_playbyplay

    UNION ALL

    SELECT
        game_id, play_seq, 'after' AS which,
        CASE WHEN outs_before + outs_on_play >= 3 AND batting_team_home
             THEN inning + 1 ELSE inning END AS inning,
        CASE WHEN outs_before + outs_on_play >= 3
             THEN NOT batting_team_home ELSE batting_team_home END AS batting_team_home,
        CASE WHEN outs_before + outs_on_play >= 3 THEN 0
             ELSE outs_before + outs_on_play END AS outs,
        CASE WHEN outs_before + outs_on_play >= 3 THEN '000' ELSE bases_after END AS bases,
        home_score_after AS home_score,
        away_score_after AS away_score
    FROM retrosheet_playbyplay
    WHERE NOT is_last_play_of_game
),
joined AS (
    SELECT
        s.*,
        LEAST(s.inning, 9) AS eff_inning,
        re.run_expectancy AS re_current,
        mu.mu_inning,
        vi.var_inning
    FROM states s
    JOIN v_run_expectancy re ON re.bases_state = s.bases AND re.outs = s.outs
    CROSS JOIN mu
    CROSS JOIN var_inning vi
),
future AS (
    SELECT
        *,
        -- Home's remaining FULL half-innings (excluding its own current
        -- partial one, already captured by re_current when home is
        -- batting): if away is currently batting, home still has its
        -- upcoming bottom of this same inning as a full future inning on
        -- top of the (9 - eff_inning) innings after that.
        CASE WHEN NOT batting_team_home THEN GREATEST(9 - eff_inning, 0) + 1
             ELSE GREATEST(9 - eff_inning, 0) END AS home_future_full,
        GREATEST(9 - eff_inning, 0) AS away_future_full
    FROM joined
),
projected AS (
    SELECT
        *,
        (home_score - away_score)
            + (CASE WHEN batting_team_home THEN re_current ELSE 0 END)
            - (CASE WHEN NOT batting_team_home THEN re_current ELSE 0 END)
            + home_future_full * mu_inning
            - away_future_full * mu_inning AS projected_diff,
        -- Variance of the projected differential: each remaining full
        -- half-inning contributes var_inning independently, and the current
        -- half-inning contributes only its remaining fraction (by outs),
        -- floored so it never collapses to zero with 2 outs.
        GREATEST(
            (home_future_full + away_future_full + (3 - outs) / 3.0) * var_inning,
            0.1 * var_inning
        ) AS var_total
    FROM future
),
wp AS (
    SELECT
        game_id, play_seq, which,
        LEAST(GREATEST(
            1.0 / (1.0 + EXP(-projected_diff / (SQRT(var_total) * 0.5513288954217921))),
        0.001), 0.999) AS win_prob_home
    FROM projected
)
SELECT
    pb.game_id,
    pb.play_seq,
    wb.win_prob_home AS win_prob_home_before,
    COALESCE(
        wa.win_prob_home,
        CASE WHEN pb.home_score_after > pb.away_score_after THEN 1.0 ELSE 0.0 END
    ) AS win_prob_home_after
FROM retrosheet_playbyplay pb
JOIN wp wb ON wb.game_id = pb.game_id AND wb.play_seq = pb.play_seq AND wb.which = 'before'
LEFT JOIN wp wa ON wa.game_id = pb.game_id AND wa.play_seq = pb.play_seq AND wa.which = 'after';

-- Leverage index: how much a single play swung the home team's win
-- probability (|after - before|). Uses the play's *actual* recorded outcome
-- rather than the textbook leverage-index definition's average over every
-- hypothetical outcome of that base-out-score state -- see
-- ingest/_playbyplay_parser.py's module docstring for why.
CREATE OR REPLACE VIEW v_play_leverage AS
SELECT
    pb.game_id,
    pb.season,
    pb.game_date,
    pb.visiting_team,
    pb.home_team,
    pb.inning,
    pb.batting_team_home,
    pb.batter_id,
    n.full_name AS batter_name,
    pb.event_type,
    pb.event_raw,
    pb.outs_before,
    pb.bases_before,
    pb.runs_scored,
    pb.home_score_after,
    pb.away_score_after,
    pb.is_last_play_of_game,
    wp.win_prob_home_before,
    wp.win_prob_home_after,
    ABS(wp.win_prob_home_after - wp.win_prob_home_before) AS leverage_index
FROM retrosheet_playbyplay pb
JOIN v_win_probability wp ON wp.game_id = pb.game_id AND wp.play_seq = pb.play_seq
LEFT JOIN v_playbyplay_names n ON n.retroID = pb.batter_id;

-- --- Simplified WAR (SQL reimplementation of query/war.py) ------------------
--
-- IMPORTANT: this is this project's own simplified re-implementation of the
-- public FanGraphs WAR (fWAR) methodology, NOT a reproduction of it. Numbers
-- here will NOT match fangraphs.com or Baseball-Reference (bWAR). This block
-- is a line-for-line SQL port of query/war.py, query/park_factor.py, and
-- transform/war_constants.py, kept ONLY so free-form question mode (NL2SQL,
-- which can only ever generate SQL against this schema) can also answer WAR
-- questions -- query/templates.py's season_war_ranking() (template 14)
-- still calls the Python implementation directly, not these views. See
-- query/war.py's module docstring for the full list of disclosed
-- simplifications (baserunning is SB/CS value only, fielding is Statcast's
-- own OAA-to-runs conversion with 0 for 2015 (no OAA data that year), park
-- factor is a single unregressed season, replacement level splits its pool
-- 50/50 between batters and pitchers, etc.) -- summarized again briefly at
-- each view below, but not repeated in full.
--
-- Difference from the Python path: query/war.py raises a clear error for a
-- season outside 2015-2026 (no transcribed constants) -- these views instead
-- just INNER JOIN to v_war_constants, so an out-of-range season silently
-- returns zero rows rather than an error.

-- Year-by-year wOBA/FIP constants, transcribed verbatim from FanGraphs'
-- public Guts! page (https://www.fangraphs.com/guts.aspx?type=cn), fetched
-- 2026-09-12 -- see transform/war_constants.py (WOBA_FIP_CONSTANTS), which
-- this table must be kept in sync with.
CREATE OR REPLACE VIEW v_war_constants AS
SELECT * FROM (VALUES
    (2015, .313, 1.251, .687, .718, .881, 1.256, 1.594, 2.065, .200, -.392, .112, 9.421,  3.134),
    (2016, .318, 1.212, .691, .721, .878, 1.242, 1.569, 2.015, .200, -.410, .118, 9.778,  3.147),
    (2017, .321, 1.185, .693, .723, .877, 1.232, 1.552, 1.980, .200, -.423, .122, 10.048, 3.158),
    (2018, .315, 1.226, .690, .720, .880, 1.247, 1.578, 2.031, .200, -.407, .117, 9.714,  3.160),
    (2019, .320, 1.157, .690, .719, .870, 1.217, 1.529, 1.940, .200, -.435, .126, 10.296, 3.214),
    (2020, .320, 1.185, .699, .728, .883, 1.238, 1.558, 1.979, .200, -.435, .125, 10.282, 3.191),
    (2021, .314, 1.209, .692, .722, .879, 1.242, 1.568, 2.007, .200, -.419, .121, 9.973,  3.170),
    (2022, .310, 1.259, .689, .720, .884, 1.261, 1.601, 2.072, .200, -.397, .114, 9.524,  3.112),
    (2023, .318, 1.204, .696, .726, .883, 1.244, 1.569, 2.004, .200, -.422, .122, 10.028, 3.255),
    (2024, .310, 1.242, .689, .720, .882, 1.254, 1.590, 2.050, .200, -.405, .117, 9.683,  3.166),
    (2025, .313, 1.232, .691, .722, .882, 1.252, 1.584, 2.037, .200, -.410, .118, 9.774,  3.135),
    (2026, .316, 1.238, .698, .729, .890, 1.262, 1.596, 2.050, .200, -.412, .119, 9.826,  3.100)
) AS t(season, wOBA, wOBAScale, wBB, wHBP, w1B, w2B, w3B, wHR, runSB, runCS, R_PA, R_W, cFIP);

-- Positional adjustment, in runs per 600 PA (FanGraphs' published standard
-- values) -- see transform/war_constants.py's POSITION_ADJUSTMENT_PER_600PA.
CREATE OR REPLACE VIEW v_war_position_adjustments AS
SELECT * FROM (VALUES
    ('C', 12.5), ('1B', -12.5), ('2B', 2.5), ('3B', 2.5), ('SS', 7.5),
    ('LF', -7.5), ('CF', 2.5), ('RF', -7.5), ('DH', -17.5)
) AS t(position, runs_per_600pa);

-- Batting value: wOBA and Weighted Runs Above Average (wRAA, not yet park-
-- adjusted) per player-season, plus the simplified stolen-base-only
-- baserunning value (runSB*SB + runCS*CS). Stints within a season are
-- summed first. woba_pa is the standard wOBA denominator (AB + unintentional
-- BB + SF + HBP -- IBB and sac bunts excluded), matching query/war.py's
-- compute_wraa().
CREATE OR REPLACE VIEW v_war_batting_season AS
WITH agg AS (
    SELECT
        b.playerID, b.yearID AS season,
        STRING_AGG(DISTINCT b.teamID, '/') AS teams,
        SUM(b.AB) AS ab, SUM(b.H) AS h, SUM(b."2B") AS b2, SUM(b."3B") AS b3,
        SUM(b.HR) AS hr, SUM(b.BB) AS bb, SUM(b.IBB) AS ibb, SUM(b.HBP) AS hbp,
        SUM(b.SF) AS sf, SUM(b.SB) AS sb, SUM(b.CS) AS cs
    FROM lahman_batting b
    GROUP BY b.playerID, b.yearID
),
with_pa AS (
    SELECT
        a.*, c.wBB, c.wHBP, c.w1B, c.w2B, c.w3B, c.wHR,
        c.wOBA AS lg_wOBA, c.wOBAScale, c.runSB, c.runCS,
        (a.ab + (a.bb - COALESCE(a.ibb, 0)) + COALESCE(a.sf, 0) + COALESCE(a.hbp, 0)) AS woba_pa
    FROM agg a
    JOIN v_war_constants c ON c.season = a.season
)
SELECT
    playerID, season, teams, ab, h, b2 AS "2B", b3 AS "3B", hr, bb, ibb, hbp, sf, sb, cs,
    woba_pa,
    (wBB * (bb - COALESCE(ibb, 0)) + wHBP * COALESCE(hbp, 0)
        + w1B * (h - b2 - b3 - hr) + w2B * b2 + w3B * b3 + wHR * hr) / NULLIF(woba_pa, 0) AS woba,
    ((wBB * (bb - COALESCE(ibb, 0)) + wHBP * COALESCE(hbp, 0)
        + w1B * (h - b2 - b3 - hr) + w2B * b2 + w3B * b3 + wHR * hr) / NULLIF(woba_pa, 0)
        - lg_wOBA) / wOBAScale * woba_pa AS wraa,
    runSB * COALESCE(sb, 0) + runCS * COALESCE(cs, 0) AS baserunning_runs
FROM with_pa;

-- League-wide FIP inputs per season (every pitcher's innings, summed) --
-- the league-average baseline compute_pitching_value() compares each
-- pitcher's FIP against. See query/war.py's module docstring: this is
-- close to, but not bit-for-bit identical to, fWAR's own "runs allowed in
-- real games" league average.
CREATE OR REPLACE VIEW v_war_pitching_league_totals AS
SELECT yearID AS season, SUM(IPouts) AS ipouts, SUM(HR) AS hr, SUM(BB) AS bb,
       SUM(HBP) AS hbp, SUM(SO) AS so
FROM lahman_pitching
GROUP BY yearID;

-- Pitching value: FIP and FIP-based Runs Above Average (not yet park-
-- adjusted) per player-season, matching query/war.py's compute_pitching_value().
CREATE OR REPLACE VIEW v_war_pitching_season AS
WITH agg AS (
    SELECT
        pit.playerID, pit.yearID AS season,
        STRING_AGG(DISTINCT pit.teamID, '/') AS teams,
        SUM(pit.IPouts) AS ipouts, SUM(pit.HR) AS hr, SUM(pit.BB) AS bb,
        SUM(pit.HBP) AS hbp, SUM(pit.SO) AS so
    FROM lahman_pitching pit
    GROUP BY pit.playerID, pit.yearID
)
SELECT
    a.playerID, a.season, a.teams,
    a.ipouts / 3.0 AS ip,
    (13 * a.hr + 3 * (a.bb + COALESCE(a.hbp, 0)) - 2 * a.so) / NULLIF(a.ipouts / 3.0, 0) + c.cFIP AS fip,
    (13 * lg.hr + 3 * (lg.bb + COALESCE(lg.hbp, 0)) - 2 * lg.so) / NULLIF(lg.ipouts / 3.0, 0) + c.cFIP AS league_fip,
    (
        ((13 * lg.hr + 3 * (lg.bb + COALESCE(lg.hbp, 0)) - 2 * lg.so) / NULLIF(lg.ipouts / 3.0, 0) + c.cFIP)
        - ((13 * a.hr + 3 * (a.bb + COALESCE(a.hbp, 0)) - 2 * a.so) / NULLIF(a.ipouts / 3.0, 0) + c.cFIP)
    ) / 9 * (a.ipouts / 3.0) AS pitching_raa
FROM agg a
JOIN v_war_constants c ON c.season = a.season
JOIN v_war_pitching_league_totals lg ON lg.season = a.season;

-- Single-season, unregressed team park factor from Retrosheet Game Logs --
-- see query/park_factor.py's module docstring for the exact method (raw
-- runs-per-home-game / runs-per-road-game ratio, then regressed halfway to
-- a neutral 1.0 for use against a player's full-season value). teamID here
-- is Lahman's scheme: Retrosheet's "ANA" is mapped to Lahman's "LAA" (the
-- one franchise code mismatch in the 2015-2026 window -- verified against
-- both tables for every season in range).
CREATE OR REPLACE VIEW v_war_park_factor_team AS
WITH split AS (
    SELECT
        team, season,
        SUM(CASE WHEN home_away = 'H' THEN runs_for + runs_against ELSE 0 END) AS home_runs,
        SUM(CASE WHEN home_away = 'H' THEN 1 ELSE 0 END) AS home_games,
        SUM(CASE WHEN home_away = 'V' THEN runs_for + runs_against ELSE 0 END) AS away_runs,
        SUM(CASE WHEN home_away = 'V' THEN 1 ELSE 0 END) AS away_games
    FROM v_team_games
    GROUP BY team, season
)
SELECT
    CASE WHEN team = 'ANA' THEN 'LAA' ELSE team END AS teamID,
    season,
    (home_runs::DOUBLE / NULLIF(home_games, 0)) / NULLIF(away_runs::DOUBLE / NULLIF(away_games, 0), 0) AS raw_pf,
    ((home_runs::DOUBLE / NULLIF(home_games, 0)) / NULLIF(away_runs::DOUBLE / NULLIF(away_games, 0), 0) + 1) / 2 AS half_pf
FROM split;

-- Games-weighted half park factor across a batter's team stints in a season
-- (handles in-season trades) -- matches query/park_factor.py's
-- weighted_half_park_factor(), with a neutral 1.0 fallback for any
-- team-season with no usable park factor.
CREATE OR REPLACE VIEW v_war_batting_park_factor AS
SELECT
    b.playerID, b.yearID AS season,
    SUM(b.G * COALESCE(pf.half_pf, 1.0)) / NULLIF(SUM(b.G), 0) AS half_pf
FROM lahman_batting b
LEFT JOIN v_war_park_factor_team pf ON pf.teamID = b.teamID AND pf.season = b.yearID
GROUP BY b.playerID, b.yearID;

-- Same idea for pitchers, weighted by innings pitched (IPouts) per stint
-- instead of games.
CREATE OR REPLACE VIEW v_war_pitching_park_factor AS
SELECT
    pit.playerID, pit.yearID AS season,
    SUM(pit.IPouts * COALESCE(pf.half_pf, 1.0)) / NULLIF(SUM(pit.IPouts), 0) AS half_pf
FROM lahman_pitching pit
LEFT JOIN v_war_park_factor_team pf ON pf.teamID = pit.teamID AND pf.season = pit.yearID
GROUP BY pit.playerID, pit.yearID;

-- Statcast's own outs-above-average -> runs conversion
-- (fielding_runs_prevented), one row per Lahman playerID/season, for 2016+.
-- No rows exist for 2015 (statcast_oaa doesn't cover it), which is this
-- project's chosen treatment of that gap: fielding value is implicitly 0
-- wherever this is left-joined (see v_war_batting_value below), not an
-- estimate from Lahman's raw PO/A/E -- see query/war.py's
-- fielding_runs_for_season() docstring for why.
CREATE OR REPLACE VIEW v_war_fielding_season AS
SELECT DISTINCT
    p.playerID,
    oaa.season,
    oaa.fielding_runs_prevented
FROM statcast_oaa oaa
JOIN player_id_lookup pl ON pl.key_mlbam = oaa.player_id
JOIN v_people p ON p.retroID = pl.key_retro
WHERE oaa.pos = 'ALL';

-- Each player's most-played position (by games, summed across stints) among
-- the 9 position-adjustment categories, per season, from Lahman
-- Appearances -- matches query/war.py's primary_positions_for_season(). On
-- a tie, this picks the same position query/war.py's pandas idxmax() would
-- (first match in C, 1B, 2B, 3B, SS, LF, CF, RF, DH order). NULL (no
-- positional adjustment either way) for a player with zero games at any of
-- these -- e.g. a pitcher who never batted.
CREATE OR REPLACE VIEW v_war_primary_position AS
WITH g AS (
    SELECT
        playerID, yearID AS season,
        SUM(G_c) AS g_c, SUM(G_1b) AS g_1b, SUM(G_2b) AS g_2b, SUM(G_3b) AS g_3b,
        SUM(G_ss) AS g_ss, SUM(G_lf) AS g_lf, SUM(G_cf) AS g_cf, SUM(G_rf) AS g_rf,
        SUM(G_dh) AS g_dh
    FROM lahman_appearances
    GROUP BY playerID, yearID
),
with_max AS (
    SELECT *, GREATEST(g_c, g_1b, g_2b, g_3b, g_ss, g_lf, g_cf, g_rf, g_dh) AS max_g
    FROM g
)
SELECT
    playerID, season,
    CASE
        WHEN max_g <= 0 THEN NULL
        WHEN g_c  = max_g THEN 'C'
        WHEN g_1b = max_g THEN '1B'
        WHEN g_2b = max_g THEN '2B'
        WHEN g_3b = max_g THEN '3B'
        WHEN g_ss = max_g THEN 'SS'
        WHEN g_lf = max_g THEN 'LF'
        WHEN g_cf = max_g THEN 'CF'
        WHEN g_rf = max_g THEN 'RF'
        WHEN g_dh = max_g THEN 'DH'
    END AS primary_position
FROM with_max;

-- Replacement-level runs pools, per season -- matches query/war.py's
-- replacement_runs_pools(): converts the published .294 replacement win%
-- into a runs deficit (via that season's actual average games-per-team, so
-- a shortened season like 2020 scales down correctly, and R/W), then splits
-- it 50/50 between batters and pitchers (this project's own simplifying
-- choice, not a published split) and spreads each half over that season's
-- total batting PA / pitching IP.
CREATE OR REPLACE VIEW v_war_replacement_pools AS
WITH teams AS (
    SELECT yearID AS season, COUNT(DISTINCT teamID) AS n_teams, AVG(G) AS avg_games
    FROM lahman_teams
    GROUP BY yearID
),
batting_pa AS (
    SELECT yearID AS season,
           SUM(AB) + (SUM(BB) - SUM(IBB)) + SUM(HBP) + SUM(SF) AS total_pa
    FROM lahman_batting
    GROUP BY yearID
),
pitching_ip AS (
    SELECT yearID AS season, SUM(IPouts) / 3.0 AS total_ip
    FROM lahman_pitching
    GROUP BY yearID
)
SELECT
    c.season,
    ((0.5 - 0.294) * t.avg_games * c.R_W * t.n_teams * 0.5) / NULLIF(bp.total_pa, 0) AS runs_per_pa,
    ((0.5 - 0.294) * t.avg_games * c.R_W * t.n_teams * 0.5) / NULLIF(pi.total_ip, 0) AS runs_per_ip
FROM v_war_constants c
JOIN teams t ON t.season = c.season
JOIN batting_pa bp ON bp.season = c.season
JOIN pitching_ip pi ON pi.season = c.season;

-- Final batting-side value per player-season: park-adjusted wRAA +
-- baserunning + fielding + positional adjustment + batting-side
-- replacement runs. Matches query/war.py's season_war_leaderboard() batting
-- half exactly.
CREATE OR REPLACE VIEW v_war_batting_value AS
SELECT
    b.playerID,
    p.full_name,
    b.season,
    b.teams,
    b.woba_pa AS pa,
    b.woba,
    b.wraa,
    b.wraa / COALESCE(pf.half_pf, 1.0) AS wraa_park_adj,
    b.baserunning_runs,
    COALESCE(f.fielding_runs_prevented, 0) AS fielding_runs,
    pos.primary_position,
    CASE WHEN pos.primary_position IS NULL OR b.woba_pa <= 0 THEN 0.0
         ELSE pa_adj.runs_per_600pa * b.woba_pa / 600.0 END AS position_adj_runs,
    rp.runs_per_pa * b.woba_pa AS batting_replacement_runs,
    b.wraa / COALESCE(pf.half_pf, 1.0)
        + b.baserunning_runs
        + COALESCE(f.fielding_runs_prevented, 0)
        + CASE WHEN pos.primary_position IS NULL OR b.woba_pa <= 0 THEN 0.0
               ELSE pa_adj.runs_per_600pa * b.woba_pa / 600.0 END
        + rp.runs_per_pa * b.woba_pa AS batting_side_runs
FROM v_war_batting_season b
JOIN v_people p ON p.playerID = b.playerID
LEFT JOIN v_war_batting_park_factor pf ON pf.playerID = b.playerID AND pf.season = b.season
LEFT JOIN v_war_fielding_season f ON f.playerID = b.playerID AND f.season = b.season
LEFT JOIN v_war_primary_position pos ON pos.playerID = b.playerID AND pos.season = b.season
LEFT JOIN v_war_position_adjustments pa_adj ON pa_adj.position = pos.primary_position
JOIN v_war_replacement_pools rp ON rp.season = b.season;

-- Final pitching-side value per player-season: park-adjusted FIP-based RAA +
-- pitching-side replacement runs. Matches query/war.py's
-- season_war_leaderboard() pitching half exactly.
CREATE OR REPLACE VIEW v_war_pitching_value AS
SELECT
    pit.playerID,
    p.full_name,
    pit.season,
    pit.teams,
    pit.ip,
    pit.fip,
    pit.league_fip,
    pit.pitching_raa,
    pit.pitching_raa / COALESCE(pf.half_pf, 1.0) AS pitching_raa_park_adj,
    rp.runs_per_ip * pit.ip AS pitching_replacement_runs,
    pit.pitching_raa / COALESCE(pf.half_pf, 1.0) + rp.runs_per_ip * pit.ip AS pitching_side_runs
FROM v_war_pitching_season pit
JOIN v_people p ON p.playerID = pit.playerID
LEFT JOIN v_war_pitching_park_factor pf ON pf.playerID = pit.playerID AND pf.season = pit.season
JOIN v_war_replacement_pools rp ON rp.season = pit.season;

-- The final simplified-WAR leaderboard view: one row per player-season that
-- has either batting or pitching value (a two-way player like Shohei Ohtani
-- gets both sides added together, same as query/war.py's
-- season_war_leaderboard()). No PA/IP minimum is baked in here -- callers
-- (NL2SQL-generated SQL, or a future template) should add
-- "WHERE pa >= ... OR ip >= ..." themselves for a real leaderboard, the same
-- way v_team_games leaves win-percentage thresholds to its callers.
CREATE OR REPLACE VIEW v_war_season AS
SELECT
    COALESCE(bat.playerID, pit.playerID) AS playerID,
    COALESCE(bat.full_name, pit.full_name) AS full_name,
    COALESCE(bat.season, pit.season) AS season,
    COALESCE(bat.teams, pit.teams) AS teams,
    COALESCE(bat.pa, 0) AS pa,
    bat.wraa_park_adj,
    bat.baserunning_runs,
    bat.fielding_runs,
    bat.primary_position,
    bat.position_adj_runs,
    COALESCE(pit.ip, 0) AS ip,
    pit.fip,
    COALESCE(pit.pitching_raa_park_adj, 0) AS pitching_raa_park_adj,
    (COALESCE(bat.batting_side_runs, 0) + COALESCE(pit.pitching_side_runs, 0)) / c.R_W AS war
FROM v_war_batting_value bat
FULL JOIN v_war_pitching_value pit
    ON pit.playerID = bat.playerID AND pit.season = bat.season
JOIN v_war_constants c ON c.season = COALESCE(bat.season, pit.season);
