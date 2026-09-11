# mlb-record-finder

A CLI tool for digging up obscure MLB records and stats -- the kind of deep-cut
finds a reporter like Sarah Langs (MLB.com) surfaces. Built entirely on free,
publicly available data.

[日本語 README](README.md)

## What it does (Phase 1 / MVP)

Run `python cli.py`, pick one of 5 record templates by number, enter a few
parameters (thresholds, a season year, etc.), and it runs SQL against a local
DuckDB file and prints the results as a table.

1. **Search home runs by exit velocity + distance** (Statcast) -- e.g. home runs with exit velocity >= 115 mph AND distance >= 470 ft
2. **All-time HR ranking for a given franchise** (Lahman) -- ranks players by home runs hit for a franchise across its entire history, including relocations/renames
3. **Players who hit two season thresholds** (Lahman) -- e.g. players with >= 30 HR AND >= 20 SB in the same season
4. **Best winning percentage over any N-game window** (Retrosheet) -- searches all teams and all seasons for the best win% over any N consecutive games
5. **Players who hit three season thresholds** (Lahman) -- e.g. a triples + home runs + stolen bases compound achievement

## Data sources

All free and publicly available.

| Source | Contents | Coverage |
|---|---|---|
| [Lahman Baseball Database (official SABR edition)](https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd) | Season/career batting, pitching, fielding, and awards | 1871 through the 2025 season |
| [Retrosheet Game Logs](https://www.retrosheet.org/gamelogs/index.html) | Team-level game-by-game results and scores | 1871-present |
| [pybaseball](https://github.com/jldbc/pybaseball)'s Statcast (Baseball Savant) | Exit velocity, distance, spin rate, etc. | Most recent 2 seasons only |

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
```

Each script loads data into `data/processed/mlb.duckdb`. Downloaded files are
cached under `data/raw/`, so **nothing already fetched is re-downloaded**
(Statcast only fetches the delta since the last run). Pass `--force` to
refetch anyway.

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
    lahman.py               fetch/load the Lahman Baseball Database
    retrosheet_gamelogs.py  fetch/load Retrosheet game logs
    statcast.py              incrementally fetch/load Statcast via pybaseball
  transform/
    schema.sql          views built on top of the ingested tables
  query/
    templates.py         the 5 record-search templates (SQL/functions)
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

## Known limitations (Phase 1)

- Lahman data goes through the 2025 season (official SABR edition, see above).
- Statcast ingest is limited to the most recent 2 seasons (fetching full
  history back to 2015 is out of scope for Phase 1).
- The winning-percentage window search (template 4) does not consider
  windows that span across a season boundary.
- The SABR edition of the Lahman data includes some Negro Leagues teams and
  players (e.g. the New York Black Yankees). If a franchise name search
  returns multiple matches, pick the one you want by number.
