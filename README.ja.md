[English](README.md)

英語版は [README.md](README.md) です。エージェントの効果と承認、MCP、ブラウザの playground を先頭に置いています。以下は日本語の README です。

# Etsah

**AIが書き、AIが検証し、人間は自然言語とGUIで読む。**

Etsahは、AIによるソフトウェア開発のために設計した言語の試作です。人間の言語を置き換えるのではなく、**AIが仕様を書き、別のAIが契約を検査し、人間が結果を理解する**ための共通の形を目指しています。

Ryuji Hashimoto (J-PROUT Inc.)

## prout からの改名

言語名は Etsah です。一つマイナー版のあいだ、旧コマンド `prout`、`proutc`、`prout-mcp` は動き、stderr に `新名称は etsah` と出します。`.prout` ファイルも検査できます。`import prout` などの旧モジュール名は、新しいモジュールを再エクスポートする薄い互換層です。`ETSAH_*` が無いとき、対応する `PROUT_*` 環境変数も読みます。`prout1.` で始まる承認トークンも検証できます。リポジトリの URL は https://github.com/RyujiHashimoto/etsah です。

## 出発点

AIが開発するなら、人間が作った言語の制約から自由になれるはずです。しかし、あらゆる言語の厳密な上位互換は作れません。意味論も構文もランタイムも衝突するからです。

そこでEtsahは、実装言語を置き換えず、その上に**契約と効果の層**をかぶせます。`impl` はPythonでもRustでもSQLでもよい。Etsahが保証するのは「何を読み、何を書き、何を約束するか」です。

その上に、もう一つの層を置きました。**Etsah Patterns** です。人間が欲しがるWebアプリの形は、驚くほど限られたパターンの組み合わせでできています。認証、一覧、作成、検索、決済、返金、通知、アップロード、承認。ならばパターン自体を言語の第一級要素にし、AIがコードを書く代わりにパターンとスロットを書くようにすれば、開発は速くなります。

## 二つの層

| 層 | ファイル | 役割 |
|---|---|---|
| 契約言語 Etsah | `etsah.py`, `SPEC.md` | 型・効果・事前条件・ポリシーを検査し、ASTハッシュと人間向けGUIを生成する |
| パターン言語 Etsah Patterns | `patterns/etsahc.py`, `patterns/registry/` | パターンをJSONで定義し、仕様のスロットを埋めて数百行を決定論的に展開する |

パターンはコードではなくデータです。だからAIは、コードを読まずに `registry/index.json` から目的のパターンを発見し、マニフェストの `intent` と `slots` だけを読んで使えます。

## 動かす

Python 3.12 と標準ライブラリで動きます。追加の実行時パッケージはありません。PyPI には公開していないので、クローンをインストールします。

```bash
git clone https://github.com/RyujiHashimoto/etsah.git
cd etsah
python3 -m venv .venv
.venv/bin/python -m pip install .
```

Debian と Ubuntu では、`python3 -m venv` に `python3.12-venv` パッケージが要ります。Windows ではプログラムの場所が `.venv\Scripts\python` と `.venv\Scripts\etsah`（同様に `etsahc` と `etsah-mcp`）です。

インストールすると `etsah`、`etsahc`、`etsah-mcp` がその環境に入ります。Windows と書いてある箇所以外は、Linux と macOS のパスです。クローンのディレクトリで実行してください。初回のインストールは次の行で終わります。

```text
Successfully installed prout-lang-0.1.0
```

`pip install .` は、ビルド用の setuptools を一時環境へ取得することがあります。インストール後のプログラムに、第三者の依存はありません。

### 最初の検査

`examples/refund_fixed.etsah` は、架空の注文を返金する契約の修正版です。チェッカーはソースを読むだけで、決済 API は呼びません。

```bash
.venv/bin/etsah check examples/refund_fixed.etsah
```

```text
Etsah check: examples/refund_fixed.etsah
AST hash: bae8501e8b3f
[WARNING] W-PRE (line 45): precondition of refund needs a runtime guard: (order.status == "paid")

0 error(s), 1 warning(s)
```

終了コードは 0 です。警告は、実行時に確認する条件です。表示は英語です。

![人間向けビュー](docs/human-view.png)

この図は、同じ返金契約の人間向けビューです（`docs/human-view.png`）。

