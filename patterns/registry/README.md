# レジストリ：AIがパターンを見つける入口

このディレクトリは、Etsah Patternsのパターン定義をJSONで保持する。AIはコードを読む前に `index.json` を読み、`intent` と `tags` で候補を絞り、必要なマニフェストだけを開く。

## 探索の手順

```bash
cd patterns

# 1. 索引を読む（全パターンの intent / tags / slots が入っている）
cat registry/index.json

# 2. やりたいことで検索する（日本語でも英語でもよい）
python3 etsahc.py find "申請 承認"
python3 etsahc.py find "refund payment"

# 3. 候補のマニフェストを読む
cat registry/approval.json

# 4. 仕様に適用してビルドする
python3 etsahc.py build examples/expense.etsah
```

`find` は `intent`、`tags`、`name`、スロット説明の一致を点数化する。上位3件と、必須スロット・任意スロット・効果を表示する。

## マニフェストの読み方

| フィールド | AIが判断すること |
|---|---|
| `intent` | このパターンが解決する問題。依頼文との一致を見る |
| `tags` | 別名・日本語・英語。検索の再現率を上げる |
| `slots` | 埋めるべき値。`required: true` は必須 |
| `requires_fields` | 対象エンティティが持つべきフィールド名。型は見ない |
| `field_types` | 型まで見るフィールド。`Date`、`Duration`、`Rate` など。指定したときだけ厳密一致 |
| `constraints` | スロット値の比較。`date` / `rate` / `duration` と `<=` など。失敗は `E-POLICY` |
| `requires_patterns` | 同じ仕様に無いとエラーになるパターン名 |
| `automation` | AIタスクの横断規則。外部効果に承認ゲートが要ること、予算と回数、トリガー別のスロット |
| `requires_target` | パターン名のうしろにインスタンス名が要る（`approval_gate SendMail`） |
| `bound_roles` | `auth.roles` に含まれていなければならないロールスロット |
| `notes` | 検査しないこと、法令として埋め込まないこと |
| `states` / `transitions` | 承認などの状態遷移。遷移元・遷移先は `states` に含める |
| `hooks` | `on_approved` など、承認後に呼ぶ処理の参照先と引数（`notify` の `channel` / `recipient`） |
| `effects` | このパターンが起こす副作用。契約検査に渡す |
| `data_policy` | 情報フローの宣言。`labels`、`sources`、`sinks`、`rejects`、`declassify`。ラベル付きアプリでこれが無いパターンは `E-POLICY` |
| `artifacts` | 生成されるファイルと行数。実装の形を予測する |
| `examples` | 実際にビルドが通る使用例 |
| `provenance` | どのアプリから抽出したか。信頼性の判断材料 |

## 新しいパターンの追加

```bash
cp registry/approval.json registry/my_pattern.json
# name, version, intent, tags, slots, artifacts, examples, provenance を編集
python3 etsahc.py index
python3 etsahc.py build examples/your_app.etsah
```

追加の基準とレビューは `../../CONTRIBUTING.md` を参照。

## 索引の再生成

`index.json` は手で編集しない。マニフェストを変更したら次を実行する。

```bash
python3 etsahc.py index
```

CIは索引がマニフェストと一致しているかを確認する。
