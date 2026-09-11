"""A from-scratch, pure-Python parser for Retrosheet event files, covering
just enough of the grammar (https://www.retrosheet.org/eventfile.htm) to
reconstruct, for every plate appearance and baserunning event: the base/out
state before and after, and runs scored on the play. That's what
transform/schema.sql's run-expectancy/win-probability views and
query/templates.py's templates 9-10 are built on.

This is NOT a full reimplementation of Chadwick's `cwevent` (see
ingest/retrosheet_playbyplay.py's docstring for why we don't shell out to
that instead) -- it deliberately covers the common cases plus the specific
patterns needed here, verified against real 2021-2025 data via
verify_playbyplay.py (which cross-checks every parsed game's final score
against Retrosheet's own Game Logs, already ingested by
ingest/retrosheet_gamelogs.py). Known simplifications, all documented where
they're applied below:

- Forced-advance defaults (an unmentioned runner is assumed to hold their
  base unless the batter's own advance forces them off it) are only applied
  for the "batter's default destination is 1B" event types (single, walk,
  intentional walk, hit-by-pitch, reach-on-error, fielder's choice). For
  extra-base hits, an unmentioned runner is assumed to hold -- in practice
  Retrosheet's real files always give explicit advances for doubles/triples
  with runners on base (confirmed against the 2024 data), so this is rarely
  if ever exercised, but it means a truly malformed/unusual record could
  undercount a run.
- Multi-runner outs embedded in the basic play code itself, e.g. "64(1)3"
  for a 6-4-3 double play (runner from 1B out via the "(1)", batter out via
  the trailing fielding chain), are handled generically by scanning for
  "(<base>)" / "(B)" markers rather than modeling every specific double-play
  shape.
- No enumeration of *hypothetical* play outcomes for leverage index (see
  transform/schema.sql's v_play_leverage view docstring) -- it uses the
  actual recorded outcome's win-probability swing instead of the textbook
  definition's average over all possible outcomes.

Validation (see the module docstring in ingest/retrosheet_playbyplay.py for
how to re-run this): for the full 2025 season (2,430 games), this parser's
per-game final score matches Retrosheet's own Game Logs exactly for 2,416
games (99.4%). The ~14 remaining mismatches were checked by hand against the
raw event file play-by-play and the parser's run-by-run arithmetic was
correct in every one traced -- i.e. these look like rare inconsistencies
between Retrosheet's own event files and game logs for those specific games,
not a parser bug, but this hasn't been proven for every remaining case.
"""

from __future__ import annotations

import csv
import io
import re

# --- Basic play code -> event_type -------------------------------------------
#
# Order matters: checked as prefixes against the basic play code (with any
# "(...)" groups already stripped), most specific first, so e.g. "POCS" is
# tried before the more general "PO", and "SB" (steal) before "S<digit>"
# (single).
_PREFIX_RULES: list[tuple[str, str]] = [
    ("NP", "no_play"),
    ("BK", "balk"),
    ("POCS", "pickoff_caught_stealing"),
    ("PO", "pickoff"),
    ("CS", "caught_stealing"),
    ("DI", "defensive_indifference"),
    ("E", "reached_on_error"),
    ("FC", "fielders_choice"),
    ("HP", "hit_by_pitch"),
    ("HR", "home_run"),
    ("IW", "intentional_walk"),
    ("I", "intentional_walk"),
    ("K", "strikeout"),
    ("OA", "other_advance"),
    ("PB", "passed_ball"),
    ("SB", "stolen_base"),
    ("SH", "sac_bunt"),
    ("SF", "sac_fly"),
    ("WP", "wild_pitch"),
    ("W", "walk"),
    ("C", "catcher_interference"),
]

# Event types where the batter does not take a plate appearance (pure
# baserunning / game events attached to an in-progress at-bat).
_NON_PA_TYPES = {
    "no_play", "balk", "pickoff_caught_stealing", "pickoff", "caught_stealing",
    "defensive_indifference", "other_advance", "passed_ball", "stolen_base",
    "wild_pitch",
}