### エージェントの方針

`examples/agents/support_reply.etsah` は架空のサポート窓口です（Harbor Lane Support、チケット T-1042）。タスクはチケットを読めます。顧客へのメールは外部の `emit(mail)` で、タスクは `gate approve_reply` を指名します。チェッカーはソースを読むだけで、メールは送りません。

```bash
.venv/bin/etsah check examples/agents/support_reply.etsah
```

```text
Etsah check: examples/agents/support_reply.etsah
AST hash: 270d77fe2df0

1 task(s):
  draft_reply — Read a ticket and send the reply through the approval gate
    agent: support_reply
    trigger: webhook "support-tickets"
    effects: read(tickets), emit(mail)
    gate: approve_reply, approver support_lead, before emit(mail), on_approved mail.send_reply
    retry: 2, escalate support_lead
    key: ticket_id
    budget: agent run 0 / period 0 yen
    limits: net 0/hour, emit 20/hour
audit id: 270d77fe2df04db5def3664c3a29f2a7a37e730661277ce2a4243f6b629ef3f0 (display 270d77fe2df0)
OK — no issues

0 error(s), 0 warning(s)
```

終了コードは 0 です。

`check --json` はオブジェクトを 1 つ出します。`diagnostics` は空で、`manifest.signature` は `null` です。

```bash
.venv/bin/etsah check --json examples/agents/support_reply.etsah
```

```json
{
  "hash": "270d77fe2df0",
  "diagnostics": [],
  "audit_id": "270d77fe2df04db5def3664c3a29f2a7a37e730661277ce2a4243f6b629ef3f0",
  "plan": [
    "1 task(s):",
    "  draft_reply — Read a ticket and send the reply through the approval gate",
    "    agent: support_reply",
    "    trigger: webhook \"support-tickets\"",
    "    effects: read(tickets), emit(mail)",
    "    gate: approve_reply, approver support_lead, before emit(mail), on_approved mail.send_reply",
    "    retry: 2, escalate support_lead",
    "    key: ticket_id",
    "    budget: agent run 0 / period 0 yen",
    "    limits: net 0/hour, emit 20/hour",
    "audit id: 270d77fe2df04db5def3664c3a29f2a7a37e730661277ce2a4243f6b629ef3f0 (display 270d77fe2df0)"
  ],
  "manifest": {
    "hash": "270d77fe2df0",
    "audit_id": "270d77fe2df04db5def3664c3a29f2a7a37e730661277ce2a4243f6b629ef3f0",
    "agents": [
      {
        "name": "support_reply",
        "allow": [
          [
            "emit",
            "mail"
          ],
          [
            "read",
            "tickets"
          ]
        ],
        "budget_run": 0,
        "budget_period": 0,
        "limit_net": 0,
        "limit_emit": 20,
        "gates": [
          "approve_reply"
        ]
      }
    ],
    "signature": null
  }
}
```

終了コードは 0 です。

`examples/agents/support_reply_broken.etsah` は、同じ窓口でゲートをタスクから外したものです。検査の終了コードは 1 です。診断の行は次のとおりです。

```text
[ERROR] E-POLICY (line 35): task draft_reply: emit to an external party has no approval gate (emit(mail))
```

### Windows

CI は `ubuntu-latest` だけです。以下の Windows の行は、上の Linux のコマンドをパスだけ置き換えたものです。実行結果の記録ではありません。

コマンドプロンプト:

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\etsah check examples/refund_fixed.etsah
.venv\Scripts\etsah check examples/agents/support_reply.etsah
.venv\Scripts\etsah check --json examples/agents/support_reply.etsah
```

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install .
.\.venv\Scripts\etsah check examples/refund_fixed.etsah
.\.venv\Scripts\etsah check examples/agents/support_reply.etsah
.\.venv\Scripts\etsah check --json examples/agents/support_reply.etsah
```

### テスト

```bash
.venv/bin/python -m unittest discover -s tests
```

単体テストは標準ライブラリの `unittest` だけを使います。成功すると最後の行が `OK` になり、終了コードは 0 です。

`tests/` はパッケージではないので、`tests.` で始めると `support` を読み込めず、クラス単位は次のコマンドです。

```bash
PYTHONPATH=tests python3 -m unittest test_checker.TestCli
```

