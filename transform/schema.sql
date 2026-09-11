-- Convenience views built on top of the raw tables that ingest/*.py load.
-- Safe to re-run: every statement is CREATE OR REPLACE.
-- Applied automatically by cli.py on startup, so it always reflects
-- whatever raw tables are currently present in the DuckDB file.

-- Player full names (Lahman People.csv has separate first/last name columns).
CREATE OR REPLACE VIEW v_people AS
SELECT
    playerID,
    nameFirst,
    nameLast,
    (nameFirst || ' ' || nameLast) AS full_name,
    birthYear,
    debut,
    finalGame
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
