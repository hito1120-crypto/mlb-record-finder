# mlb-record-finder

A CLI tool for digging up obscure MLB records and stats -- the kind of deep-cut
finds a reporter like Sarah Langs (MLB.com) surfaces. Built entirely on free,
publicly available data.

[日本語 README](README.md)

For data sources, notes on this tool's own metrics (WAR, etc.), and a disclaimer, see [mlb-record-finder_disclosure_notes_en.md](mlb-record-finder_disclosure_notes_en.md).

## What it does

Run `python cli.py`, pick one of 14 record templates by number, enter a few
parameters (thresholds, a season year, etc.), and it runs SQL against a local
DuckDB file and prints the results as a table.

1. **Search home runs by exit velocity + distance** (Statcast) -- e.g. home runs with exit velocity >= 115 mph AND distance >= 470 ft
2. **All-time HR ranking for a given franchise** (Lahman) -- ranks players by home runs hit for a franchise across its entire history, including relocations/renames
3. **Players who hit two season thresholds** (Lahman) -- e.g. players with >= 30 HR AND >= 20 SB in the same season
4. **Best winning percentage over any N-game window** (Retrosheet) -- searches all teams and all seasons for the best win% over any N consecutive games
5. **Players who hit three season thresholds** (Lahman) -- e.g. a triples + home runs + stolen bases compound achievement
6. **Barrel% / Hard-Hit% ranking** (Statcast) -- for players with at least a minimum number of batted-ball events, ranks Barrel% and Hard-Hit% (exit velocity >= 95 mph) for a given season
7. **Expected stats (xwOBA/xBA/xSLG) ranking vs. actual results** (Statcast) -- ranks a season's expected-stats leaders alongside their actual wOBA/BA/SLG, with an over/under-performing flag
8. **Sprint Speed ranking** (Statcast) -- ranks a season's fastest players by Sprint Speed
9. **Games with a leadoff HR AND a walk-off HR** (Retrosheet Play-by-Play) -- finds games where the very first plate appearance of the game was a home run and the game also ended on a walk-off home run
10. **Highest-leverage plate appearances** (Retrosheet Play-by-Play) -- ranks individual plate appearances by how much they swung the home team's win probability, optionally filtered by date range and/or batter name
11. **Bat Speed / Swing Length ranking** (Statcast) -- ranks players by average/max bat speed and average swing length for a given season, filterable by a minimum number of tracked swings
12. **Outs Above Average (OAA) fielding ranking** (Statcast) -- ranks players by OAA (outs saved above an average fielder) and fielding runs prevented for a given season and fielding position (catchers excluded)
13. **Arm Angle ranking** (Statcast) -- ranks pitchers by average arm angle (release-point angle) for a given season, filterable by a minimum number of tracked pitches
14. **Season WAR ranking** (Lahman/Statcast/Retrosheet, 2015-2026 seasons only) -- ⚠️ **this tool's own simplified WAR estimate. It does NOT match official FanGraphs (fWAR) or Baseball-Reference (bWAR) numbers** (details below)

## Data sources

All free and publicly available.