### パターンと MCP サーバ

```bash
.venv/bin/etsahc build patterns/examples/expense.etsah --out patterns/build/expense
```

```text
Etsah Patterns: expense
stack: nextjs, postgres  freedom: guided
entities: 2  patterns: 3  custom: 1
spec lines: 28  generated lines: 1,264  expansion: 45.1x
information flow: skipped (no labels declared)
OK — no issues
wrote patterns/build/expense/report.html
wrote patterns/build/expense/demo.html
wrote patterns/build/expense/manifest.json
wrote patterns/build/expense/security.json
```

28 行の経費仕様を展開し、`patterns/build/expense/demo.html` を書きます。ブラウザでそのファイルを開いてください。サーバは要りません。申請、承認、差し戻し、却下を操作できます。承認された処理は、承認の後にだけ動きます。`patterns/build/` は gitignore です。

```bash
.venv/bin/etsah-mcp --help
```

```text
usage: etsah-mcp [mcp]
       etsah-mcp guard --contract FILE [--agent NAME] [--allow-net HOST] [--deny-net HOST] [--approval-key-file FILE] [--nonce-file FILE] [--approval-token TOKEN] [--allow-legacy-approval-token] -- UPSTREAM...
       etsah-mcp approve --contract FILE --gate NAME --op NAME --approver ID (--args JSON | --args-file FILE) (--ttl SECONDS | --expires-at UNIX)
```

引数なしの `etsah-mcp` は stdio のサーバです。改行区切りの JSON-RPC を読み、標準入力が閉じると戻ります。

英語の 5 分ガイドは [docs/en/agents-quickstart.md](docs/en/agents-quickstart.md)、比較は [docs/en/comparison.md](docs/en/comparison.md) です。

## プルリクエストの検査

リポジトリ直下の composite action は、プルリクエストで変わった `.etsah` ファイルを検査し、診断を GitHub の注釈（`::error file=...,line=...::`）として出します。エラーがあるとジョブは失敗します。警告では失敗しません。Python の標準ライブラリだけで動きます。タグは未公開です。`@main` を指定してください。

```yaml
on:
  pull_request:

jobs:
  etsah:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: RyujiHashimoto/etsah@main
```

`glob` で対象を変えられます。`scope` は `pull-request`（既定）または `all` です。プルリクエスト以外のイベントでは、既定の scope は一致したファイルをすべて検査します。`allow-net` と `deny-net` はカンマ区切りのホスト名で、チェッカーへ渡します。このリポジトリの CI は `uses: ./` で、合格する例と壊した例の両方を動かします。

## 承認前後をGUIで確かめる

`etsahc.py build` は `report.html` に加えて `demo.html` を生成します。承認パターンを含む仕様なら、申請→承認→差し戻しをブラウザで操作でき、承認後にだけ `on_approved` の処理が動くことを確認できます。

```bash
cd patterns
python3 etsahc.py build examples/expense.etsah --out build/expense
open build/expense/demo.html
```

![承認デモ](docs/demo.png)

同じ `approval` から、備品購入申請のデモも生成できます。

![備品購入申請の承認デモ](docs/demo-equipment.png)

## フックの引数まで検査する

`approval` の `on_approved` は、適用済みの `notify` を指せます。このとき `notify` の `channel` と `recipient` がフックの引数として検査されます。

```etsah
pattern notify
  channel: email
  recipient: accounting
  template: travel_approved

pattern approval TravelRequest
  entity: TravelRequest
  approver: manager
  on_approved: notify
```

`recipient` が `auth.roles` に含まれない場合や、`channel` が `email` / `slack` 以外の場合は `E-HOOK-ARG` で止まります。`notify` を適用していなければ `E-HOOK`、同じ `notify` を複数回適用して参照先が曖昧な場合も `E-HOOK` です。custom ブロックを指すフックは従来どおり使えます。

## パターンで書く