# Event types where the batter's default (i.e. if not overridden by an
# explicit "B-x" advance or a "(B)" out marker) is to reach base safely, and
# at which base.
_BATTER_DEFAULT_BASE = {
    "single": "1", "double": "2", "triple": "3", "home_run": "H",
    "walk": "1", "intentional_walk": "1", "hit_by_pitch": "1",
    "reached_on_error": "1", "fielders_choice": "1",
    "catcher_interference": "1",
}

# Event types whose batter-default-base is 1B, and where -- per Retrosheet's
# convention -- any runner not explicitly given an advance is force-advanced
# one base if (and only if) doing so is required by the batter now
# occupying 1B (the classic "force" chain: runner on 1st forced to 2nd,
# which forces a runner already on 2nd to 3rd, and so on).
_FORCE_CHAIN_TYPES = {"single", "walk", "intentional_walk", "hit_by_pitch",
                      "reached_on_error", "fielders_choice", "catcher_interference"}

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_ADV_RE = re.compile(r"^([123B])([X-])([123H])")


def _classify_subcode(code: str) -> str:
    # Single/double/triple ("S9", "D8", "T3", ...) and the ground-rule-double
    # spelling ("DGR") are checked first: a plain letter+digit code would
    # otherwise fall all the way through to the "other" bucket below, since
    # they're not literal prefixes shared with anything in _PREFIX_RULES.
    if code.startswith("DGR"):
        return "double"
    if len(code) >= 2 and code[0] in ("S", "D", "T") and code[1].isdigit():
        return {"S": "single", "D": "double", "T": "triple"}[code[0]]
    for prefix, event_type in _PREFIX_RULES:
        if code.startswith(prefix):
            return event_type
    if code and code[0].isdigit():
        return "fielding_out"
    return "other"


def _split_event_field(event_field: str) -> tuple[str, list[str]]:
    """basic_play[/modifiers...][.advances] -> (basic_play, [advance tokens])."""
    left, _, adv_part = event_field.partition(".")
    basic_play = left.split("/", 1)[0]
    advances = [a for a in adv_part.split(";") if a] if adv_part else []
    return basic_play, advances


def _parse_advance_token(token: str) -> tuple[str, str, bool] | None:
    """"2X3(...)" -> ('2','3',True); "1-H(UR)" -> ('1','H',False)."""
    m = _ADV_RE.match(token)
    if not m:
        return None
    origin, sign, dest = m.groups()
    return origin, dest, sign == "X"


