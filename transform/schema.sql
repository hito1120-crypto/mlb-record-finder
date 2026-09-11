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