```bash
cd patterns

# 38行の仕様から、1,581行相当のファイル構成を展開する
python3 etsahc.py build examples/shop.etsah

# 日本語でパターンを検索する
python3 etsahc.py find "申請 承認"

# 経費精算を、承認デモ付きで組む
python3 etsahc.py build examples/expense.etsah --out build/expense

# 出張申請を、承認→経理通知のデモ付きで組む
python3 etsahc.py build examples/travel_request.etsah --out build/travel

# PC貸出は、承認パターンを二つのアプリから抽出するための利用例
python3 etsahc.py build bench/pc_lending.etsah --out build/pc_lending

# パターン追加前後を比較する
python3 etsahc.py bench

# 人材派遣の基幹（勤怠承認、抵触日、請求単価）。設計は docs/staffing.md
python3 etsahc.py build examples/staffing.etsah --out build/staffing
```

PC貸出は、承認パターンを二つのアプリから抽出するために使った二つ目の利用例です。製品デモとして育てる予定はなく、再利用が効いた証拠としてベンチに残しています。

`find "申請 承認"` は `approval` を score 15 で返します。元の会話を知らないAIでも、この索引とマニフェストだけで「申請→承認→差し戻し」を再利用できます。人材派遣の基幹に足した `timesheet`、`overtime_agreement`、`dispatch_contract`、`staffing_billing`、`matching` は `docs/staffing.md` にまとめています。

### パターン追加前後

| ケース | 仕様 | custom | AIが書く行数 | 生成行数 | 再利用倍率 |
|---|---:|---:|---:|---:|---:|
| 経費精算（approval追加前） | 24 | 180 | 204 | 981 | 4.8x |
| 経費精算（approval追加後） | 28 | 30 | **58** | 1,264 | **21.8x** |
| 出張申請（approval再利用） | 33 | 0 | **33** | 1,386 | **42.0x** |
| 備品購入申請（approval再利用） | 34 | 0 | **34** | 1,394 | **41.0x** |
| PC貸出（approval再利用） | 28 | 35 | **63** | 1,269 | **20.1x** |

同じ「申請→承認→差し戻し」を二度目に書くとき、AIの記述量は204行から58行へ **72%減**、再利用倍率は4.8倍から21.8倍へ改善しました。パターンが言語に追加されたことで、次の開発が短くなったことを示す最初の測定です。出張申請と備品購入申請は、承認後の通知を `custom` から標準の `notify` パターンに移したため、custom 0行・42.0倍/41.0倍になっています。

## 二回の追試：会話を知らないAIに渡す

2026年11月22日、このリポジトリのURLと依頼だけを、開発経緯を知らないAIに渡しました。`approval` という名前は教えていません。

| 回 | 依頼 | 時間 | 納品ビルド | 失敗 | 主な発見 |
|---|---|---:|---:|---:|---|
| 1 | 出張申請を作って。上長が承認したら経理に通知して | 8分13秒 | 3回 | 0 | GUIがなく、フックも未検証 |
| 2 | 備品購入申請を作って。上長が承認したら総務に通知して | **5分48秒** | 2回 | 0 | 標準の `demo.html` で承認前後を確認 |

一回目、AIは `python3 etsahc.py find "出張 申請 承認 通知"` を実行し、`approval` を score 12（当時の採点。現在はスロット説明も採点するため15）で自力発見しました。当時の仕様は36行、生成物は1,426行相当、再利用倍率18.8倍でした。しかし標準の `build` は `report.html` と `manifest.json` しか出さず、承認前後をGUIで確かめる手段がありませんでした。`on_approved` の参照先も検証されず、未知のスロットも黙って無視されました。

この追試を受けて、`demo.html`、状態遷移のデータ、`on_approved` の検査、未知スロットの警告、`notify` の `recipient` を追加しました。二回目は、出張申請の例をそのままコピーできない「備品購入申請」に変えて試しています。結果は **5分48秒**、納品ビルド2回で失敗0。AIは `demo.html` を標準機能で生成し、Playwrightで承認前・承認後・差し戻し・却下まで確認しました。一回目より **29%短く**、修正も1回減っています。現在の出張申請の例は、当時の custom 40行を標準の `notify` パターンに置き換え、33行・42.0倍に整理しています。

`on_approved` が標準の `notify` を指す場合は、`channel` の許可値と `recipient` が `auth.roles` に含まれるかを `E-HOOK-ARG` で検査します。残る穴は、custom ブロックを指すフックの引数まで検証しないことです。次は custom 側にも引数宣言を持たせます。

## 言語が育つループ

Etsahの成長は、コードの行数ではなく、**次のAIが少ない手順で解けるようになったか**で測ります。

