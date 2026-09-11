"""mlb-record-finder CLI (Phase 1 / MVP).

    python cli.py            # Japanese UI (default)
    python cli.py --lang en  # English UI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
from tabulate import tabulate

# Windows consoles often default to a legacy codepage (e.g. cp932) rather
# than UTF-8, which would otherwise garble the Japanese UI strings.
for _stream in (sys.stdout, sys.stderr, sys.stdin):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from i18n import en as i18n_en
from i18n import ja as i18n_ja
from query import templates as q

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "processed" / "mlb.duckdb"
SCHEMA_PATH = ROOT / "transform" / "schema.sql"

REQUIRED_TABLES = {
    "lahman": ["lahman_people", "lahman_batting", "lahman_teams", "lahman_teams_franchises"],
    "retrosheet": ["retrosheet_gamelogs"],
    "statcast": ["statcast_pitches", "statcast_sprint_speed", "player_id_lookup"],
    "retrosheet_pbp": ["retrosheet_playbyplay"],
}


def load_strings(lang: str) -> dict:
    return i18n_ja.STRINGS if lang == "ja" else i18n_en.STRINGS


def existing_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    return {r[0] for r in rows}


def ensure_data(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    present = existing_tables(con)
    missing_sources = [
        name for name, tables in REQUIRED_TABLES.items()
        if not all(t in present for t in tables)
    ]
    if not missing_sources:
        return

    print(S["data_missing"].format(tables=", ".join(missing_sources)))
    answer = input(S["run_ingest_prompt"] + " ").strip().lower()
    if answer not in ("y", "yes"):
        print(S["ingest_skipped"])
        return

    if "lahman" in missing_sources:
        print(S["running_ingest"].format(name="Lahman"))
        from ingest import lahman
        lahman.download()
        lahman.load_to_duckdb()
    if "retrosheet" in missing_sources:
        print(S["running_ingest"].format(name="Retrosheet"))
        from ingest import retrosheet_gamelogs
        retrosheet_gamelogs.download()
        retrosheet_gamelogs.load_to_duckdb()
    if "statcast" in missing_sources:
        print(S["running_ingest"].format(name="Statcast"))
        from ingest import statcast
        statcast.download()
        statcast.load_to_duckdb()
    if "retrosheet_pbp" in missing_sources:
        print(S["running_ingest"].format(name="Retrosheet Play-by-Play"))
        from ingest import retrosheet_playbyplay
        retrosheet_playbyplay.download()
        retrosheet_playbyplay.load_to_duckdb()
    print(S["ingest_done"])


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    present = existing_tables(con)
    if not any(t in present for tables in REQUIRED_TABLES.values() for t in tables):
        return  # nothing ingested yet -- views would fail to create
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            try:
                con.execute(statement)
            except duckdb.CatalogException:
                pass  # underlying table for this view not ingested yet -- skip it


def print_table(df, S: dict) -> None:
    if df.empty:
        print(S["no_results"])
        return
    print(S["results_count"].format(n=len(df)))
    for col in df.columns:
        if str(df[col].dtype).startswith("datetime64"):
            df[col] = df[col].dt.date
        elif str(df[col].dtype) in ("float64", "float32"):
            df[col] = df[col].round(3)
    if "luck" in df.columns:
        luck_labels = S.get("luck_labels", {})
        df["luck"] = df["luck"].map(lambda v: luck_labels.get(v, v))
    labels = S["column_labels"]
    df = df.rename(columns={c: labels.get(c, c) for c in df.columns})
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))


def prompt_float(prompt: str, S: dict) -> float:
    while True:
        raw = input(prompt + ": ").strip()
        try:
            return float(raw)
        except ValueError:
            print(S["invalid_number"])


def prompt_int(prompt: str, S: dict, default: int | None = None) -> int:
    while True:
        raw = input(prompt + (f" [{default}]" if default is not None else "") + ": ").strip()
        if not raw and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print(S["invalid_number"])


def pick_franchise(con: duckdb.DuckDBPyConnection, S: dict) -> str:
    while True:
        query = input(S["prompt_franchise_search"] + ": ").strip()
        matches = con.execute(
            "SELECT franchID, team_name, franchName FROM v_franchise_current "
            "WHERE franchName ILIKE ? OR team_name ILIKE ? OR franchID ILIKE ? "
            "ORDER BY franchName",
            [f"%{query}%", f"%{query}%", f"%{query}%"],
        ).fetchall()
        if not matches:
            print(S["franchise_no_match"])
            continue
        if len(matches) == 1:
            franch_id, team_name, _ = matches[0]
            print(S["franchise_selected"].format(name=team_name, franch_id=franch_id))
            return franch_id
        print(S["franchise_multiple_matches"])
        for i, (franch_id, team_name, _) in enumerate(matches, start=1):
            print(f"  {i}. {team_name} ({franch_id})")
        choice = prompt_int(S["prompt_select"], S, default=1)
        if 1 <= choice <= len(matches):
            franch_id, team_name, _ = matches[choice - 1]
            print(S["franchise_selected"].format(name=team_name, franch_id=franch_id))
            return franch_id


def prompt_statcast_season(con: duckdb.DuckDBPyConnection, S: dict) -> int:
    seasons = [r[0] for r in con.execute(
        "SELECT DISTINCT game_year FROM statcast_pitches ORDER BY game_year"
    ).fetchall()]
    default = seasons[-1] if seasons else None
    prompt = S["prompt_statcast_season"].format(seasons=", ".join(str(s) for s in seasons))
    return prompt_int(prompt, S, default=default)


def handle_homerun_search(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    min_velo = prompt_float(S["prompt_min_exit_velo"], S)
    min_dist = prompt_float(S["prompt_min_distance"], S)
    limit = prompt_int(S["prompt_result_limit"].format(default=50), S, default=50)
    df = q.homerun_search(con, min_velo, min_dist, limit=limit)
    print_table(df, S)


def handle_team_career_hr(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    franch_id = pick_franchise(con, S)
    limit = prompt_int(S["prompt_result_limit"].format(default=20), S, default=20)
    df = q.team_career_hr_ranking(con, franch_id, limit=limit)
    print_table(df, S)


def handle_season_two_stat(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    year = prompt_int(S["prompt_year"], S)
    hr_min = prompt_int(S["prompt_hr_min"], S)
    sb_min = prompt_int(S["prompt_sb_min"], S)
    df = q.season_two_stat_achievers(con, year, hr_min, sb_min)
    print_table(df, S)


def handle_win_pct_window(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    window_games = prompt_int(S["prompt_window_games"], S, default=10)
    team = input(S["prompt_team_optional"] + ": ").strip().upper() or None
    limit = prompt_int(S["prompt_result_limit"].format(default=20), S, default=20)
    df = q.best_win_pct_window(con, window_games, team=team, limit=limit)
    print_table(df, S)


def handle_season_three_stat(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    year = prompt_int(S["prompt_year"], S)
    triple_min = prompt_int(S["prompt_3b_min"], S)
    hr_min = prompt_int(S["prompt_hr_min"], S)
    sb_min = prompt_int(S["prompt_sb_min"], S)
    df = q.season_three_stat_achievers(con, year, triple_min, hr_min, sb_min)
    print_table(df, S)


def handle_barrel_hard_hit(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    season = prompt_statcast_season(con, S)
    min_bbe = prompt_int(S["prompt_min_batted_balls"], S, default=50)
    limit = prompt_int(S["prompt_result_limit"].format(default=50), S, default=50)
    df = q.barrel_hard_hit_ranking(con, season, min_batted_balls=min_bbe, limit=limit)
    print_table(df, S)


def handle_expected_vs_actual(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    season = prompt_statcast_season(con, S)
    min_pa = prompt_int(S["prompt_min_pa"], S, default=100)
    limit = prompt_int(S["prompt_result_limit"].format(default=50), S, default=50)
    df = q.expected_vs_actual_ranking(con, season, min_pa=min_pa, limit=limit)
    print_table(df, S)


def handle_sprint_speed(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    season = prompt_statcast_season(con, S)
    min_opp = prompt_int(S["prompt_min_sprint_opp"], S, default=10)
    limit = prompt_int(S["prompt_result_limit"].format(default=50), S, default=50)
    df = q.sprint_speed_ranking(con, season, min_opportunities=min_opp, limit=limit)
    print_table(df, S)


def handle_leadoff_and_walkoff(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    limit = prompt_int(S["prompt_result_limit"].format(default=50), S, default=50)
    df = q.leadoff_and_walkoff_hr_games(con, limit=limit)
    print_table(df, S)


def handle_highest_leverage(con: duckdb.DuckDBPyConnection, S: dict) -> None:
    start_date = input(S["prompt_leverage_start_date"] + ": ").strip() or None
    end_date = input(S["prompt_leverage_end_date"] + ": ").strip() or None
    player_name = input(S["prompt_batter_name_optional"] + ": ").strip() or None
    limit = prompt_int(S["prompt_result_limit"].format(default=50), S, default=50)
    df = q.highest_leverage_plays(con, start_date=start_date, end_date=end_date,
                                   player_name=player_name, limit=limit)
    print_table(df, S)


HANDLERS = [
    handle_homerun_search,
    handle_team_career_hr,
    handle_season_two_stat,
    handle_win_pct_window,
    handle_season_three_stat,
    handle_barrel_hard_hit,
    handle_expected_vs_actual,
    handle_sprint_speed,
    handle_leadoff_and_walkoff,
    handle_highest_leverage,
]


def main() -> None:
    parser = argparse.ArgumentParser(description="mlb-record-finder CLI")
    parser.add_argument("--lang", choices=["ja", "en"], default="ja")
    args = parser.parse_args()
    S = load_strings(args.lang)

    print(S["app_title"])
    print(S["coverage_notice"])

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_data(con, S)
        apply_schema(con)

        while True:
            print(S["menu_title"])
            for i, label in enumerate(S["menu_items"], start=1):
                print(f"  {i}. {label}")
            print(f"  0. {S['menu_exit']}")

            raw = input(S["prompt_select"] + ": ").strip()
            if raw == "0":
                print(S["goodbye"])
                break
            try:
                choice = int(raw)
                handler = HANDLERS[choice - 1]
            except (ValueError, IndexError):
                print(S["invalid_choice"])
                continue

            handler(con, S)
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