def _resolve_play(bases: dict[str, str | None], outs: int, event_field: str) -> dict:
    """Apply one play's event string to the current (bases, outs) state.

    Returns a dict with: event_type, is_plate_appearance, new_bases,
    outs_on_play, runs_scored.
    """
    raw_basic, adv_tokens = _split_event_field(event_field)

    # Extract "(<base>)" / "(B)" out markers embedded in the basic play code
    # itself (used for multi-out fielding plays, e.g. "64(1)3" for a 6-4-3
    # double play), then strip them so prefix classification below sees a
    # clean code.
    embedded_outs = set(_PAREN_RE.findall(raw_basic))
    stripped_basic = _PAREN_RE.sub("", raw_basic)

    # A basic play can list multiple semicolon-separated sub-codes for
    # simultaneous baserunning events (e.g. a double steal: "SB3;SB2").
    sub_codes = stripped_basic.split(";") if ";" in stripped_basic else [stripped_basic]
    primary_code = sub_codes[0]
    event_type = _classify_subcode(primary_code)

    if event_type == "no_play":
        return {"event_type": "no_play", "is_plate_appearance": False,
                "new_bases": dict(bases), "outs_on_play": 0, "runs_scored": 0}

    new_bases: dict[str, str | None] = dict(bases)
    outs_on_play = 0
    runs_scored = 0
    explicit_origins: set[str] = set()

    def _score_or_place(origin: str, dest: str, is_out: bool) -> None:
        nonlocal outs_on_play, runs_scored
        if origin != "B":
            new_bases[origin] = None
        if is_out:
            outs_on_play += 1
            return
        if dest == "H":
            runs_scored += 1
        else:
            new_bases[dest] = "runner"

    # 1) Embedded "(<base>)"/"(B)" outs from the basic play code (GDP/TP shapes).
    for origin in embedded_outs:
        if origin in ("1", "2", "3") and new_bases.get(origin):
            new_bases[origin] = None
            outs_on_play += 1
            explicit_origins.add(origin)
        elif origin == "B":
            explicit_origins.add("B")
            outs_on_play += 1  # batter out; no base to clear

    # 2) Default baserunning movement for SB/CS/PO/POCS sub-codes (each
    #    sub-code names its *destination* base; the runner is assumed to be
    #    coming from one base back, unless an explicit advance below
    #    overrides it).
    for code in sub_codes:
        sub_type = _classify_subcode(code)
        if sub_type not in ("stolen_base", "caught_stealing", "pickoff", "pickoff_caught_stealing"):
            continue
        m = re.match(r"^[A-Z]+(H|[23])", code)
        if not m:
            continue
        dest = m.group(1)
        origin = {"2": "1", "3": "2", "H": "3"}.get(dest)
        if origin is None or origin in explicit_origins:
            continue
        is_out = sub_type in ("caught_stealing", "pickoff", "pickoff_caught_stealing")
        if is_out or new_bases.get(origin):
            _score_or_place(origin, dest, is_out)
            explicit_origins.add(origin)

    # 3) Explicit advance list ("1-2", "2-H", "1X2(...)", "B-1", ...) --
    #    always overrides whatever step 2 assumed for the same origin.
    for token in adv_tokens:
        parsed = _parse_advance_token(token)
        if not parsed:
            continue
        origin, dest, is_out = parsed
        if origin != "B" and not bases.get(origin) and origin not in explicit_origins:
            # Advance references a base we don't think is occupied (can
            # happen for baserunning sub-events chained onto this play) --
            # trust the explicit annotation anyway.
            pass
        _score_or_place(origin, dest, is_out)
        explicit_origins.add(origin)

    # 4) Batter's own resolution, if not already covered by an explicit
    #    "B-x"/"BXx" advance or an embedded "(B)" out marker.
    is_plate_appearance = event_type not in _NON_PA_TYPES
    if is_plate_appearance and "B" not in explicit_origins:
        if event_type in _BATTER_DEFAULT_BASE:
            _score_or_place("B", _BATTER_DEFAULT_BASE[event_type], is_out=False)
        else:
            # strikeout, fielding_out, and anything unrecognized default to
            # "batter is out" -- the standard case for a plate appearance
            # that isn't explicitly a way of reaching base.
            outs_on_play += 1

    # 5) Force-advance chain for any *unmentioned* runner, for event types
    #    where the batter's default base is 1B (see _FORCE_CHAIN_TYPES).
    #
    # Whether a runner is forced depends only on the *original* (pre-play)
    # base occupancy, not on where anyone ends up: the runner on 2nd is
    # forced to 3rd iff there was *also* a runner on 1st originally (who is
    # in turn forced off 1st by the batter); the runner on 3rd is forced
    # home iff 1st and 2nd were *both* originally occupied. This holds
    # regardless of whether the base-1/2 runner's own advance was given
    # explicitly above (an explicit "1-2" doesn't break the force chain for
    # whoever was on 2nd) -- only skip re-applying a base whose origin is
    # already in explicit_origins.
    if event_type in _FORCE_CHAIN_TYPES and new_bases.get("1") is not None:
        force_1_2 = bases.get("1") is not None
        force_2_3 = force_1_2 and bases.get("2") is not None
        force_3_h = force_2_3 and bases.get("3") is not None
        if force_1_2 and "1" not in explicit_origins:
            _score_or_place("1", "2", is_out=False)
            explicit_origins.add("1")
        if force_2_3 and "2" not in explicit_origins:
            _score_or_place("2", "3", is_out=False)
            explicit_origins.add("2")
        if force_3_h and "3" not in explicit_origins:
            _score_or_place("3", "H", is_out=False)
            explicit_origins.add("3")

    return {
        "event_type": event_type,
        "is_plate_appearance": is_plate_appearance,
        "new_bases": new_bases,
        "outs_on_play": outs_on_play,
        "runs_scored": runs_scored,
    }