1. 観察 — 二つのアプリに同じ形が現れる。
2. 共通部分を抽出する — 経費精算とPC貸出から始め、出張申請と備品購入で再確認する。
3. スロットで一般化する — `entity`, `approver`, `on_approved`。
4. レジストリに公開する — JSONマニフェストと索引。
5. 別のAIが発見する — `index.json` と `find`。
6. 使用する — 必須スロットを埋め、`on_approved` でアプリ固有の処理を接続する。
7. 改良を提案する — Pull RequestとCI。

![言語が育つループ](docs/growth.png)

`patterns/growth_report.py` は、この一周をHTMLレポートにします。ベンチ結果とレジストリから自動生成されるので、パターンを追加するたびに数値が更新されます。

## 現在の範囲

`build` が生成するのは、成果物の一覧と行数見積もりを含む `report.html`、操作できる `demo.html`、`manifest.json` です。APIやDBの実ソースはまだ書き出していません。生成行数はパターン定義からの見積もりで、実際にコンパイルされたコードの行数ではありません。

次の課題は、`approval` の7成果物を実際のファイルとして書き出し、テストを通すことです。現時点のEtsahは、パターンの発見・適用・検証・人間向け表示までを試作した段階です。

## これは「全言語の上位互換」ではない

意味論・構文・ランタイムが衝突するため、厳密な上位互換は作れません。Etsahは実装言語を置き換えず、その上に契約と効果の層をかぶせるメタ言語です。

リバースエンジニアリングへの含意は `SPEC.md` に書きました。契約が存在し、検証されている場合に限り、意味の復元は契約の照合に変わります。契約のないバイナリの解析は、依然として難しいままです。

## リポジトリ構成

```
action.yml               プルリクエスト検査の composite action
scripts/etsah_action.py  その action が注釈を出すスクリプト
etsah.py                 契約言語のパーサー・検証器・CLI
etsah_api.py             check_source と check_file
etsah_import.py          MCP と OpenAI のツール定義を op にする
etsah_mcp/               MCP サーバ（etsah mcp）とガード（etsah guard）
render.py                ASTから人間向けHTMLを生成
SPEC.md                  契約言語の仕様と設計原則
examples/                壊れた初稿、修正版、examples/agents のエージェント契約、情報フローの安全例・漏洩例
docs/playground/         ブラウザの検査（静的ページ。Pyodide）
docs/en/comparison.md    契約・効果・ポリシー・ガードレールとの比較
docs/check-demo.svg      etsah check の前後を録った SVG
tests/                   unittest の単体テスト（`python3 -m unittest discover -s tests -v`）
patterns/
  etsahc.py              パターンコンパイラ（build / find / add / index / bench）
  PATTERNS.md            パターン言語の設計思想
  registry/              27個のパターンマニフェストと index.json
  examples/              ショップ、経費、出張、備品、修理受付、サポート、ゲームブック、人材派遣、AIタスクの仕様
  bench/                 パターン追加前後の比較（PC貸出を含む）
  build/                 生成物（report.html、demo.html、story.html）
  growth_report.py       成長ループのHTMLレポート
.github/workflows/ci.yml 単体テスト、契約検査、索引整合、ビルド、ベンチ、回帰ガード
CONTRIBUTING.md          パターン提案とレビューの基準
```

## ゲームブック・分岐教材を作る

`pattern scene 場面ID` に見出し `title`、本文 `body`、選択肢 `choices` を指定します。最初の宣言が開始場面です。各選択肢は「表示名・移動先」の組で、選択肢のない場面からは最初に戻れます。ビルド後、出力先の `story.html` をブラウザで開いてください。単体でオフラインでも動きます。

```sh
cd patterns
python3 etsahc.py build examples/moon_garden.etsah --out build/garden
python3 etsahc.py build examples/fraction_quiz.etsah --out build/quiz
```

## 修理受付・顧客サポートをパターンで組む

`repair_desk` と `support_desk` の2例から、担当割当、作業キュー、期限、チェックリスト、コメント、ラベル、操作履歴、CSV出力、アーカイブ、評価の10パターンを抽出しました。どちらの仕様も52行で、業務固有の記述をパターンの適用に置き換えています。

```sh
cd patterns
python3 etsahc.py build examples/repair_desk.etsah --out build/repair
python3 etsahc.py build examples/support_desk.etsah --out build/support
```

