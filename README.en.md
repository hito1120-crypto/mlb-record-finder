# mlb-record-finder

A CLI tool for digging up obscure MLB records and stats -- the kind of deep-cut
finds a reporter like Sarah Langs (MLB.com) surfaces. Built entirely on free,
publicly available data.

[日本語 README](README.md)

## What it does

Run `python cli.py`, pick one of 10 record templates by number, enter a few
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

## Data sources

All free and publicly available.

| Source | Contents | Coverage |
|---|---|---|
| [Lahman Baseball Database (official SABR edition)](https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd) | Season/career batting, pitching, fielding, and awards | 1871 through the 2025 season |
| [Retrosheet Game Logs](https://www.retrosheet.org/gamelogs/index.html) | Team-level game-by-game results and scores | 1871-present |
| [pybaseball](https://github.com/jldbc/pybaseball)'s Statcast (Baseball Savant) | Exit velocity, distance, spin rate, etc. | Most recent 2 seasons only |
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
                                 (pitch data / Sprint Speed / player ID lookup)
    retrosheet_playbyplay.py   fetch/load Retrosheet play-by-play event files
    _playbyplay_parser.py      from-scratch parser for the event-file grammar
  transform/
    schema.sql          views built on top of the ingested tables
                         (incl. run expectancy / win probability / leverage)
  query/
    templates.py         the 10 record-search templates (SQL/functions)
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
- Statcast ingest is limited to the most recent 2 seasons (fetching full
  history back to 2015 is out of scope).
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
