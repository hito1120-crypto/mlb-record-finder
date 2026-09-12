# About mlb-record-finder (Data Sources, Notes, and Limitations)

## 1. Data Sources

This tool uses the following free, publicly available data sources.

- **Lahman Baseball Database** (official SABR edition): Player season and career statistics (batting, pitching, fielding, awards). Covers 1871 to present.
- **Retrosheet Game Logs**: Team-level game results and scores. Covers 1871 to present.
- **Retrosheet Play-by-Play**: Pitch/plate-appearance-level in-game event data. Covers 1901 onward (parsed with a custom-built parser instead of cwevent).
- **Baseball Savant (Statcast)**: Exit velocity, distance, spin rate, OAA, bat speed, arm angle, and other detailed metrics, retrieved via the pybaseball library. Covers 2015–2026.
- **Google Gemini API**: Used for the free-form question mode (converting natural-language questions into SQL).

Given MLB.com's and Baseball Savant's terms of service, which prohibit automated collection and redistribution, this project is not offered as a hosted public service. It is published as open-source code for individuals to run in their own environment.

## 2. Notes on Data Coverage and Completeness

- MLB records from 1901 onward are covered almost completely, but 19th-century data (1871–1900) has limited completeness on the Retrosheet side, so claims of "first in history" for that period should be treated with caution.
- Play-by-play (in-game sequence) data is only partially available before the 1920s.
- Statcast-based metrics (exit velocity, expected stats, barrel rate, sprint speed, etc.) are only available from 2015 onward.
- Bat speed and swing length are only available from 2023 onward, and arm angle only from 2020 onward (the tracking technology itself did not exist before then).
- OAA (fielding metric) data does not exist for the 2015 season on Baseball Savant's side, so that year is treated as missing.

## 3. How This Differs From What a Reporter Like Sarah Langs (MLB.com) Can Produce

A dedicated MLB record researcher and writer like Sarah Langs draws on official Elias Sports Bureau records, internal team/league resources, and years of contextual reporting experience to make human judgment calls about whether a record is genuinely unprecedented and what it means historically.

This tool works differently in nature:

- It mechanically runs SQL queries against public statistical databases and returns matching records. It does not make editorial judgments about whether a finding is "interesting enough" to be newsworthy.
- It cannot access official Elias Sports Bureau records or non-public team/league resources — results are limited to what's available in the free public datasets (Lahman, Retrosheet, Statcast).
- If the underlying data (Lahman, Retrosheet) contains errors or gaps, this tool will faithfully reproduce them; it does not cross-check against primary sources the way a human reporter would.
- The free-form question mode relies on AI (Gemini API) to generate SQL from a question, which can occasionally misinterpret intent or produce an unintended aggregation. The generated SQL is always shown, so users should verify it themselves.
- Claims like "first in history" or "most all-time" are only as good as the data this tool can access. For periods with missing or incomplete data (19th century, pre-1920s play-by-play), the tool avoids definitive claims and automatically appends a caveat.

## 4. Notes on This Tool's Own Metrics (WAR and Park Factor)

The WAR (Wins Above Replacement) and park factor figures produced by this tool are an **independent, simplified calculation** based on publicly documented formulas, and they do NOT match the official numbers published by FanGraphs (fWAR) or Baseball-Reference (bWAR).

Specific simplifications include:

- Batting value is calculated as wOBA-based wRAA, but park and era adjustments are minimal (close to a single-season, unadjusted calculation).
- Baserunning value only reflects the run value of stolen base attempts (steals and caught stealing); it does not include a full Baserunning Runs component (extra-base advancement, etc.).
- Fielding value only uses Baseball Savant's OAA from 2016 onward; fielding value is treated as zero for 2015 and earlier.
- Pitching value is FIP-based, which can differ from official FIP by roughly 0.5.
- Park factor is a simplified, single-season calculation based on the ratio of home to road runs scored/allowed, and differs from official multi-year, fully adjusted ballpark factors (correlation with Lahman's official BPF is approximately 0.73).
- For two-way players (pitcher and batter), the standard DH positional adjustment is applied in full, which can undervalue such players relative to official WAR (for example, this tool's simplified calculation for Shohei Ohtani's 2024 season comes in about 0.9 lower than the official figure).

For these reasons, this tool's WAR and park factor figures should be treated as approximate reference values, not cited as official statistics.

## 5. Records and Questions This Tool Cannot Answer

- It cannot search for records based on official sources (e.g., Elias Sports Bureau) or non-public team data.
- It cannot make subjective judgments about a record's "interest" or "historical significance" — it only mechanically returns results matching the query.
- It cannot accurately answer questions about periods or metrics for which pitch/batted-ball-level data doesn't exist (19th-century play-by-play, pre-2015 Statcast metrics, etc.).
- The free-form question mode cannot handle overly vague questions or questions outside the scope of the database's schema (e.g., contract details, injury specifics — information this tool does not store).
- It does not support real-time or same-day game results (there is a data-retrieval lag).

## 6. Disclaimer

This tool was developed as a personal hobby project using free, publicly available data, and it has no affiliation with MLB or any related organization. It makes no guarantee of accuracy, and use is at the user's own risk.