## 人材派遣の勤怠・契約・単価を検査する

`timesheet`（勤怠と派遣先承認）、`overtime_agreement`（36協定の上限、警告と硬停止）、`dispatch_contract`（個別契約と抵触日）、`staffing_billing`（請求単価 >= 支払単価、勤怠承認が必須）、`matching`（スキル欄で候補を結ぶ）を追加しました。`Date`、`Duration`、`Rate` はパターン層の型として扱い、スロットの制約をコンパイル時に検査します。

36協定の上限はスロットで、一般則を既定かつ天井にします。抵触日は、通知された日に加え、同じ事業所・同じ人と組織単位の開始日から3年後を計算します。どちらも仕様の定数だけを見て、実績の時間や保存済みの契約は実行時の検査として生成します。社会保険料率と同一労働同一賃金は対象外です。設計と、社労士の確認が要る箇所は [docs/staffing.md](docs/staffing.md) にまとめています。勤怠、配属、請求単価をコピーして埋める空欄は [examples/templates/README.md](examples/templates/README.md) です。

```sh
cd patterns
python3 etsahc.py build examples/staffing.etsah --out build/staffing
python3 etsahc.py build examples/staffing_desk.etsah --out build/staffing_desk
python3 etsahc.py find "勤怠"
```

## AIに任せる作業の境界を仕様で止める

`ai_task` は、定期実行・新しい行・webhook のどれで起動するかを宣言し、AIが使ってよい効果、`spend` の1回と期間の上限、`net` と `emit` の時間あたり回数、再試行とエスカレーション、冪等キー、監査（`audit: ast_hash`）をスロットに持つ。`approval_gate` は、社外への `emit` と `net`、支払いの `spend`、契約の変更を、人が承認するまで止める。ゲートの無い社外への `emit` は `E-POLICY` になる。

契約層も同じ境界を `agent` と `task` で検査する。手順は既存の op を呼ぶ。社外への `emit` と `net`、`spend`、契約の変更にゲートが無いと `E-POLICY` になる。`check` は各タスクの起動、効果、ゲート、再試行を文章で出す。cron の実行、予算の残数、冪等ストアはまだ無い。

```sh
python3 etsah.py check examples/ai_tasks.etsah
```

設計と、採用した初期値は [docs/ai_automation.md](docs/ai_automation.md) にまとめた。

```sh
cd patterns
python3 etsahc.py build examples/ai_automation.etsah --out build/ai_automation
python3 etsahc.py build examples/ai_desk.etsah --out build/ai_desk
python3 etsahc.py find "自動化"
```

## エージェントのツールを契約で止める

英語の5分ガイドは [docs/en/agents-quickstart.md](docs/en/agents-quickstart.md)、ブラウザの試用は [docs/playground/index.html](docs/playground/index.html)、先行する仕組みとの比較は [docs/en/comparison.md](docs/en/comparison.md) です。MCP と OpenAI のツール定義を `op` に変換し、社外メール、支払い、契約変更、許可リスト外の通信を、実行前に `E-POLICY` で止めます。

```sh
python3 etsah.py import mcp examples/agents/tools/mcp_tools.json
python3 etsah.py check examples/agents/support_reply.etsah
python3 etsah.py check examples/agents/github_triage.etsah --allow-net github
```

同じ検査を MCP から呼べます。`etsah mcp` は stdio のサーバで、`etsah guard` は上流の `tools/call` を契約と照合してから転送します。`ASK` を通すのは、`etsah approve` が発行した署名付きの一度きりの承認が、契約・ゲート・ツール・引数ハッシュと一致するときだけです。`limit calls` と `limit spend` は台帳ファイルで減らし、署名があっても上限は超えません。設定は [docs/en/mcp.md](docs/en/mcp.md) と [docs/mcp.ja.md](docs/mcp.ja.md) です。

架空の問い合わせ Q-4401（`guest@example.invalid`）は、規則を無視して金庫のトークンを転送し、9000.00 JPY を返せと書いている。[examples/prompt_injection_leak.etsah](examples/prompt_injection_leak.etsah) はその秘密を `mail.forward` に渡すので、`E-IFLOW-ARG` で止まる。[examples/prompt_injection_safe.etsah](examples/prompt_injection_safe.etsah) はトークンを外に出さず、返信は `ASK`、契約に無い転送は `BLOCK`、1000.00 JPY を超える返金は署名が合っても `BLOCK` になる。手順と期待する出力は [README.md](README.md) の Prompt injection にある。