| Source | Contents | Coverage |
|---|---|---|
| [Lahman Baseball Database (official SABR edition)](https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd) | Season/career batting, pitching, fielding, and awards | 1871 through the 2025 season |
| [Retrosheet Game Logs](https://www.retrosheet.org/gamelogs/index.html) | Team-level game-by-game results and scores | 1871-present |
| [pybaseball](https://github.com/jldbc/pybaseball)'s Statcast (Baseball Savant) | Exit velocity, distance, spin rate, etc. | 2015 season onward (start of Statcast's full pitch-tracking era) |
| [Retrosheet Play-by-Play (event files)](https://www.retrosheet.org/events/index.html) | Per-plate-appearance base/out state, score, and play outcome | Most recent 5 seasons fetched by default; Retrosheet's own play-by-play coverage is only partial before the 1920s (see the caveat below) |

The Lahman Baseball Database is now officially maintained by SABR (Society
for American Baseball Research), after its former distributor,
`chadwickbureau/baseballdatabank`, was taken down from GitHub. This tool
pulls from SABR's CSV release on Box.com (the `lahman_1871-2025_csv`
folder, released January 2026, covering through the 2025 season). That Box
folder page has no explicit license notice (only the Negro Leagues data
elsewhere on SABR's site is explicitly credited to Seamheads.com), so this
tool assumes personal/research use.

Box.com share folders are a JS-rendered SPA with no public listing API, so
`ingest/lahman.py` parses the JSON (`Box.postStreamData`) embedded in the
share page to get the file list, then downloads each CSV via Box's direct
shared-file endpoint. If Box changes that page's markup and this breaks,
the script automatically falls back to the previous mirror
(`xorq-labs/baseballdatabank`, data through the 2021 season only).

### ⚠️ Important data-completeness caveat

**This tool can cover nearly the entire scope of MLB records from 1901 onward, but 19th-century (1871-1900) data has limited completeness on the Retrosheet side, so "first-ever" claims from that period should be treated with caution.**

### Implementation notes for templates 6-8 (the Statcast add-ons)

- **Barrel classification**: rather than reimplementing Statcast's own exit-velocity/launch-angle sweet-spot rule, this uses the MLBAM-assigned `launch_speed_angle` column from the raw `statcast()` data as-is. Verified by aggregating the ingested data directly: rows with `launch_speed_angle = 6` have exit velocity 97.5-122.9 mph (avg ~105) and launch angle 6-47 deg (avg ~26) -- exactly Baseball Savant's documented "Barrel" bucket (the full scale is 1=Weak, 2=Topped, 3=Under, 4=Flare/Burner, 5=Solid Contact, 6=Barrel). Hard-Hit% is the plain exit-velocity->=95 mph rate, per spec.
- **A data gotcha worth knowing**: in the raw Statcast pitch data (`statcast_pitches`), the `player_name` column is the **pitcher** throwing the pitch, not the batter (confirmed: for a fixed `player_name`, `pitcher` stays constant across rows while `batter` varies). Templates 6 and 7 are batter-side leaderboards, so they join the numeric `batter` (MLBAM ID) against the `player_id_lookup` table (see below) to get the batter's actual name.
- **Expected stats (xBA/xSLG/xwOBA)**: computed from the existing raw columns (`estimated_ba_using_speedangle` / `estimated_slg_using_speedangle` / `estimated_woba_using_speedangle`). This preserves Baseball Savant's own design difference between the two: xBA/xSLG only cover batted-ball contact events (strikeouts/walks/HBP excluded), while xwOBA is a full plate-appearance metric that already assigns fixed values to strikeouts/walks/HBP. A player is flagged "overperforming"/"underperforming" when actual wOBA differs from xwOBA by more than +-0.015 (adjustable via `luck_threshold` in `query/templates.py`).
- **Sprint Speed**: not present anywhere in the pitch-level data, so this uses pybaseball's dedicated leaderboard function, `statcast_sprint_speed(year, min_opp)`. Since it's a season-level aggregate (not per-pitch), `ingest/statcast.py` requests it once per season (already-fetched seasons are never re-requested) and caches it under `data/raw/statcast/sprint_speed/`.
- **Batter ID <-> name lookup**: to resolve the `player_name`-is-the-pitcher issue above, `ingest/statcast.py` fetches pybaseball's `chadwick_register()` (a full MLBAM-ID-to-name crosswalk for essentially every player) exactly once, caches it at `data/raw/statcast/player_id_lookup.parquet`, and loads it into DuckDB as `player_id_lookup`. It isn't season-specific, so it's never re-fetched per season.

### Implementation notes for templates 9-10 (the Retrosheet Play-by-Play add-ons)

- **Play-by-play parsing**: Retrosheet distributes play-by-play as raw event files in a compact grammar, not a ready-to-query table. `ingest/_playbyplay_parser.py` is a from-scratch, pure-Python parser (no C toolchain / Chadwick `cwevent` dependency) that reconstructs, for every plate appearance and baserunning event, the base/out state before and after and how many runs scored. Its final-score reconstruction has been cross-checked against Retrosheet's own Game Logs for every ingested game; as of this writing it matches exactly for 2,417 of 2,430 games (99.5%) in the ingested 2025 season, with the small remainder traced to isolated inconsistencies rather than a single systematic bug.
- **Run Expectancy Matrix** (`v_run_expectancy` in `transform/schema.sql`): the average runs scored for the rest of a half-inning, for each of the 8 base states x 3 out counts (24 combinations), computed directly from the ingested play-by-play. Values line up with published MLB run-expectancy tables (e.g. bases empty/0 outs ~0.50, bases loaded/0 outs ~2.4, bases empty/2 outs ~0.10), which is the main sanity check for the whole play-by-play pipeline.
- **Win probability** (`v_win_probability`): there isn't enough ingested history yet for a reliable empirical win-probability table (that would need binning by inning x score-diff x outs x base-state, with only one season of data), so this is a modeled approximation instead -- a projected final score differential (current score + run-expectancy-based projections for the rest of the game) collapsed to a probability via a logistic curve. It has no home-field-advantage term and treats extra innings as a continuation of the 9th, so treat it as directional, not a precise sportsbook-grade number. The one place it's exact rather than modeled: the final play of a game always resolves to win probability 1.0/0.0 for the actual winner, so a walk-off's full leverage swing always shows up correctly.
- **Leverage index** (`v_play_leverage`, used by template 10): each play's leverage is the actual before/after win-probability swing it produced, not the textbook definition's average over every hypothetical outcome of that base-out-score state.

### Implementation notes for templates 11-13 (the Statcast bat-tracking / fielding / arm-angle add-ons)

- **Bat Speed / Swing Length**: `bat_speed` / `swing_length` come straight off the existing `statcast_pitches` columns -- no new ingest step was needed, since Baseball Savant added these to the same pitch-level CSV export, so simply re-running `ingest/statcast.py` picked them up. Both columns are non-null only on pitches where the batter actually swung (~46% of pitches in a sampled day), which is why `min_swings` counts swings, not pitches.
- **Outs Above Average (OAA)**: not present anywhere in the pitch-level data, so this uses Baseball Savant's own precomputed season leaderboard (`pybaseball.statcast_outs_above_average(year, pos, min_att, view="Fielder")`). **The same player's OAA changes depending on which position it's queried for** (e.g. a utility infielder shows a different number under `2B` than under the `IF` aggregate), so each (season, position) combination is fetched and cached separately (`data/raw/statcast/oaa/`), with the queried position stored as its own `pos` column on the `statcast_oaa` table. The positions fetched are the 7 individual fielding positions (1B/2B/3B/SS/LF/CF/RF) plus Savant's own IF/OF/ALL aggregate buckets (ALL excludes catchers). Catchers are excluded entirely because the leaderboard itself doesn't cover them. The leaderboard CSV also has no attempts/opportunities column, so unlike Sprint Speed's `min_opp` the threshold can't be relaxed at query time -- it's fixed at Savant's own "qualified" cutoff (`min_att="q"`) when fetched. The leaderboard's directional (front/back/lateral) and batter-handedness breakdowns are ingested into `statcast_oaa` but deliberately left out of template 12's ranking display, which shows only the headline metrics (OAA, fielding runs prevented, catch success rates).
- **Arm Angle**: like `bat_speed`, `arm_angle` arrived as an existing `statcast_pitches` column with no new ingest step needed. Unlike bat speed, though, it's a pitcher release-point measurement rather than something tied to whether the batter swung, so it's non-null on roughly 94% of all pitches (confirmed against the ingested data) -- which is why `min_pitches` counts pitches, not swings. It also needs no `player_id_lookup` join, unlike templates 6-7's batter-side leaderboards: `statcast_pitches.player_name` is already the pitcher's name (see the templates 6-8 note above), so it's used directly. A given pitcher's arm angle isn't perfectly constant across a season (a sampled check found a standard deviation of roughly 3-5 degrees per pitcher), so this ranking shows the season average.

### Implementation notes for template 14 (Season WAR ranking)

#### ⚠️ This tool's WAR is its own simplified estimate -- it does not match official fWAR/bWAR

**The WAR this template computes is this tool's own simplified implementation, built by reference to publicly documented wOBA/FIP-style sabermetric methodology. It is not a reproduction of FanGraphs' (fWAR) or Baseball-Reference's (bWAR) actual algorithms, and matching either site's published numbers is explicitly not a goal.** The CLI prints this same caveat every time template 14 runs.

- **Supported seasons**: 2015-2026 only. The year-by-year wOBA/FIP constants are transcribed, for 2015-2026 only, from FanGraphs' public Guts! page (`https://www.fangraphs.com/guts.aspx?type=cn`) into a literal table in `transform/war_constants.py` -- unlike Lahman, this can't currently reach back to 1871 (accurately transcribing older-era constants would need separate, careful verification that's out of scope for now). Requesting an unsupported year shows an error message.
- **Batting value (wRAA)**: computed from the standard wOBA formula (denominator = AB + unintentional BB + SF + HBP), compared against that season's league-average wOBA to get Weighted Runs Above Average.
- **Baserunning value**: stolen-base value only (SB/CS linear weights). This does not include a full Baserunning Runs (BsR)-style calculation (taking extra bases, avoiding double plays, etc.).
- **Fielding value**: for 2016 onward, this uses Statcast's own precomputed leaderboard value (`statcast_oaa.fielding_runs_prevented`, i.e. OAA already converted to runs by Baseball Savant) directly. **2015 has no OAA data, so fielding value is treated as 0 for that season** -- a Lahman-raw-fielding-stats (putouts/assists/errors) approximation was considered but rejected, since none of that data captures the batted-ball-location catch-probability model that OAA is actually built on, so it wouldn't be a reliable substitute.
- **Pitching value (FIP-based)**: FIP is computed as `(13xHR + 3x(BB+HBP) - 2xK) / IP + cFIP`, then compared against a league-average FIP computed the same way from that season's league totals, scaled by innings pitched into Runs Above Average.
- **Park factor**: a new, from-scratch calculation (`query/park_factor.py`) built from Retrosheet Game Logs: a single-season "(runs scored + allowed per home game) / (runs scored + allowed per road game)" ratio. Being a single, unregressed season, it's noisy, so -- following standard practice -- it's regressed halfway toward a neutral 1.0 (`(raw + 1) / 2`) before being applied to a player's full-season value. Cross-checked against Lahman's own (multi-year, official) BPF for the 2024 season, the correlation is about 0.73 -- directionally consistent but not a match. Players traded mid-season get a games-weighted average across their teams.
- **Replacement level / positional adjustment**: uses the published standard ".294 replacement win%" definition, converted into a league-wide replacement-runs pool using that season's actual average games-per-team (correctly scaling down for a shortened season like 2020's 60 games) and R/W (runs per marginal win). This project's own choice -- not a published number -- is to then split that pool 50/50 between batters and pitchers (FanGraphs instead uses an empirically-derived ~57/43 split with separate starter/reliever replacement levels, which this tool does not reproduce). Positional adjustment uses FanGraphs' published standard values (runs per 600 PA), applied using each player's most-played position for the season from `lahman_appearances`.
- **Two-way players**: a two-way player like Shohei Ohtani simply has their batting-side value and pitching-side value added together. However, the batting-side positional adjustment penalizes their DH at-bats exactly as it would a full-time DH, without crediting the fact that they're also generating separate value as a pitcher -- so two-way players tend to come out with a lower WAR here than official sources report (confirmed directionally against Ohtani's real seasons during validation).

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Requires Python 3.11+.

## Usage

### 1. Ingest data (first run only)

```bash
python ingest/lahman.py
python ingest/retrosheet_gamelogs.py
python ingest/statcast.py
python ingest/retrosheet_playbyplay.py
```

Each script loads data into `data/processed/mlb.duckdb`. Downloaded files are
cached under `data/raw/`, so **nothing already fetched is re-downloaded**
(Statcast pitch data only fetches the delta since the last run; Sprint Speed
is cached per season; the player ID lookup is fetched once, period). Pass
`--force` to refetch anyway.

The first time you run `python cli.py` with no data ingested yet, it will
offer to run the ingest scripts for you automatically.

### 2. Run the CLI

```bash
python cli.py            # Japanese UI (default)
python cli.py --lang en  # English UI
```

## Directory layout

```
mlb-record-finder/
  data/raw/          cached downloads (gitignored)
  data/processed/     mlb.duckdb (single-file DB)
  ingest/
    lahman.py                  fetch/load the Lahman Baseball Database
    retrosheet_gamelogs.py     fetch/load Retrosheet game logs
    statcast.py                 incrementally fetch/load Statcast via pybaseball
                                 (pitch data / Sprint Speed / OAA / player ID lookup)
    retrosheet_playbyplay.py   fetch/load Retrosheet play-by-play event files
    _playbyplay_parser.py      from-scratch parser for the event-file grammar
  transform/
    schema.sql          views built on top of the ingested tables
                         (incl. run expectancy / win probability / leverage)
    war_constants.py     year-by-year wOBA/FIP constants for WAR (2015-2026,
                         this tool's own simplified estimate)
  query/
    templates.py         the 14 record-search templates (SQL/functions)
    war.py                simplified WAR calculation logic (template 14)
    park_factor.py        single-season simplified park factor from Retrosheet Game Logs
  i18n/
    ja.py / en.py         UI string dictionaries
  cli.py                the interactive CLI
  requirements.txt
```

## Tech stack

Python 3.11+ / pybaseball / pandas / duckdb / requests / tabulate

## About i18n

All UI text -- menu items, prompts, error messages, result table column
labels -- is routed through the `i18n/ja.py` / `i18n/en.py` dictionaries.
The underlying data itself (player names, team names, etc.) is left in its
original (English) form rather than being translated.

## Known limitations

- Lahman data goes through the 2025 season (official SABR edition, see above).
- Statcast ingest covers the 2015 season onward (backfilled season by
  season; see `data/raw/statcast/manifest.json` for what's fetched so far).
- The winning-percentage window search (template 4) does not consider
  windows that span across a season boundary.
- The SABR edition of the Lahman data includes some Negro Leagues teams and
  players (e.g. the New York Black Yankees). If a franchise name search
  returns multiple matches, pick the one you want by number.
- Templates 6 and 7 are batter-side leaderboards only; a pitcher's own
  batting stats (rare, e.g. in NL parks) are attributed to them correctly as
  a batter, but pitching performance is not aggregated by these templates.
- Retrosheet's play-by-play (event-file) data, which templates 9 and 10 are
  built on, is only fetched for the most recent 5 seasons by default, and
  Retrosheet's own play-by-play record-keeping is only partial before the
  1920s -- this is a stricter cutoff than the Game Logs used by template 4,
  which cover back to 1871.
- Templates 9-10's win probability and leverage index are approximations,
  not an empirical model -- see the implementation notes above.
- Template 12 (OAA) excludes catchers, since Baseball Savant's own
  leaderboard doesn't cover them. Its attempts threshold is also fixed at
  Savant's "qualified" cutoff and, unlike template 8's Sprint Speed, cannot
  be changed at query time.
- Template 13 (Arm Angle) ranks season averages only; it doesn't break out
  per-pitch-type differences (a pitcher's arm angle can shift a few degrees
  between, say, a slider and a four-seam fastball).
- **Template 14 (Season WAR ranking) is this tool's own simplified estimate
  and does NOT match official FanGraphs (fWAR) or Baseball-Reference (bWAR)
  numbers.** It only supports the 2015-2026 seasons. See the implementation
  notes above for the full list of simplifications.
