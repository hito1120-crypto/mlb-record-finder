# mlb-record-finder

サラ・ラングス記者(MLB.com)のような、マニアックなMLB記録の発掘・検索ができるCLIツールです。
すべて無料・公開データのみを使用しています。

[English README](README.en.md)

データ出典・独自指標(WAR等)の注記・免責事項は [mlb-record-finder_disclosure_notes.md](mlb-record-finder_disclosure_notes.md) を参照してください。

## 目次

- [できること](#できること)
- [既存サービスとの違い](#既存サービスとの違い)
- [データソース](#データソース)
- [セットアップ](#セットアップ)
- [使い方](#使い方)
- [ディレクトリ構成](#ディレクトリ構成)
- [技術スタック](#技術スタック)
- [多言語対応について](#多言語対応について)
- [既知の制約](#既知の制約)

## できること

`python cli.py` を実行すると、14の定型記録テンプレートから番号を選び、閾値や年度などのパラメータを入力するだけで、DuckDBに対してSQLが実行され、結果が表形式で表示されます。

1. **打球速度・飛距離条件を満たす本塁打を検索** (Statcast) — 例: 打球速度115mph以上 かつ 飛距離470ft以上の本塁打一覧
2. **特定球団の通算本塁打ランキング** (Lahman) — 球団の歴史(移転・改名含む)を通算した選手別HRランキング
3. **あるシーズンでの2条件達成者一覧** (Lahman) — 例: 本塁打30本以上+盗塁20個以上を同一シーズンで達成した選手
4. **チームの任意N試合ウィンドウでの最高勝率記録** (Retrosheet) — 全チーム・全シーズンを対象に、連続N試合での最高勝率を検索
5. **あるシーズンでの3条件複合達成者検索** (Lahman) — 例: 三塁打+本塁打+盗塁の複合達成
6. **バレル率・Hard-Hit%ランキング** (Statcast) — 最低打球イベント数以上の選手を対象に、指定シーズンのバレル率・Hard-Hit%(打球速度95mph以上の割合)をランキング表示
7. **期待成績(xwOBA/xBA/xSLG)ランキングと実成績との乖離** (Statcast) — 指定シーズンの期待成績上位選手を、実成績(wOBA/BA/SLG)との差分・好不調フラグ付きで表示
8. **Sprint Speedランキング** (Statcast) — 指定シーズンの走力(Sprint Speed)上位選手を表示
9. **先頭打者本塁打+サヨナラ本塁打の試合検索** (Retrosheet Play-by-Play) — その試合の最初の打席が本塁打、かつ試合そのものもサヨナラ本塁打で終わった試合を検索
10. **レバレッジ指数が最も高かった打席検索** (Retrosheet Play-by-Play) — 各打席が本拠地チームの勝利確率に与えた変化量(レバレッジ指数)でランキング。期間・打者名で絞り込み可能
11. **バットスピード・スイング長ランキング** (Statcast) — 指定シーズンの平均/最高バットスピード・平均スイング長を選手別にランキング(最低スイング数で絞り込み可能)
12. **OAA (Outs Above Average) 守備ランキング** (Statcast) — 指定シーズン・守備位置ごとのOAA(平均的な野手と比べたアウト創出数)・守備による失点抑止をランキング(捕手は対象外)
13. **アームアングル(投球時の腕の角度)ランキング** (Statcast) — 指定シーズンの投手別の平均アームアングルをランキング(最低投球数で絞り込み可能)
14. **シーズンWARランキング** (Lahman/Statcast/Retrosheet, 2015〜2026年シーズンのみ) — ⚠️ **本ツール独自の簡易算出WARです。公式のFanGraphs (fWAR) やBaseball-Reference (bWAR) の数値とは一致しません**(詳細は後述)

## 既存サービスとの違い

MLB選手・記録の検索サービスとしては、Baseball-Reference.comの有料サービス「Stathead」や、Baseball Savant公式の「Statcast Search」など、画面上でフィルター項目を選んで検索する本格的なツールが既に存在します。
本ツールの違いは、日本語または英語の自然文で質問するとAIが自動でSQLクエリを生成し、生成されたSQLも表示しながら結果を返す自由質問モードを備えている点です。また、WAR・勝利確率・レバレッジ指数・Statcast詳細指標(OAA・バットスピード・投球腕角度等)までを1つのローカルDBにまとめ、無料・無登録で使えるようにしています。

## データソース

すべて無料・公開データです。

| ソース | 内容 | 収録期間 |
|---|---|---|
| [Lahman Baseball Database (SABR公式版)](https://sabr.box.com/s/y1prhc795jk8zvmelfd3jq7tl389y6cd) | シーズン・通算成績(打撃・投球・守備・受賞歴) | 1871年〜2025年シーズン |
| [Retrosheet Game Logs](https://www.retrosheet.org/gamelogs/index.html) | チーム単位の試合ごとの勝敗・スコア | 1871年〜現在 |
| [pybaseball](https://github.com/jldbc/pybaseball) 経由の Statcast (Baseball Savant) | 打球速度・飛距離・回転数等 | 2015年シーズン以降(Statcast全球追跡開始以降) |
| [Retrosheet Play-by-Play (イベントファイル)](https://www.retrosheet.org/events/index.html) | 打席単位の走者状況・スコア・プレー結果 | デフォルトで直近5シーズンのみ取得。Retrosheet側のPlay-by-Play記録自体、1920年代以前は部分的(後述) |

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

### テンプレート9〜10 (Retrosheet Play-by-Play発展テンプレート) の実装メモ

- **Play-by-Playの解析**: RetrosheetのPlay-by-Playは、そのままクエリできる表ではなく独自の圧縮記法で書かれたイベントファイルとして配布されています。`ingest/_playbyplay_parser.py` は、Cツールチェーン(Chadwickの `cwevent`)に依存しないフルスクラッチのPythonパーサーで、打席・走塁イベントごとに前後の走者状況とアウトカウント、得点を再構築します。この最終スコアの再構築結果は、取り込み済みの全試合についてRetrosheetのGame Logsと突き合わせて検証しており、本稿執筆時点で2025年シーズンの2,430試合中2,417試合(99.5%)が完全一致、残りの少数は単一の系統的バグというより個別の不整合に起因すると見られます。
- **得点期待値表 (Run Expectancy Matrix)** (`transform/schema.sql` の `v_run_expectancy`): 走者状況8パターン×アウトカウント3パターン(計24通り)ごとに、そのイニングの残りで平均何点入ったかを、取り込み済みのPlay-by-Playデータから直接算出しています。値は公表されているMLBの得点期待値表とよく一致しており(例: 走者なし0アウトで約0.50点、満塁0アウトで約2.4点、走者なし2アウトで約0.10点)、Play-by-Playパイプライン全体の妥当性を確認する主な手がかりになっています。
- **勝利確率** (`v_win_probability`): 1シーズン分のPlay-by-Playデータだけでは、イニング×得点差×アウト×走者状況で分割した実績ベースの勝利確率表を作るには標本数が足りないため、得点期待値表を使った近似モデルで計算しています。具体的には、現在の得点差に得点期待値ベースの残り試合の得点予測を加えた「予測最終得点差」を、ロジスティック関数で確率に変換する方式です。本拠地チームの利(ホームアドバンテージ)は考慮しておらず、延長回は9回の延長として扱う簡略化があるため、正確なスポーツブック水準の数値としてではなく、あくまで傾向を示す値として扱ってください。例外的に厳密なのは試合の最後のプレーで、実際の勝者に基づいて必ず勝利確率1.0/0.0に確定させているため、サヨナラプレーのレバレッジは正しく反映されます。
- **レバレッジ指数** (`v_play_leverage`、テンプレート10で使用): 各打席のレバレッジは、その状況で起こり得たすべての結果を平均する教科書的な定義ではなく、実際に記録された結果による勝利確率の前後差(打席前後のスイング幅)をそのまま使っています。

### テンプレート11〜13 (Statcastバットトラッキング・守備・アームアングル発展テンプレート) の実装メモ

- **バットスピード・スイング長**: `bat_speed` / `swing_length` は既存の `statcast_pitches` の列としてそのまま取得されており、新規の取得ステップは不要でした(Baseball Savantが同じ投球単位CSVにこれらの列を追加したため、`ingest/statcast.py` を再実行するだけで取り込まれます)。両列はスイングが発生した投球でのみ非NULLになる(サンプル調査では全投球の約46%)ため、`min_swings` はスイング数そのものを基準にしています。
- **OAA (Outs Above Average)**: 投球単位データには存在しない、Baseball Savant公式のシーズン集計リーダーボード(`pybaseball.statcast_outs_above_average(year, pos, min_att, view="Fielder")`)を使用しています。**同じ選手でも問い合わせた守備位置によってOAAの値が変わる**(例: 内野全体で守備をこなす選手を`2B`で見た場合と`IF`集計で見た場合とで別の数値になる)ため、シーズン×守備位置の組み合わせごとに個別リクエスト・キャッシュしており(`data/raw/statcast/oaa/`)、DuckDBの `statcast_oaa` テーブルでは問い合わせた守備位置を `pos` 列として保持しています。取得対象の守備位置は 1B/2B/3B/SS/LF/CF/RF の実守備位置7種類に加え、Savant側の集計バケットである IF/OF/ALL(捕手を除く全体)です。捕手はこのリーダーボード自体が非対応のため対象外です。また、このリーダーボードのCSVには機会数(attempts)の列が無く、Sprint Speedの `min_opp` のようにクエリ時に変更できないため、取得時点でBaseball Savant側の「qualified」基準(`min_att="q"`)に固定しています。方向別内訳(前方/後方/左右)・打者の左右別内訳の列は `statcast_oaa` テーブルには取り込んでいますが、テンプレート12のランキング表示では主要指標(OAA・守備による失点抑止・捕球成功率)のみを表示しています。
- **アームアングル**: `arm_angle` も `bat_speed` 同様、`statcast_pitches` に既存の列としてそのまま追加されていました。ただしバットスピードとは性質が異なり、スイングの有無ではなく投手のリリースポイントに基づく指標のため、**ほぼ全投球(取り込み済みデータで確認したところ約94%)で非NULL**です。そのため `min_pitches` はスイング数ではなく投球数そのものを基準にしています。また、テンプレート6・7の打者ランキングとは異なり `player_id_lookup` との突き合わせは不要です。`statcast_pitches.player_name` はそもそも投手側の名前であるため(前述のテンプレート6〜8の実装メモ参照)、そのまま投手名として使えます。シーズン内でも同一投手のアームアングルは完全に一定ではなく(標本調査では標準偏差でおおよそ3〜5度程度のばらつき)、このランキングはシーズン平均値を表示しています。

### テンプレート14 (シーズンWARランキング) の実装メモ

#### ⚠️ 本ツールのWARは独自の簡易算出であり、公式のfWAR/bWARとは一致しません

**本テンプレートが算出するWARは、公開されているwOBA/FIPベースのサーベルメトリクス手法を参考にした本ツール独自の簡易実装です。FanGraphs (fWAR) やBaseball-Reference (bWAR) の公式アルゴリズムを再現したものではなく、両サイトの数値と一致することは意図していません。** CLI上でもテンプレート14を実行するたびに同じ注記を表示しています。

- **対応シーズン**: 2015〜2026年のみ。wOBA/FIPの年度別係数はFanGraphsの公開Guts!ページ(`https://www.fangraphs.com/guts.aspx?type=cn`)から2015〜2026年分のみを転記して `transform/war_constants.py` にテーブル化しており、Lahmanのように1871年まで遡ることはできません(古い年代の係数を正確に転記するには別途慎重な検証が必要なため、今回は対象外としています)。対応外の年を指定するとエラーメッセージを表示します。
- **打撃価値 (wRAA)**: 標準的なwOBA式(分母はAB+故意四球を除くBB+犠飛+死球)で算出したwOBAと、その年のリーグ平均wOBAとの差からWeighted Runs Above Averageを算出しています。
- **走塁価値**: 盗塁企図(SB/CS)の得点価値のみの簡易版です。進塁や併殺回避などを含む本格的なBaserunning Runs (BsR)は対象外です。
- **守備価値**: 2016年以降はStatcastの公式リーダーボード(`statcast_oaa.fielding_runs_prevented`、OAAを既にラン換算済みの値)をそのまま採用しています。**2015年はOAAデータが存在しないため、守備価値は一律0として扱っています**(Lahmanの守備成績(補殺・刺殺・失策等)からの簡易推定も検討しましたが、OAAが前提とする打球コース別の捕球確率モデルに相当する情報がなく、信頼できる代替指標を作れないと判断したため0扱いとしました)。
- **投手価値 (FIPベース)**: `(13×HR + 3×(BB+HBP) - 2×K) / IP + cFIP` で算出したFIPと、同じ方法でリーグ全体から算出したリーグ平均FIPとの差を、投球回に応じてRuns Above Averageに換算しています。
- **パークファクター**: 独自の新規ロジックとして、Retrosheet Game Logsから「本拠地の(得点+失点)/試合 ÷ ロードの(得点+失点)/試合」という単年の簡易パークファクターを算出しています(`query/park_factor.py`)。1シーズン分・無補正の値のため標本ノイズが大きく、標準的な手法に倣って中立の1.0側に半分寄せた値(`(raw+1)/2`)を、選手のシーズン成績全体に適用しています。Lahman公式のBPF(複数年補正済み)との相関は2024年シーズンで約0.73であり、方向性は一致するものの完全には一致しません。移籍選手はチームごとの出場機会で加重平均しています。
- **リプレースメントレベル・ポジション補正**: 「勝率.294相当」という公開されている標準的な定義を採用し、その年の実際の平均試合数(2020年の60試合シーズンなども自動反映)とR/W(1勝あたりの得点換算値)から、リーグ全体のリプレースメント分の得点プールを算出しています。このプールを打撃側・投手側に独自に50/50で配分しています(FanGraphs公式は実績ベースの約57/43分割かつ先発/救援で異なるリプレースメントレベルを使用しており、本ツールはそれを再現していません)。ポジション補正はFanGraphsが公開している標準値(600打席あたりの補正ラン数)を採用し、`lahman_appearances` から選手のシーズン最多出場ポジションを判定して適用しています。
- **二刀流選手の扱い**: 大谷翔平選手のような二刀流選手は、打撃側の価値と投手側の価値を単純に合算しています。ただし打撃側のポジション補正はDH出場分をそのまま「本業DH」と同様に減点しており、投手として別途価値を生んでいる分を考慮していないため、二刀流選手は実際より低めのWARになりやすい点に注意してください(実データ検証では大谷選手のWARが公式値より低めに出る傾向を確認しています)。

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
python ingest/retrosheet_playbyplay.py
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
    lahman.py                  Lahman Baseball Databaseの取得・取込
    retrosheet_gamelogs.py     Retrosheet Game Logsの取得・取込
    statcast.py                 Statcast (pybaseball) の差分取得・取込 (打球データ/Sprint Speed/OAA/選手ID台帳)
    retrosheet_playbyplay.py   Retrosheet Play-by-Playイベントファイルの取得・取込
    _playbyplay_parser.py      イベントファイル記法のフルスクラッチパーサー
  transform/
    schema.sql          取り込んだテーブルに対するビュー定義 (得点期待値・勝利確率・レバレッジ含む)
    war_constants.py     WAR算出用のwOBA/FIP年度別係数テーブル (2015〜2026年、独自簡易算出)
  query/
    templates.py         14の記録検索テンプレート (SQL/関数)
    war.py                独自簡易WARの算出ロジック (テンプレート14)
    park_factor.py        Retrosheet Game Logsからの単年簡易パークファクター算出
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
- Statcastは2015年シーズン以降を対象に取得(シーズン単位で順次バックフィル中。取得済み範囲は `data/raw/statcast/manifest.json` を参照)。
- チームの勝率ウィンドウ検索(テンプレート4)は、シーズンをまたぐ連続試合は対象外です。
- SABR版のLahmanデータには一部Negro Leaguesの球団・選手データも含まれています(例: New York Black Yankees)。球団名検索で複数候補が出た場合は番号で選択してください。
- テンプレート6・7(バレル率・期待成績)は打者側の指標のため、投手が打席に立った打球(まれなケース)は打者本人の成績として正しく集計されますが、逆に投手成績としての集計は行っていません。
- テンプレート9・10の基盤となるRetrosheet Play-by-Play(イベントファイル)データは、デフォルトで直近5シーズンのみ取得対象です。また、Retrosheet側のPlay-by-Play記録自体、1920年代以前は部分的にしか整備されていません(1871年まで遡れるテンプレート4のGame Logsより厳しい制約です)。
- テンプレート9・10の勝利確率・レバレッジ指数は実績ベースの統計モデルではなく近似計算です。詳細は上記の実装メモを参照してください。
- テンプレート12(OAA)は捕手が対象外です(Baseball Savant側のリーダーボード自体が捕手に非対応)。また機会数の閾値はSavantの「qualified」基準に固定されており、テンプレート8のSprint Speedのようにクエリ時に変更することはできません。
- テンプレート13(アームアングル)はシーズン平均値のランキングです。球種ごとの傾向(例: 同じ投手でもスライダーとフォーシームでアームアングルが数度変わるケース)までは区別していません。
- **テンプレート14(シーズンWARランキング)は本ツール独自の簡易算出であり、公式のFanGraphs (fWAR) / Baseball-Reference (bWAR) とは一致しません。** 対応シーズンも2015〜2026年のみです。詳細な簡略化点は上記の実装メモを参照してください。
