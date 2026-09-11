# mlb-record-finder

サラ・ラングス記者(MLB.com)のような、マニアックなMLB記録の発掘・検索ができるCLIツールです。
すべて無料・公開データのみを使用しています。

[English README](README.en.md)

## できること

`python cli.py` を実行すると、8つの定型記録テンプレートから番号を選び、閾値や年度などのパラメータを入力するだけで、DuckDBに対してSQLが実行され、結果が表形式で表示されます。

1. **打球速度・飛距離条件を満たす本塁打を検索** (Statcast) — 例: 打球速度115mph以上 かつ 飛距離470ft以上の本塁打一覧
2. **特定球団の通算本塁打ランキング** (Lahman) — 球団の歴史(移転・改名含む)を通算した選手別HRランキング
3. **あるシーズンでの2条件達成者一覧** (Lahman) — 例: 本塁打30本以上+盗塁20個以上を同一シーズンで達成した選手
4. **チームの任意N試合ウィンドウでの最高勝率記録** (Retrosheet) — 全チーム・全シーズンを対象に、連続N試合での最高勝率を検索
5. **あるシーズンでの3条件複合達成者検索** (Lahman) — 例: 三塁打+本塁打+盗塁の複合達成
6. **バレル率・Hard-Hit%ランキング** (Statcast) — 最低打球イベント数以上の選手を対象に、指定シーズンのバレル率・Hard-Hit%(打球速度95mph以上の割合)をランキング表示
7. **期待成績(xwOBA/xBA/xSLG)ランキングと実成績との乖離** (Statcast) — 指定シーズンの期待成績上位選手を、実成績(wOBA/BA/SLG)との差分・好不調フラグ付きで表示
8. **Sprint Speedランキング** (Statcast) — 指定シーズンの走力(Sprint Speed)上位選手を表示

## データソース

すべて無料・公開データです。