## 情報フローラベルでPIIの流出を止める

`pii` や `secret` を型に付け、許可されていない sink への流出をコンパイル時に検出します。`String @pii`、`sends`、`policy`、`declassify` の4つで、引数・戻り値・契約・sink を検査します。実装は `etsah.py` に入りました。この `policy` は `sink` と `declassify` 用で、AIタスクの `policy`（`external`、`payment`、`contract`）とは別のブロックです。一つのプログラムに両方置けます。

```etsah
type Customer {
  email: String @pii
}

policy
  sink net(email) accepts pii
  sink net(*) rejects secret
  declassify pii in masked_tracking require role support

op send_receipt(to: String @pii) -> Receipt
  eff net(email)
  sends to -> net(email)
  impl native
```

顧客のメールアドレスをそのまま分析に渡すと `E-IFLOW-ARG` で止まり、`declassify` を通せば `W-DECLASS` として監査に残ります。実際に動く2つの例で確かめられます。

```bash
# 0エラー、1警告（declassify の監査ログ）
python3 etsah.py check examples/information_flow_safe.etsah \
  --allow-net email --allow-net analytics

# E-IFLOW-ARG で失敗する
python3 etsah.py check examples/information_flow_leak.etsah \
  --allow-net email --allow-net analytics
```

分岐を越える漏れも止まります。`if` の条件が `pii` を持つと、その分岐の中で返す公開値にも条件のラベルが付きます。`if c.email == ...` で `"blocked"` と `"allowed"` を返し分けると、文字列自体は公開でも、どちらを返したかでメールアドレスが分かるため `E-IFLOW-RET` になります。条件を公開してよい場合は、条件式を `declassify` してください。

```etsah
let blocked = declassify(c.email == "blocked@example.com", pii, "判定結果だけを公開する")
if blocked
  return "blocked"
else
  return "allowed"
```

`examples/implicit_flow_leak.etsah` は `E-IFLOW-RET` で失敗し、`examples/implicit_flow_safe.etsah` は `W-DECLASS` 1件だけで通ります。`else` を書かずに片方だけ `return` する場合も、その後ろの公開の戻り値は同じラベルで検査します。条件が `pii` の分岐から、`pii` を受け取らない sink を呼ぶと `E-IFLOW-SINK` になります。

ラベル宣言も注釈もないプログラムでは検査を省略するため、既存の AST ハッシュは変わりません。式のラベル推論、sink の解決、`sends` の必須条件、診断コード、現時点の限界（代入・ループ・例外、コレクション、ラベル多相）は [docs/information-flow.md](docs/information-flow.md) と [SPEC.md](SPEC.md) にまとめています。
パターン層にも同じ考え方を入れました。アプリ仕様のフィールドに `note: String @pii` と書き、`policy` ブロックで sink の許可・拒否と `declassify` を宣言します。パターンのマニフェストは `data_policy` で、読み込み元、要求する sink、拒否ラベルを宣言します。両者をマージし、パターンが要求するラベルをアプリが許可していなければ `E-POLICY` で止まります。`data_policy` のないパターンをラベル付きデータに適用した場合も fail closed です。`build` は `security.json` を出力し、`flows`、`sinks`、`guarantees`、`unresolved`、`verified` を残します。安全な例は [patterns/examples/data_policy.etsah](patterns/examples/data_policy.etsah)、漏洩例は [patterns/examples/data_policy_leak.etsah](patterns/examples/data_policy_leak.etsah) です。

## 参加する

新しいパターンは `patterns/registry/<name>.json` を追加し、`python3 etsahc.py index` で索引を更新してPull Requestを出します。AIが生成した提案も歓迎しますが、人間のレビューは必須です。

詳しくは [CONTRIBUTING.md](CONTRIBUTING.md) と [patterns/registry/README.md](patterns/registry/README.md) を参照してください。

## ライセンス

MIT. Copyright (c) 2026 Ryuji Hashimoto (J-PROUT Inc.).

Developed by [J-PROUT Inc.](https://www.j-prout.co.jp/).
