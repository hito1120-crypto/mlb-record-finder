"""English UI strings. Mirror of i18n/ja.py -- keep keys identical between the two."""

STRINGS = {
    "app_title": "MLB Record Finder (mlb-record-finder)",
    "menu_title": "\n--- Menu: choose a record template to explore ---",
    "menu_items": [
        "Search home runs by exit velocity + distance (Statcast)",
        "All-time HR ranking for a given franchise (Lahman)",
        "Players who hit two season thresholds (e.g. HR + SB) (Lahman)",
        "Best winning percentage over any N-game window (Retrosheet)",
        "Players who hit three season thresholds (e.g. 3B + HR + SB) (Lahman)",
        "Barrel% / Hard-Hit% ranking (Statcast)",
        "Expected stats (xwOBA/xBA/xSLG) ranking vs. actual results (Statcast)",
        "Sprint Speed ranking (Statcast)",
        "Games with a leadoff HR AND a walk-off HR (Retrosheet Play-by-Play)",
        "Highest-leverage plate appearances (Retrosheet Play-by-Play)",
        "Bat Speed / Swing Length ranking (Statcast)",
        "Outs Above Average (OAA) fielding ranking (Statcast)",
        "Arm Angle ranking (Statcast)",
        "Season WAR ranking (this tool's own simplified estimate, 2015-2026 only) (Lahman/Statcast/Retrosheet)",
        "Free-form question mode: ask in plain language (Gemini API generates SQL)",
    ],
    "menu_exit": "Exit",
    "prompt_select": "Select a number",
    "invalid_choice": "Invalid choice, please try again.",
    "invalid_number": "Please enter a number.",
    "goodbye": "Goodbye.",

    "prompt_min_exit_velo": "Minimum exit velocity (mph)",
    "prompt_min_distance": "Minimum distance (ft)",
    "prompt_result_limit": "Max rows to display (default: {default})",

    "prompt_franchise_search": "Enter part of a team name (e.g. Yankees)",
    "franchise_no_match": "No matching franchise found, please try again.",
    "franchise_multiple_matches": "Multiple matches found, pick a number:",
    "franchise_selected": "Selected franchise: {name} ({franch_id})",

    "prompt_year": "Season year (e.g. 2019)",
    "prompt_hr_min": "Minimum home runs (HR)",
    "prompt_sb_min": "Minimum stolen bases (SB)",
    "prompt_3b_min": "Minimum triples (3B)",

    "prompt_window_games": "Window size N in games (e.g. 10)",
    "prompt_team_optional": "Team code to filter on (blank = all teams, e.g. NYA)",

    "prompt_statcast_season": "Season (available: {seasons})",
    "prompt_min_batted_balls": "Minimum batted-ball events",
    "prompt_min_pa": "Minimum plate appearances (PA)",
    "prompt_min_sprint_opp": "Minimum sprint opportunities (min_opp)",
    "prompt_min_swings": "Minimum tracked swings",
    "prompt_min_pitches": "Minimum tracked pitches",
    "prompt_min_ip_war": "Minimum innings pitched (IP) for the pitching side",
    "prompt_oaa_position": "Select a position by number",
    "oaa_catcher_notice": (
        "Choose a fielding position "
        "(Baseball Savant's OAA leaderboard does not cover catchers):"
    ),

    "prompt_leverage_start_date": "Start date (YYYY-MM-DD, blank = no limit)",
    "prompt_leverage_end_date": "End date (YYYY-MM-DD, blank = no limit)",
    "prompt_batter_name_optional": "Filter by batter name (blank = all players, e.g. Ohtani)",

    "no_results": "No results matched those conditions.",
    "results_count": "{n} result(s) found.",

    "data_missing": "Required data has not been ingested yet: {tables}",
    "run_ingest_prompt": "Run the data ingest scripts now? (y/n)",
    "running_ingest": "Fetching {name} data... (this only takes a while the first time)",
    "ingest_done": "Data ingest complete.",
    "ingest_skipped": "Skipped ingest. Please run ingest/*.py first.",

    "coverage_notice": (
        "Note: Statcast exit-velocity/distance data covers the 2015 season onward,\n"
        "and Lahman player/season stats (official SABR edition) cover through the 2025 season.\n"
        "Play-by-Play data (templates 9-10) is only fetched for the most recent 5 seasons, and\n"
        "Retrosheet's own play-by-play coverage is only partial before the 1920s."
    ),

    "war_notice": (
        "⚠️ This WAR is this tool's own simplified estimate, independently built from\n"
        "public wOBA/FIP-style sabermetric methodology. It is NOT FanGraphs (fWAR) or\n"
        "Baseball-Reference (bWAR) and will not match those sites' numbers. Only the\n"
        "2015-2026 seasons are supported. See query/war.py's comments for the full\n"
        "list of simplifications."
    ),
    "war_unsupported_season": "WAR is not supported for that season: {error}",
    "war_provisional_notice": (
        "⚠️ PROVISIONAL: this season has no Lahman data yet, so these numbers\n"
        "are built from live MLB Stats API totals as of the last data refresh, not a\n"
        "final Lahman-verified season line. Park factor is neutral (1.0) for this\n"
        "season (Retrosheet Game Logs aren't published yet either)."
    ),

    "oaa_position_labels": {
        "1B": "First Base (1B)",
        "2B": "Second Base (2B)",
        "3B": "Third Base (3B)",
        "SS": "Shortstop (SS)",
        "LF": "Left Field (LF)",
        "CF": "Center Field (CF)",
        "RF": "Right Field (RF)",
        "IF": "Infield, all (IF)",
        "OF": "Outfield, all (OF)",
        "ALL": "All positions (catchers excluded)",
    },

    "column_labels": {
        "player_name": "Player",
        "full_name": "Player",
        "game_date": "Date",
        "away_team": "Away",
        "home_team": "Home",
        "exit_velocity_mph": "Exit Velo (mph)",
        "distance_ft": "Distance (ft)",
        "launch_angle_deg": "Launch Angle (deg)",
        "franchID": "Franchise ID",
        "career_hr_with_team": "Career HR",
        "first_year": "First Year",
        "last_year": "Last Year",
        "season": "Season",
        "teams": "Team(s)",
        "HR": "HR",
        "SB": "SB",
        "X3B": "3B",
        "team": "Team",
        "window_start_date": "Start Date",
        "window_end_date": "End Date",
        "wins": "Wins",
        "win_pct": "Win %",
        "batted_balls": "Batted Balls",
        "barrels": "Barrels",
        "barrel_pct": "Barrel%",
        "hard_hit_pct": "Hard-Hit%",
        "avg_exit_velo": "Avg Exit Velo (mph)",
        "pa": "PA",
        "ab": "AB",
        "ba": "BA",
        "xba": "xBA",
        "slg": "SLG",
        "xslg": "xSLG",
        "woba": "wOBA",
        "xwoba": "xwOBA",
        "ba_diff": "BA Diff",
        "slg_diff": "SLG Diff",
        "woba_diff": "wOBA Diff",
        "luck": "Trend",
        "position": "Position",
        "age": "Age",
        "competitive_runs": "Opportunities",
        "sprint_speed": "Sprint Speed (ft/s)",
        "visiting_team": "Visitor",
        "leadoff_batter": "Leadoff Batter",
        "walkoff_inning": "Walk-off Inning",
        "walkoff_batter": "Walk-off Batter",
        "final_home_score": "Final Score (Home)",
        "final_away_score": "Final Score (Away)",
        "batter_name": "Batter",
        "event_type": "Event Type",
        "event_raw": "Play Detail (raw)",
        "win_prob_home_before": "Win Prob. Before (Home)",
        "win_prob_home_after": "Win Prob. After (Home)",
        "leverage_index": "Leverage Index",
        "inning": "Inning",
        "game_id": "Game ID",
        "swings": "Swings",
        "avg_bat_speed": "Avg Bat Speed (mph)",
        "max_bat_speed": "Max Bat Speed (mph)",
        "avg_swing_length": "Avg Swing Length (ft)",
        "primary_position": "Primary Position",
        "outs_above_average": "OAA",
        "fielding_runs_prevented": "Fielding Runs Prevented",
        "actual_success_rate": "Actual Success Rate (%)",
        "adj_estimated_success_rate": "Est. Success Rate (%)",
        "diff_success_rate": "Success Rate Diff (%)",
        "pitches": "Pitches",
        "avg_arm_angle": "Avg Arm Angle (deg)",
        "min_arm_angle": "Min Arm Angle (deg)",
        "max_arm_angle": "Max Arm Angle (deg)",
        "PA": "PA",
        "IP": "IP",
        "FIP": "FIP",
        "wRAA_park_adj": "wRAA (Park-Adj.)",
        "baserunning_runs": "Baserunning Runs",
        "fielding_runs": "Fielding Runs",
        "position_adj_runs": "Positional Adj. Runs",
        "pitching_RAA_park_adj": "Pitching RAA (Park-Adj.)",
        "WAR": "WAR (simplified)",
    },

    "luck_labels": {
        "overperforming": "Overperforming (lucky)",
        "underperforming": "Underperforming (unlucky)",
        "as expected": "As expected",
    },

    "prompt_free_question": "Type your question in Japanese or English, in your own words",
    "nl_no_api_key": (
        "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in the environment. "
        "Set it first to use free-form question mode."
    ),
    "nl_generating": "Asking the Gemini API to generate SQL...",
    "nl_cache_hit": "(Reused cached SQL from an earlier identical question)",
    "nl_model_used": "Model used: {model}",
    "nl_sql_label": "--- Generated SQL (for verification) ---",
    "nl_note_label": "Note: {note}",
    "nl_generation_failed": "Failed to generate SQL: {error}",
    "nl_caveats": {
        "19th_century": "Note: 19th-century data has known limits on record completeness.",
        "early_pbp": "Note: Play-by-Play data for this era is only partially covered.",
    },
}
