"""Season-by-season sabermetric constants used by query/war.py's simplified WAR.

IMPORTANT -- read this before trusting any number this module produces:
This project's WAR is an independent, simplified re-implementation inspired by
FanGraphs' public methodology. It is NOT FanGraphs WAR (fWAR) and will NOT
match fangraphs.com or Baseball-Reference (bWAR) exactly -- see
query/war.py's module docstring for the list of simplifications.

WOBA_FIP_CONSTANTS below is a literal transcription of FanGraphs' public
"Guts!" constants table (https://www.fangraphs.com/guts.aspx?type=cn),
fetched 2026-09-12. That table only cross-checks cleanly against this
project's other data sources back to the start of the Statcast era (2015),
which is also the range covered by this project's Statcast ingest -- see
README.md's coverage notice -- so WAR support is intentionally limited to
2015-2026 for now. Extending it to Lahman's full 1871-2025 range would need a
separate, carefully-verified transcription of the older rows and is left for
a later phase.

Column meanings (all from FanGraphs' Guts! page):
  wOBA       -- that season's league-average wOBA
  wOBAScale  -- divisor that converts a wOBA-points difference into runs
  wBB/wHBP/w1B/w2B/w3B/wHR -- wOBA linear weight per event
  runSB/runCS -- run value of a stolen base / caught stealing (linear weights)
  R_PA       -- league runs per plate appearance (run environment)
  R_W        -- runs per marginal win, for that season's scoring environment
  cFIP       -- FIP constant, calibrated so that league-average FIP ~= league ERA
"""

from __future__ import annotations

# Season -> constants dict. Source: FanGraphs Guts! (type=cn), 2015-2026 rows.
WOBA_FIP_CONSTANTS: dict[int, dict[str, float]] = {
    2015: dict(wOBA=.313, wOBAScale=1.251, wBB=.687, wHBP=.718, w1B=.881, w2B=1.256, w3B=1.594, wHR=2.065, runSB=.200, runCS=-.392, R_PA=.112, R_W=9.421, cFIP=3.134),
    2016: dict(wOBA=.318, wOBAScale=1.212, wBB=.691, wHBP=.721, w1B=.878, w2B=1.242, w3B=1.569, wHR=2.015, runSB=.200, runCS=-.410, R_PA=.118, R_W=9.778, cFIP=3.147),
    2017: dict(wOBA=.321, wOBAScale=1.185, wBB=.693, wHBP=.723, w1B=.877, w2B=1.232, w3B=1.552, wHR=1.980, runSB=.200, runCS=-.423, R_PA=.122, R_W=10.048, cFIP=3.158),
    2018: dict(wOBA=.315, wOBAScale=1.226, wBB=.690, wHBP=.720, w1B=.880, w2B=1.247, w3B=1.578, wHR=2.031, runSB=.200, runCS=-.407, R_PA=.117, R_W=9.714, cFIP=3.160),
    2019: dict(wOBA=.320, wOBAScale=1.157, wBB=.690, wHBP=.719, w1B=.870, w2B=1.217, w3B=1.529, wHR=1.940, runSB=.200, runCS=-.435, R_PA=.126, R_W=10.296, cFIP=3.214),
    2020: dict(wOBA=.320, wOBAScale=1.185, wBB=.699, wHBP=.728, w1B=.883, w2B=1.238, w3B=1.558, wHR=1.979, runSB=.200, runCS=-.435, R_PA=.125, R_W=10.282, cFIP=3.191),
    2021: dict(wOBA=.314, wOBAScale=1.209, wBB=.692, wHBP=.722, w1B=.879, w2B=1.242, w3B=1.568, wHR=2.007, runSB=.200, runCS=-.419, R_PA=.121, R_W=9.973, cFIP=3.170),
    2022: dict(wOBA=.310, wOBAScale=1.259, wBB=.689, wHBP=.720, w1B=.884, w2B=1.261, w3B=1.601, wHR=2.072, runSB=.200, runCS=-.397, R_PA=.114, R_W=9.524, cFIP=3.112),
    2023: dict(wOBA=.318, wOBAScale=1.204, wBB=.696, wHBP=.726, w1B=.883, w2B=1.244, w3B=1.569, wHR=2.004, runSB=.200, runCS=-.422, R_PA=.122, R_W=10.028, cFIP=3.255),
    2024: dict(wOBA=.310, wOBAScale=1.242, wBB=.689, wHBP=.720, w1B=.882, w2B=1.254, w3B=1.590, wHR=2.050, runSB=.200, runCS=-.405, R_PA=.117, R_W=9.683, cFIP=3.166),
    2025: dict(wOBA=.313, wOBAScale=1.232, wBB=.691, wHBP=.722, w1B=.882, w2B=1.252, w3B=1.584, wHR=2.037, runSB=.200, runCS=-.410, R_PA=.118, R_W=9.774, cFIP=3.135),
    2026: dict(wOBA=.316, wOBAScale=1.238, wBB=.698, wHBP=.729, w1B=.890, w2B=1.262, w3B=1.596, wHR=2.050, runSB=.200, runCS=-.412, R_PA=.119, R_W=9.826, cFIP=3.100),
}

MIN_SUPPORTED_SEASON = min(WOBA_FIP_CONSTANTS)
MAX_SUPPORTED_SEASON = max(WOBA_FIP_CONSTANTS)


def constants_for_season(year: int) -> dict[str, float]:
    if year not in WOBA_FIP_CONSTANTS:
        raise ValueError(
            f"WAR is only supported for {MIN_SUPPORTED_SEASON}-{MAX_SUPPORTED_SEASON} "
            f"(no transcribed wOBA/FIP constants for {year})"
        )
    return WOBA_FIP_CONSTANTS[year]


# --- Replacement level / positional adjustment (published standard values) --
#
# A replacement-level team is conventionally defined (Tom Tango / FanGraphs)
# as a team that would win about 29.4% of its games if fielded entirely with
# freely-available (replacement-level) players -- see query/war.py's
# replacement_runs_pools() for how this win-rate is converted into a runs
# pool and split between batters and pitchers.
REPLACEMENT_LEVEL_WIN_PCT = 0.294

# Positional adjustment, in runs per 600 PA (FanGraphs' published standard
# values -- reflects the relative defensive difficulty/scarcity of each
# position, independent of how well the player themself fields it, which is
# what the separate OAA-based fielding value above already covers).
POSITION_ADJUSTMENT_PER_600PA: dict[str, float] = {
    "C": 12.5,
    "1B": -12.5,
    "2B": 2.5,
    "3B": 2.5,
    "SS": 7.5,
    "LF": -7.5,
    "CF": 2.5,
    "RF": -7.5,
    "DH": -17.5,
}
