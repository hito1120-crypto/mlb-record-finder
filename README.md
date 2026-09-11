# mlb-record-finder

サラ・ラングス記者(MLB.com)のような、マニアックなMLB記録の発掘・検索ができるCLIツールです。
すべて無料・公開データのみを使用しています。

[English README](README.en.md)

## できること (Phase 1 / MVP)

`python cli.py` を実行すると、5つの定型記録テンプレートから番号を選び、閾値や年度などのパラメータを入力するだけで、DuckDBに対してSQLが実行され、結果が表形式で表示されます。

1. **打球速度・飛距離条件を満たす本塁打を検索** (Statcast) — 例: 打球速度115mph以上 かつ 飛距離470ft以上の本塁打一覧
2. **特定球団の通算本塁打ランキング** (Lahman) — 球団の歴史(移転・改名含む)を通算した選手別HRランキング
3. **あるシーズンでの2条件達成者一覧** (Lahman) — 例: 本塁打30本以上+盗塁20個以上を同一シーズンで達成した選手
4. **チームの任意N試合ウィンドウでの最高勝率記録** (Retrosheet) — 全チーム・全シーズンを対象に、連続N試合での最高勝率を検索
5. **あるシーズンでの3条件複合達成者検索** (Lahman) — 例: 三塁打+本塁打+盗塁の複合達成

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

いずれも `data/processed/mlb.duckdb` にデータを格納します。`data/raw/` にダウンロード済みファイルをキャッシュするため、**一度取得したデータは再ダウンロードしません**(Statcastは前回取得日からの差分のみ取得します)。強制的に再取得したい場合は `--force` を付けてください。

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
    statcast.py              Statcast (pybaseball) の差分取得・取込
  transform/
    schema.sql          取り込んだテーブルに対するビュー定義
  query/
    templates.py         5つの記録検索テンプレート (SQL/関数)
  i18n/
    ja.py / en.py         UI文言辞書
  cli.py                対話式CLI本体
  requirements.txt
```

## 技術スタック

Python 3.11+ / pybaseball / pandas / duckdb / requests / tabulate

## 多言語対応について

CLIのメニュー・プロンプト・エラーメッセージ・結果テーブルの列名などのUI文言はすべて `i18n/ja.py` と `i18n/en.py` の辞書経由で出し分けています。選手名・球団名などのデータ自体は原則として原語(英語)表記のままです。

## 既知の制約 (Phase 1)

- Lahmanデータは2025年シーズンまで(SABR公式版、上記参照)。
- Statcastは直近2シーズンのみ取得(全期間の取得はPhase 1のスコープ外)。
- チームの勝率ウィンドウ検索(テンプレート4)は、シーズンをまたぐ連続試合は対象外です。
- SABR版のLahmanデータには一部Negro Leaguesの球団・選手データも含まれています(例: New York Black Yankees)。球団名検索で複数候補が出た場合は番号で選択してください。
