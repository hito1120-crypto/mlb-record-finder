"""Japanese UI strings. Mirror of i18n/en.py -- keep keys identical between the two."""

STRINGS = {
    "app_title": "MLB記録発掘ツール (mlb-record-finder)",
    "menu_title": "\n--- メニュー: 調べたい記録テンプレートを選んでください ---",
    "menu_items": [
        "打球速度・飛距離条件を満たす本塁打を検索 (Statcast)",
        "特定球団の通算本塁打ランキング (Lahman)",
        "あるシーズンでの2条件達成者一覧 (例: 本塁打+盗塁) (Lahman)",
        "チームの任意N試合ウィンドウでの最高勝率記録 (Retrosheet)",
        "あるシーズンでの3条件複合達成者検索 (例: 三塁打+本塁打+盗塁) (Lahman)",
    ],
    "menu_exit": "終了",
    "prompt_select": "番号を選択してください",
    "invalid_choice": "無効な選択です。もう一度入力してください。",
    "invalid_number": "数値を入力してください。",
    "goodbye": "終了します。",

    "prompt_min_exit_velo": "最低打球速度 (mph) を入力してください",
    "prompt_min_distance": "最低飛距離 (ft) を入力してください",
    "prompt_result_limit": "表示件数の上限 (デフォルト: {default})",

    "prompt_franchise_search": "球団名の一部を入力してください (例: Yankees)",
    "franchise_no_match": "該当する球団が見つかりませんでした。もう一度入力してください。",
    "franchise_multiple_matches": "複数の候補が見つかりました。番号で選択してください:",
    "franchise_selected": "選択された球団: {name} ({franch_id})",

    "prompt_year": "対象のシーズン(年)を入力してください (例: 2019)",
    "prompt_hr_min": "本塁打(HR)の最低本数を入力してください",
    "prompt_sb_min": "盗塁(SB)の最低個数を入力してください",
    "prompt_3b_min": "三塁打(3B)の最低本数を入力してください",

    "prompt_window_games": "ウィンドウの試合数Nを入力してください (例: 10)",
    "prompt_team_optional": "対象チームコードを入力 (空欄で全チーム対象, 例: NYA)",

    "no_results": "条件に一致する結果は見つかりませんでした。",
    "results_count": "{n}件の結果が見つかりました。",

    "data_missing": "必要なデータがまだ取り込まれていません: {tables}",
    "run_ingest_prompt": "今すぐデータ取得スクリプトを実行しますか? (y/n)",
    "running_ingest": "{name} のデータを取得しています... (初回のみ時間がかかります)",
    "ingest_done": "データ取り込みが完了しました。",
    "ingest_skipped": "データ取得をスキップしました。先に ingest/*.py を実行してください。",

    "coverage_notice": (
        "注記: 打球速度・飛距離データ(Statcast)は直近2シーズンのみ、\n"
        "選手・シーズン成績(Lahman、SABR公式版)は2025年シーズンまで対応しています。"
    ),

    "column_labels": {
        "player_name": "選手名",
        "full_name": "選手名",
        "game_date": "試合日",
        "away_team": "敵地チーム",
        "home_team": "本拠地チーム",
        "exit_velocity_mph": "打球速度(mph)",
        "distance_ft": "飛距離(ft)",
        "launch_angle_deg": "打球角度(度)",
        "franchID": "球団ID",
        "career_hr_with_team": "通算本塁打数",
        "first_year": "初年度",
        "last_year": "最終年度",
        "season": "シーズン",
        "teams": "所属球団",
        "HR": "本塁打",
        "SB": "盗塁",
        "X3B": "三塁打",
        "team": "チーム",
        "window_start_date": "開始日",
        "window_end_date": "終了日",
        "wins": "勝利数",
        "win_pct": "勝率",
    },
}