def _bases_key(bases: dict[str, str | None]) -> str:
    return "".join("1" if bases.get(b) else "0" for b in ("1", "2", "3"))


def parse_event_file(text: str, season: int) -> list[dict]:
    """Parse one Retrosheet .EVA/.EVN team-season event file into a flat
    list of per-play row dicts, one per game's plate appearances and
    baserunning events, in file order."""
    rows: list[dict] = []
    reader = csv.reader(io.StringIO(text))

    game_id = None
    visiting_team = home_team = game_date = None
    bases: dict[str, str | None] = {"1": None, "2": None, "3": None}
    outs = 0
    away_score = home_score = 0
    play_seq = 0
    half_key = None  # (inning, team) of the half-inning currently in progress
    game_rows: list[dict] = []

    def _flush_game():
        if game_rows:
            game_rows[0]["is_first_play_of_game"] = True
            game_rows[-1]["is_last_play_of_game"] = True
            rows.extend(game_rows)

    for fields in reader:
        if not fields:
            continue
        tag = fields[0]

        if tag == "id":
            _flush_game()
            game_id = fields[1]
            visiting_team = home_team = game_date = None
            bases = {"1": None, "2": None, "3": None}
            outs = 0
            away_score = home_score = 0
            play_seq = 0
            half_key = None
            game_rows = []

        elif tag == "info":
            if len(fields) < 3:
                continue
            key, value = fields[1], fields[2]
            if key == "visteam":
                visiting_team = value
            elif key == "hometeam":
                home_team = value
            elif key == "date":
                game_date = value

        elif tag == "play":
            if len(fields) < 7:
                continue
            inning = int(fields[1])
            team = int(fields[2])  # 0 = visiting team batting, 1 = home team batting
            batter_id = fields[3]
            event_field = ",".join(fields[6:])  # defensive: event field itself never has a comma in practice

            this_half = (inning, team)
            if this_half != half_key:
                bases = {"1": None, "2": None, "3": None}
                outs = 0
                half_key = this_half

            result = _resolve_play(bases, outs, event_field)
            if result["event_type"] == "no_play":
                continue

            bases_before_key = _bases_key(bases)
            batting_team_home = team == 1

            if batting_team_home:
                home_score_before, away_score_before = home_score, away_score
                home_score += result["runs_scored"]
            else:
                home_score_before, away_score_before = home_score, away_score
                away_score += result["runs_scored"]

            bases = result["new_bases"]
            outs = min(outs + result["outs_on_play"], 3)
            play_seq += 1

            game_rows.append({
                "game_id": game_id,
                "season": season,
                "game_date": game_date,
                "visiting_team": visiting_team,
                "home_team": home_team,
                "inning": inning,
                "batting_team_home": batting_team_home,
                "batter_id": batter_id,
                "event_raw": event_field,
                "event_type": result["event_type"],
                "is_plate_appearance": result["is_plate_appearance"],
                "outs_before": outs - result["outs_on_play"] if outs - result["outs_on_play"] >= 0 else 0,
                "outs_on_play": result["outs_on_play"],
                "bases_before": bases_before_key,
                "bases_after": _bases_key(bases),
                "runs_scored": result["runs_scored"],
                "home_score_before": home_score_before,
                "away_score_before": away_score_before,
                "home_score_after": home_score,
                "away_score_after": away_score,
                "play_seq": play_seq,
                "is_first_play_of_game": False,
                "is_last_play_of_game": False,
            })

    _flush_game()
    return rows