| ソース | 内容 | 収録期間 |
|---|---|---|
| [Lahman Baseball Database (SABR公式版)](https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd) | シーズン・通算成績(打撃・投球・守備・受賞歴) | 1871年〜2025年シーズン |
| [Retrosheet Game Logs](https://www.retrosheet.org/gamelogs/index.html) | チーム単位の試合ごとの勝敗・スコア | 1871年〜現在 |
| [pybaseball](https://github.com/jldbc/pybaseball) 経由の Statcast (Baseball Savant) | 打球速度・飛距離・回転数等 | 直近2シーズンのみ取得 |

Lahman Baseball Database は、かつての配布元だった `chadwickbureau/baseballdatabank` リポジトリがGitHub上から削除されて以降、SABR (Society for American Baseball Research) が公式に引き継いでメンテナンスしています。本ツールはSABRが公開しているBox.com上のCSV版(`lahman_1871-2025_csv` フォルダ、2026年1月リリース、2025年シーズンまで収録)を取得元としています。このBox.comフォルダのページ上には明示的なライセンス表記が見当たりません(SABRサイト内のNegro Leaguesデータ部分のみSeamheads.comのライセンス表記があります)。そのため本ツールでの利用は個人利用・研究利用の範囲を前提としています。

Box.comの共有フォルダは公開APIを持たないJS製SPAのため、`ingest/lahman.py` は共有ページに埋め込まれたJSON(`Box.postStreamData`)を解析してファイル一覧を取得し、Boxの直リンクエンドポイント経由でCSVをダウンロードしています。Box側のページ構造が変わると取得に失敗する可能性があるため、その場合は旧ミラー(`xorq-labs/baseballdatabank`、2021年シーズンまでのデータ)に自動的にフォールバックします。

### ⚠️ データ完全性に関する重要な注意

**本ツールは1901年以降のMLB記録についてはほぼ全域をカバーできますが、19世紀(1871〜1900年)のデータはRetrosheet側の記録完全性に限定的な制約があり、この期間の「史上初」系の主張には注意が必要です。**

### テンプレート6〜8 (Statcast発展テンプレート) の実装メモ

- **バレル判定**: 独自の速度・角度ルールは実装せず、`statcast()` の生データに含まれるMLBAM側の分類列 `launch_speed_angle` をそのまま使用しています。取得済みデータを実際に集計して確認したところ、`launch_speed_angle = 6` の打球は打球速度97.5〜122.9mph(平均約105mph)・打球角度6〜47度(平均約26度)であり、これはBaseball Savantが公式に定義する「Barrel」の範囲と一致します(1=Weak, 2=Topped, 3=Under, 4=Flare/Burner, 5=Solid Contact, 6=Barrel)。Hard-Hit%は仕様通り打球速度95mph以上の単純な割合です。
- **重要なデータの癖**: Statcastの生データ(`statcast_pitches`)の `player_name` 列は**投手側**の名前であり、打者名ではありません(同一 `player_name` に対し `pitcher` 列は一定、`batter` 列だけが変化することで確認済み)。テンプレート6・7は打者ランキングのため、`batter` (MLBAM ID) を `player_id_lookup` テーブル(後述)と突き合わせて正しい打者名を取得しています。
- **期待成績(xBA/xSLG/xwOBA)**: いずれも生データの既存列(`estimated_ba_using_speedangle` / `estimated_slg_using_speedangle` / `estimated_woba_using_speedangle`)を使用。xBA/xSLGは打球イベント(K/四球/死球は対象外)、xwOBAはK・四球・死球も加味した打席単位の指標という、Baseball Savant側の設計差をそのまま踏襲しています。実成績との差分(wOBA基準)が±0.015を超える場合に「好調(幸運)」「不振(不運)」フラグを付与しています(閾値は `query/templates.py` の `luck_threshold` で調整可能)。
- **Sprint Speed**: 生データの `statcast_pitches` にはSprint Speed列が存在しないため、pybaseballの専用リーダーボード取得関数 `statcast_sprint_speed(year, min_opp)` を使用しています。これは投球単位ではなくシーズン単位の集計値のため、`ingest/statcast.py` では1シーズンにつき1回のみリクエストし(取得済みシーズンは再取得しません)、`data/raw/statcast/sprint_speed/` にキャッシュしています。
- **打者ID⇄名前の変換**: 上記の理由により、pybaseballの `chadwick_register()` (MLBAM ID⇄選手名の全選手クロスウォーク)を一度だけ取得し、`data/raw/statcast/player_id_lookup.parquet` にキャッシュ、DuckDBには `player_id_lookup` テーブルとしてロードしています。これは特定シーズンに紐づかない全選手台帳のため、シーズンごとの再取得は不要です。

## セットアップ

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Python 3.11以上が必要です。

## 使い方

### 1. データ取得 (初回のみ)

```bash
python ingest/lahman.py
python ingest/retrosheet_gamelogs.py
python ingest/statcast.py
```

いずれも `data/processed/mlb.duckdb` にデータを格納します。`data/raw/` にダウンロード済みファイルをキャッシュするため、**一度取得したデータは再ダウンロードしません**(Statcastの打球データは前回取得日からの差分のみ取得、Sprint Speedはシーズン単位、選手IDテーブルは全体で1回のみ取得します)。強制的に再取得したい場合は `--force` を付けてください。

`python cli.py` を初回実行した際にデータが未取得の場合も、自動的にingestスクリプトを実行するか確認されます。

### 2. CLI起動

```bash
python cli.py            # 日本語UI (デフォルト)
python cli.py --lang en  # 英語UI
```

## ディレクトリ構成

```
mlb-record-finder/
  data/raw/          ダウンロードした生データのキャッシュ (gitignore対象)
  data/processed/     mlb.duckdb (単一ファイルDB)
  ingest/
    lahman.py               Lahman Baseball Databaseの取得・取込
    retrosheet_gamelogs.py  Retrosheet Game Logsの取得・取込
    statcast.py              Statcast (pybaseball) の差分取得・取込 (打球データ/Sprint Speed/選手ID台帳)
  transform/
    schema.sql          取り込んだテーブルに対するビュー定義
  query/
    templates.py         8つの記録検索テンプレート (SQL/関数)
  i18n/
    ja.py / en.py         UI文言辞書
  cli.py                対話式CLI本体
  requirements.txt
```

## 技術スタック

Python 3.11+ / pybaseball / pandas / duckdb / requests / tabulate

## 多言語対応について

CLIのメニュー・プロンプト・エラーメッセージ・結果テーブルの列名などのUI文言はすべて `i18n/ja.py` と `i18n/en.py` の辞書経由で出し分けています。選手名・球団名などのデータ自体は原則として原語(英語)表記のままです。

## 既知の制約

- Lahmanデータは2025年シーズンまで(SABR公式版、上記参照)。
- Statcastは直近2シーズンのみ取得(全期間の取得はスコープ外)。
- チームの勝率ウィンドウ検索(テンプレート4)は、シーズンをまたぐ連続試合は対象外です。
- SABR版のLahmanデータには一部Negro Leaguesの球団・選手データも含まれています(例: New York Black Yankees)。球団名検索で複数候補が出た場合は番号で選択してください。
- テンプレート6・7(バレル率・期待成績)は打者側の指標のため、投手が打席に立った打球(まれなケース)は打者本人の成績として正しく集計されますが、逆に投手成績としての集計は行っていません。
