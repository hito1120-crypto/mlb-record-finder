"""Phase 3: free-form natural-language questions, translated to SQL by the
Gemini API and run against the same DuckDB database the templates in
query/templates.py use.

Flow: normalize the question -> check the local cache -> ask Gemini Flash to
generate SQL (self-flagging whether it thinks the question is too complex for
it) -> validate the SQL (SELECT-only, single statement, binds cleanly against
the live schema) -> on failure, retry once with Flash -> on a second failure,
or an immediate complexity self-flag, escalate once to Pro -> execute and
cache the SQL that worked.

This module knows nothing about i18n/terminal rendering -- like
query/templates.py, it returns data (NLQueryResult) and cli.py renders it.

SDK: google-genai (`pip install google-genai`, `from google import genai`).
Verified against the official README (github.com/googleapis/python-genai) and
ai.google.dev docs as of 2026-09: genai.Client() auto-picks up GEMINI_API_KEY
(or GOOGLE_API_KEY) from the environment, client.models.generate_content(...)
is the content-generation call, and response_mime_type="application/json" +
response_json_schema=<schema dict> forces schema-conformant JSON output
(no manual code-fence stripping needed). Model choice: gemini-3.8-flash is
the current GA cost-tier default; gemini-3.1-pro-preview is used as the
fallback -- gemini-2.5-pro looks GA in the docs but the live API rejects it
for new users/keys ("no longer available to new users"), and its own error
message points at gemini-3.1-pro-preview instead (confirmed empirically
against the live API, 2026-09).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL_PATH = ROOT / "transform" / "schema.sql"
CACHE_PATH = ROOT / "data" / "processed" / "nl2sql_cache.json"

FLASH_MODEL = "gemini-3.8-flash"
# gemini-2.5-pro returns 404 "no longer available to new users" as of this
# writing (confirmed against the live API, despite docs listing it as GA) --
# the API's own error message points to gemini-3.1-pro-preview instead.
PRO_MODEL = "gemini-3.1-pro-preview"

MAX_RESULT_ROWS = 500

SQL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string"},
        "complexity": {"type": "string", "enum": ["simple", "complex"]},
        "note": {"type": "string"},
    },
    "required": ["sql", "complexity", "note"],
}

# Keyword guard applied to the generated SQL in addition to the SELECT/WITH-only
# check below -- belt and suspenders against a model emitting a mutating or
# administrative statement.
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|COPY|"
    r"EXPORT|IMPORT|PRAGMA|CALL|GRANT|REVOKE|VACUUM|CHECKPOINT|LOAD|INSTALL|"
    r"SET|RESET|BEGIN|COMMIT|ROLLBACK|TRUNCATE|MERGE)\b",
    re.IGNORECASE,
)
_SELECT_START_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")

# Views/tables whose data comes from retrosheet_playbyplay -- coverage for
# these is limited to the most recent 5 ingested seasons, and the parser's
# accuracy is known to degrade the further back play-by-play data goes.
_PBP_MARKERS = (
    "retrosheet_playbyplay",
    "v_run_expectancy",
    "v_win_probability",
    "v_play_leverage",
    "v_playbyplay_names",
    "v_half_inning_runs",
)


class NLQueryError(Exception):
    """Raised when SQL generation or validation fails (caught by the caller,
    which decides whether to retry / escalate / give up)."""


@dataclass
class NLQueryResult:
    question: str
    lang: str
    sql: str
    df: pd.DataFrame
    model_used: str
    from_cache: bool
    caveats: list[str] = field(default_factory=list)
    note: str = ""


def get_schema_context(con: duckdb.DuckDBPyConnection) -> str:
    """Builds the schema description embedded in the system prompt: an exact,
    always-current table/view -> column listing pulled live from
    information_schema (so it can never drift from what ingest/*.py has
    actually loaded), followed by transform/schema.sql's own view definitions
    and comments verbatim -- those comments carry semantics (join keys, known
    approximations, ID-scheme gotchas) a bare column list can't express."""
    rows = con.execute(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_type, table_name
        """
    ).fetchall()

    base_tables, views = [], []
    for name, table_type in rows:
        cols = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [name],
        ).fetchall()
        line = f"{name}({', '.join(c[0] for c in cols)})"
        (views if table_type == "VIEW" else base_tables).append(line)

    schema_sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8") if SCHEMA_SQL_PATH.exists() else ""

    return "\n".join([
        "## Base tables (raw ingested data)",
        *base_tables,
        "",
        "## Views (derived; see the SQL definitions below for exact semantics)",
        *views,
        "",
        "## View definitions (transform/schema.sql, verbatim) -- read the comments,",
        "## they explain join keys, ID-scheme differences, and known approximations",
        schema_sql,
    ])


def _build_system_prompt(schema_desc: str) -> str:
    return f"""You are a DuckDB SQL generator for an MLB historical statistics database.
Convert the user's natural-language baseball question (asked in Japanese or English) into
a single DuckDB SELECT (or WITH ... SELECT) statement that answers it.

Database schema:
{schema_desc}

Rules:
- Respond with the "sql", "complexity", and "note" fields per the response schema. No prose outside those fields.
- The SQL must be exactly one read-only SELECT/WITH statement. Never write INSERT/UPDATE/DELETE/DROP/ALTER/CREATE or any other mutating or administrative statement.
- Column names that start with a digit (e.g. "2B", "3B" in lahman_batting) must be double-quoted.
- Prefer the documented views (v_people, v_team_seasons, v_batting_by_franchise, v_run_expectancy,
  v_win_probability, v_play_leverage, etc.) over re-deriving the same joins/logic against raw tables --
  the views already encode the right join keys and known approximations.
- retrosheet_playbyplay.batter_id uses Retrosheet's own ID scheme (e.g. "troum001"), NOT Lahman's
  playerID ("troutmi01") -- join through v_playbyplay_names (retroID -> full_name) for player names on
  play-by-play-based queries, and through v_people/v_batting_by_franchise (playerID) for Lahman-based ones.
- statcast_pitches.player_name is the PITCHER, not the batter. For batter-side stats, join
  statcast_pitches.batter (a numeric MLBAM id) to player_id_lookup.key_mlbam for the batter's name.
- A season still in progress (e.g. the current season) has no lahman_batting/lahman_pitching rows yet --
  its data only exists in mlbapi_batting/mlbapi_pitching (see mlb_stats_api's comment block above). If a
  question could span both a finished season (Lahman) and an in-progress one (mlbapi), or if you are
  joining v_war_season (which already spans both) back to raw per-column batting/pitching stats, read the
  schema listing above for BOTH lahman_batting/lahman_pitching AND mlbapi_batting/mlbapi_pitching and
  COALESCE every column that exists in both -- e.g. COALESCE(b.RBI, m.RBI) AS RBI, not just b.RBI AS RBI.
  Never silently read a column from only one of the two tables when the other table also has that same
  column, and never rely on memory for which columns overlap -- always check the live column lists in the
  schema description above, since mlbapi_batting/mlbapi_pitching intentionally carry fewer columns than
  their Lahman counterparts (e.g. Lahman-only batting columns currently include SH and GIDP). For a
  Lahman-only column with no mlbapi equivalent, select it directly from Lahman (it will simply be NULL for
  an mlbapi-only season) and do not fabricate a COALESCE against a column that doesn't exist.
- For any question about WAR (Wins Above Replacement), use v_war_season (columns: playerID, full_name,
  season, teams, pa, wraa_park_adj, baserunning_runs, fielding_runs, primary_position, position_adj_runs,
  ip, fip, pitching_raa_park_adj, war). Only seasons 2015-2026 are supported -- it returns zero rows for
  any other season. This project's WAR is its own independently-derived, simplified estimate; it is NOT
  FanGraphs (fWAR) or Baseball-Reference (bWAR) and will not match those sites' numbers -- always say so
  in the "note" field when answering a WAR question. For a "top WAR" leaderboard, filter out small
  samples (e.g. "WHERE pa >= 100 OR ip >= 20") the same way the ranking templates do.
- If the question is ambiguous, make a reasonable baseball-domain interpretation and briefly explain
  the choice in "note" (leave "note" as "" if no explanation is needed).
- Set "complexity" to "complex" only when the question needs multi-step statistical reasoning, exact-match
  logic across several disjoint conditions, or judgment calls a small/cheap model is likely to get wrong
  (e.g. open-ended "is this the first time in history that ..." questions). Otherwise use "simple".
"""


def _generate_sql(
    client: genai.Client,
    model: str,
    schema_desc: str,
    question: str,
    lang: str,
    previous_error: str | None = None,
) -> dict:
    user_text = f"Question language: {lang}\nQuestion: {question}"
    if previous_error:
        user_text += (
            "\n\nYour previous SQL failed validation with this error -- fix it "
            f"and try again:\n{previous_error}"
        )
    try:
        response = client.models.generate_content(
            model=model,
            contents=user_text,
            config=genai_types.GenerateContentConfig(
                system_instruction=_build_system_prompt(schema_desc),
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=SQL_RESPONSE_SCHEMA,
            ),
        )
    except genai_errors.APIError as e:
        raise NLQueryError(f"Gemini API request failed: {e.code} {e.message}") from e

    text = response.text
    if not text:
        raise NLQueryError("Gemini returned an empty response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise NLQueryError(f"model did not return valid JSON: {e}") from e
    if not isinstance(data.get("sql"), str) or not data["sql"].strip():
        raise NLQueryError("model response was missing a non-empty 'sql' field")
    return data


def _validate_and_execute(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if len(statements) != 1:
        raise NLQueryError("exactly one SQL statement is required (no multi-statement SQL)")
    stmt = statements[0]

    if not _SELECT_START_RE.match(stmt):
        raise NLQueryError("only SELECT/WITH statements are allowed")
    if _FORBIDDEN_RE.search(stmt):
        raise NLQueryError("statement contains a disallowed keyword (non-SELECT operation)")

    # EXPLAIN plans (binds table/column names) without executing -- the
    # cheapest reliable way to check the SQL references real tables/columns.
    try:
        con.execute(f"EXPLAIN {stmt}")
    except duckdb.Error as e:
        raise NLQueryError(f"query does not match the database schema: {e}") from e

    if not _LIMIT_RE.search(stmt):
        stmt = f"{stmt}\nLIMIT {MAX_RESULT_ROWS}"

    try:
        return con.execute(stmt).fetchdf()
    except duckdb.Error as e:
        raise NLQueryError(f"query failed to execute: {e}") from e


def _try_generate(
    con: duckdb.DuckDBPyConnection,
    client: genai.Client,
    model: str,
    schema_desc: str,
    question: str,
    lang: str,
    previous_error: str | None = None,
) -> tuple[str, pd.DataFrame, str, str]:
    """One generate-then-validate attempt. Raises NLQueryError on any failure
    (bad JSON, invalid SQL, schema mismatch, execution error)."""
    parsed = _generate_sql(client, model, schema_desc, question, lang, previous_error)
    df = _validate_and_execute(con, parsed["sql"])
    return parsed["sql"], df, parsed.get("complexity", "simple"), parsed.get("note", "")


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip()).casefold()


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _detect_caveats(question: str, sql: str) -> list[str]:
    """Coverage caveats to surface alongside the answer -- based on years
    mentioned in the question or baked into the generated SQL, and whether
    the query touches play-by-play-derived data. See transform/schema.sql's
    comments and README for exactly what's approximate/limited and why."""
    caveats: list[str] = []
    years = [int(y) for y in _YEAR_RE.findall(question)] + [int(y) for y in _YEAR_RE.findall(sql)]
    if years and min(years) <= 1900:
        caveats.append("19th_century")
    if years and min(years) <= 1929 and any(marker in sql for marker in _PBP_MARKERS):
        caveats.append("early_pbp")
    return caveats


def answer_natural_language_question(
    con: duckdb.DuckDBPyConnection, question: str, lang: str = "ja"
) -> NLQueryResult:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise NLQueryError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in the environment.")

    norm = _normalize_question(question)
    cache_key = f"{lang}:{norm}"
    cache = _load_cache()
    cached = cache.get(cache_key)
    if cached:
        try:
            df = _validate_and_execute(con, cached["sql"])
            return NLQueryResult(
                question, lang, cached["sql"], df, cached["model"], True,
                _detect_caveats(question, cached["sql"]), cached.get("note", ""),
            )
        except NLQueryError:
            pass  # schema/data may have changed since this was cached -- regenerate below

    schema_desc = get_schema_context(con)
    client = genai.Client()

    sql: str | None = None
    df: pd.DataFrame | None = None
    note = ""
    last_error: str | None = None
    flagged_complex = False
    model_used = FLASH_MODEL

    try:
        sql, df, complexity, note = _try_generate(con, client, FLASH_MODEL, schema_desc, question, lang)
        if complexity == "complex":
            sql, df = None, None  # discard -- self-flagged, escalate straight to Pro below
            flagged_complex = True
            last_error = note or "question self-flagged as complex by the fast model"
    except NLQueryError as e:
        last_error = str(e)

    # A validation/generation failure gets exactly one Flash retry with the
    # error attached; a complexity self-flag skips the retry and escalates
    # straight to Pro.
    if sql is None and not flagged_complex:
        try:
            sql, df, complexity, note = _try_generate(
                con, client, FLASH_MODEL, schema_desc, question, lang, previous_error=last_error
            )
        except NLQueryError as e2:
            last_error = str(e2)

    if sql is None:
        try:
            sql, df, complexity, note = _try_generate(
                con, client, PRO_MODEL, schema_desc, question, lang, previous_error=last_error
            )
            model_used = PRO_MODEL
        except NLQueryError as e3:
            raise NLQueryError(
                f"Failed to generate a valid SQL query even with the fallback model: {e3}"
            ) from e3

    cache[cache_key] = {"sql": sql, "model": model_used, "question": question, "note": note}
    _save_cache(cache)

    return NLQueryResult(question, lang, sql, df, model_used, False, _detect_caveats(question, sql), note)
