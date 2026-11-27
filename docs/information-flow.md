# Etsah 情報フロー制御の設計（v0.1）

## 要約

`pii` や `secret` のような機密ラベルを型に付け、値が許可されていない出力先（sink）へ流れることをコンパイル時に検出する。ラベルを下げられるのは、ポリシーで許可された `declassify` だけである。

この設計で決めたことは次の9点。

1. ラベルは型の後置修飾 `String @pii` で書く。別構文のアノテーションにはしない。
2. `public` は「ラベルを書かないこと」で表す。`@public` は禁止する。
3. ラベルは順序を持たない集合として扱う。`@pii @secret` と `@secret @pii` は同じ。
4. レコード型のラベルは全フィールドの和集合。フィールドアクセスはそのフィールドのラベルだけを返す。
5. sink の許可は `policy` ブロックに書く。既定は public のみ。
6. `impl native` の op は `sends` で「どの引数がどの sink へ流れるか」を宣言する。
7. `declassify` だけがラベルを下げられる。理由・スコープ・ロールを要求する。
8. 診断メッセージにラベル付きの値を出さない。エラーログ自体を漏洩経路にしない。
9. ラベルを使わない既存プログラムの AST とハッシュは変えない。

![情報フローの検査経路](information-flow.png)

型に付いたラベルから式のラベルを推論し、引数・戻り値・契約・sink の4か所で検査する。`declassify` だけが推論の入力を下げられ、`native sends` が実行時実装のデータ束縛を宣言する。

## 1. 目的と脅威モデル

### 1.1 守るもの

- 個人情報（メールアドレス、氏名、住所、勤怠など）が、分析基盤・ログ・第三者 API へ意図せず流れること。
- API キー、トークン、パスワードなどの秘密が、画面・レポート・エラーメッセージへ出ること。
- プロンプトインジェクションで混入したコードが、既存の便利な関数（`track_event` など）を使ってデータを外部へ持ち出すこと。
- 監査できない場所でのマスキング・匿名化。誰が、どの操作で、どのラベルを、なぜ外したかを残す。

### 1.2 守らないもの

- `impl native` の内部実装。宣言された `sends` と効果を信頼する。悪意ある native は実行時の能力制限でしか止められない。
- 保存時・通信時の暗号化。ラベルは暗号化ではない。
- タイミング、キャッシュ、パケットサイズなどの副作用チャネル。
- ラベル宣言そのものの正しさ。機密フィールドにラベルを付け忘れれば public になる。
- OS・ランタイム・依存ライブラリの脆弱性。

### 1.3 想定する攻撃

| 攻撃 | v0.1 の静的検査 | 実行時（v0.3） |
|---|---|---|
| PII を分析 API へ送る | `E-IFLOW-SINK` | sink アダプタが拒否 |
| PII を public 引数へ渡す | `E-IFLOW-ARG` | `Labeled<T>` の型不一致 |
| PII を戻り値で隠す | `E-IFLOW-RET` | 戻り値のラベル検査 |
| 条件式で PII を漏らす | v0.2 の PC ラベル | 動的 taint |
| 理由なしのマスキング | `E-DECLASS` | 監査ログ |
| native が勝手に送信 | `sends` の宣言を信頼 | 能力ブローカー |

## 2. 設計原則

1. **ラベルは型の一部** — `String @pii` は型である。変数、引数、戻り値、フィールドのすべてで同じ構文を使う。
2. **デフォルトは public** — ラベルを書かなければ public。既存コードの意味とハッシュを変えない。
3. **fail closed** — ポリシーがなければ public のみ。`data_policy` のないパターンはラベル付きデータを受け取れない。
4. **下げる操作は1つだけ** — `declassify`。代入やキャストでラベルは消えない。
5. **診断は修復可能** — コード、行、期待ラベル、実際ラベル、修正案を構造化して返す。値そのものは出さない。
6. **意味論はハッシュに含める** — ラベル、ポリシー、`sends` は AST の一部。後から書き換えればハッシュが変わる。

## 3. 用語

| 用語 | 意味 |
|---|---|
| ラベル | `pii`, `secret`, `internal` などのタグ。順序なしの集合。 |
| public | ラベルなし。空集合 `∅`。 |
| 実効ラベル `L(T)` | 型 `T` の値が持ち得るラベルの集合。 |
| sink | 値が出ていく先。`net(email)`, `write(customers)`, `emit(audit)`, `spend(stripe)` など。 |
| source | 値が入ってくる先。`read(customers)` など。source 自体はラベルを持たず、戻り値の型がラベルを持つ。 |
| declassify | ポリシーに許可された場所でだけラベルを下げる操作。 |
| `sends` | native op の引数と sink のデータフローを宣言する束縛。 |
| PC ラベル | 分岐条件のラベル。暗黙フローを表す。v0.2 で導入する。 |

## 4. 構文

### 4.1 字句規則

- ラベル名は `[a-z][a-z0-9_]*`。大文字は使わない。
- `public` は予約語。`label public` は `E-LABEL`。
- `@` は型の直後のみ。`String @pii` のように空白を空けてもよい。
- ラベルは1つの型に0個以上。`String @pii @secret`。
- ラベルの重複は `E-LABEL`。`String @pii @pii` はエラー。
- 型引数の中のラベルは v0.1 では禁止する。`List<String @pii>` は `E-LABEL`。型引数を持つ型自体へのラベル `List<String> @pii` は許可する。
- 型引数内の `@` はパーサーが検出し、`E-LABEL` として報告する。構文エラーにはしない。
- 効果種別（`read`, `write`, `net`, `spend`, `emit`）と同じ名前のラベルは `E-LABEL`。`@net` のような紛らわしい指定を許さない。
- 予約語は `label`, `policy`, `sink`, `accepts`, `rejects`, `sends`, `none`, `declassify`, `public`。型名・op名・変数名には使えない。

### 4.2 EBNF

行指向の既存文法を拡張する。`INDENT` は2スペース、`NEWLINE` は改行。

```ebnf
program        = { declaration } ;
declaration    = label_decl | type_decl | op_decl | flow_decl | policy_decl ;

label_decl     = "label" ident [ string ] NEWLINE ;

type_decl      = "type" ident [ label_list ] ( "{" NEWLINE { field } "}" NEWLINE
                                         | NEWLINE ) ;
field          = INDENT ident ":" type_ref NEWLINE ;

type_ref       = type_base { "@" ident } ;
type_base      = ident [ "<" type_arg { "," type_arg } ">" ] ;
type_arg       = ident ;

op_decl        = "op" ident "(" [ params ] ")" "->" type_ref NEWLINE { op_prop } ;
params         = param { "," param } ;
param          = ident ":" type_ref ;

op_prop        = "doc" string
               | "eff" effects
               | "sends" send_args "->" effect
               | "pre" expr
               | "post" expr
               | "impl" ( "native" | expr ) ;

send_args      = "none" | ident { "," ident } ;
effect         = ident "(" [ target ] ")" ;
target         = ident | "*" ;

flow_decl      = "flow" ident "(" [ params ] ")" "->" type_ref NEWLINE { flow_stmt } ;
flow_stmt      = "doc" string
               | "summary" string
               | "eff" effects
               | "let" ident "=" expr
               | "return" expr ;

policy_decl    = "policy" NEWLINE { policy_rule } ;
policy_rule    = "sink" effect ( "accepts" label_list | "rejects" label_list )
               | "declassify" label_ref "in" ident "require" "role" ident ;

label_list     = ident { "," ident } ;
label_ref      = ident | "public" ;
```

`policy` は1プログラムに1つ。2つ以上は `E-POLICY`。`sink` と `declassify` 以外のルールは `E-POLICY`。

`op_prop`、`flow_stmt`、`policy_rule` は行頭に2スペースのインデントを置く（図では省略）。

式の中では、既存の一次式に次を追加する。

```ebnf
primary        = ... | "declassify" "(" expr "," label_ref "," expr ")" ;
```

`declassify` は予約語。変数名には使えない。第3引数は式として解析し、チェッカーが文字列リテラルであることを検査する（`E-DECLASS`）。

### 4.3 ラベル宣言

```etsah
label pii "個人情報"
label secret "秘密情報"
label internal "社内情報"
```

- 文字列は人間向けの説明。省略できる。
- 未宣言のラベルを型・ポリシー・`declassify` で使うと `E-LABEL`。
- 同じ名前の宣言は `E-LABEL`。
- ラベルの宣言順は意味を持たない。AST では名前でソートする。

### 4.4 型

```etsah
type Customer {
  id: Id
  email: String @pii
  api_key: String @secret
  plan: String @internal
}

type ApiKey @secret
type Money
```

- レコード型 `type T { ... }` の実効ラベルは全フィールドの和集合。
- 不透明型 `type T @L` は、その型の全値が `L`。
- 不透明型にラベルを書かなければ public。
- レコード型そのものへのラベル `type Customer @internal { ... }` は `E-LABEL`。レコードはフィールドにラベルを付ける。
- 型参照へのラベル `Customer @internal` も `E-LABEL`。レコードのラベルはフィールドから決まる。
- 組み込み型 `String`, `Int`, `Bool`, `Money`, `Id` などへのラベルは許可する。
- 型引数の中のラベルは `E-LABEL`（v0.1 の制限）。

### 4.5 op と flow のシグネチャ

```etsah
op send_receipt(to: String @pii, body: String) -> Receipt
  eff net(email)
  sends to, body -> net(email)
  impl native
```

- 引数・戻り値の型にラベルを付ける。
- `flow` にも同じ構文を使う。`flow receipt(...) -> Receipt @pii` は許可する。
- 型注釈のラベルは「その値が持ち得るラベルの上限」を宣言する。実引数のラベルが上限を超えたら `E-IFLOW-ARG`。
- 戻り値のラベルは「この操作が返し得るラベルの上限」。実装がそれを超えたら `E-IFLOW-RET`。

### 4.6 `sends`（native op のデータ束縛）

ラベルだけでは「どの引数がどの sink へ流れるか」が分からない。特に `impl native` は本体が見えないため、明示的な束縛が必要になる。

```etsah
op send_receipt(to: String @pii, body: String) -> Receipt
  eff net(email)
  sends to, body -> net(email)
  impl native

op save_customer(customer: Customer) -> Id
  eff write(customers)
  sends customer -> write(customers)
  impl native

op health_check() -> Bool
  eff net(health)
  sends none -> net(health)
  impl native
```

- `sends p1, p2 -> effect` は、引数 `p1`, `p2` がその effect の sink へ流れると宣言する。
- `effect` は `eff` に宣言済みでなければならない（`E-SINK`）。
- 引数名は実在しなければならない（`E-SINK`）。
- `sends` の effect に `*` は使えない（`E-SINK`）。`sends` は具体的な sink にだけ束縛する。
- `sends none -> effect` は、その sink にラベル付きデータを渡さない宣言。
- 1つの `sends` が束縛する effect は1つ。複数 sink へ流すなら行を分ける。
- `sends` は `impl native` の op でだけ許可する。式実装の op に書くと `E-SINK`（フローは本体から推論できるため、宣言を信用しない）。
- `sends` の引数は重複してもよい。AST ではソートして一意にする。
- `impl native` の op が `read` 以外の効果を持ち、次のいずれかに該当するなら、各非 read effect に `sends` を要求する。なければ `E-SINK-UNBOUND`。
  - ラベル付きの引数がある。
  - ラベル付きの戻り値がある。
  - `read(...)` の効果がある。
- 3つ目は、引数にも戻り値にもラベルが現れないが、read したデータを集計・加工して sink へ流す native op を捕まえるためである。`sends none -> effect` は「ラベル付きデータを渡さない」という監査可能な宣言になる。
- ラベル付きの引数も戻り値も read もない native op は、public なデータだけを扱う前提で、`sends` を要求しない。

### 4.7 `policy`

```etsah
policy
  sink net(email) accepts pii
  sink write(customers) accepts pii
  sink emit(audit) accepts internal
  sink net(*) rejects secret
  declassify pii in send_receipt require role support
  declassify public in export_audit require role security_admin
```

#### sink ルール

- `sink <effect> accepts <labels>` — その sink が受け取れるラベルを追加する。
- `sink <effect> rejects <labels>` — その sink が受け取ってはいけないラベルを追加する。
- 既定は public のみ。`accepts` に public を書く必要はない。`accepts public` は `E-LABEL`（public は暗黙）。
- `net(*)` は同じ効果種別の全ターゲットに対するワイルドカード。
- `read(...)` は sink ではないため、`sink read(...)` は `E-POLICY`。
- ターゲットが空の `sink net()` は `E-POLICY`。
- 同じ sink・同じラベルに対する `accepts` と `rejects` の共存は `E-POLICY`。同じモードの重複は冪等で、エラーにしない。

既存の CLI `--allow-net` / `--deny-net` はデプロイ先の制限であり、この `policy` の sink ラベルとは独立に両方が適用される。`--allow-net` は「どこへ出せるか」、`policy` は「何を出せるか」を決める。片方を緩めても、もう片方は緩まない。

#### 解決アルゴリズム

```python
def resolve(effect):
    exact = [r for r in policy.sinks if r.effect == effect]
    wild = [r for r in policy.sinks
            if r.effect.kind == effect.kind and r.effect.target == "*"]
    # 許可: 具体的な accepts があればそれを使い、なければワイルドカードの accepts
    exact_accepts = [r for r in exact if r.mode == "accepts"]
    wild_accepts = [r for r in wild if r.mode == "accepts"]
    accepts = union(r.labels for r in (exact_accepts or wild_accepts))
    # 拒否: 具体的なルールとワイルドカードの和集合。上書きできない。
    rejects = union(r.labels for r in exact + wild if r.mode == "rejects")
    return accepts, rejects

def check_sink(effect, labels):
    accepts, rejects = resolve(effect)
    if labels & rejects:
        error("E-IFLOW-SINK", rejected=labels & rejects)
    if not labels <= accepts:
        error("E-IFLOW-SINK", not_accepted=labels - accepts)
```

`accepts` は、その sink に具体的な `accepts` があればそれがワイルドカードを置き換える（絞り込む）。`rejects` は加算的で、より具体的な `accepts` で上書きできない。`net(*) rejects secret` は全ネットワークに対する絶対的な下限になる。public は空集合なので常に通る。

例えば `sink net(*) accepts pii` と `sink net(email) rejects secret` がある場合、`net(email)` の許可は `{pii}`、拒否は `{secret}`。exact の `rejects` は wildcard の `accepts` を打ち消さない。

### 4.8 `declassify`

ラベルを下げる唯一の操作。式として書く。

```etsah
declassify(customer.email, pii, "領収書の宛先に使う")
```

- 第1引数は式。
- 第2引数は外すラベル名、または `public`。どちらでもなければ `E-DECLASS`。
- 第3引数は監査ログに残す理由。文字列リテラルで、前後の空白を除いて1文字以上、改行を含まず、200文字以下。違反は `E-DECLASS`。
- 結果のラベルは `L(expr) - {label}`。
- 第2引数に `public` を書くと全ラベルを外す。最も強い操作なので、ポリシーで明示的に許可された箇所だけに限る。
- 対象ラベルを持っていない場合は `E-DECLASS`（タイポを黙って通さない）。
- v0.1 ではレコード型の値に `declassify` できない（`E-DECLASS`）。フィールドごとに外す。レコード全体を一括で下げると、フィールドアクセスが元のラベルを返すため、意味が混乱するからである。
- `pre` / `post` の中の `declassify` は `E-DECLASS`。契約は public でなければならない。
- ネストは許可する。各 `declassify` がそれぞれポリシーを持つ必要がある。

`declassify` は値を作り変えない。ラベルを下げるだけである。実際のマスキング・匿名化・暗号化は別の操作で行い、その結果に対して `declassify` する。`declassify` は「この値はもう安全だと主張する」監査可能な宣言であり、変換ではない。生のメールアドレスに `declassify(..., "マスクする")` と書いても、値は生のままである。

許可は `policy` に書く。

```etsah
policy
  declassify pii in masked_email require role support
```

- `in <op|flow>` は、その操作の中でのみ許可する。`*` は許可しない。
- `in` のスコープが実在する op / flow でなければ `E-POLICY`。
- `require role <role>` は、パターン層の `auth.roles` と照合する。契約層にはロール宣言がないため、文字列として保持し、パターンコンパイラが検査する。
- 許可されていない場所での `declassify` は `E-DECLASS`。
- 許可された `declassify` は `W-DECLASS` として監査レポートに残す。警告であってエラーではない。
- 同じ `(label, scope)` に対する複数の `declassify` ルールは `E-POLICY`。

## 5. ラベルの意味論

### 5.1 集合ラベル

ラベルは順序を持たない集合として扱う。

```
{pii, secret} = {secret, pii}
{pii} ∪ {secret} = {pii, secret}
{pii} - {pii} = ∅
```

`pii` と `secret` は独立したタグであり、`secret` が `pii` を包含するとは定義しない。API キーは `secret` だが PII ではない。メールアドレスは `pii` だが API キーではない。両方必要な値は `@pii @secret` とする。

将来的に `label secret > pii` のような階層を足せるが、v0.1 では明示的な集合のままにする。ポリシーで両方を許可するか、`declassify` で片方を外す。

### 5.2 実効ラベル

```
L(String) = ∅
L(Int) = ∅
L(Bool) = ∅
L(type T @L) = L                    （不透明型）
L(type T { f1: T1 @L1, ... }) = L1 ∪ ... ∪ Ln   （レコード型）
L(T @L) = L(T) ∪ L                  （T が組み込み型・不透明型のとき）
L(T @L) は定義しない                （T がレコード型のときは E-LABEL）
```

レコード型の値全体を sink へ渡すときは、全フィールドのラベルの和集合が検査される。フィールドアクセス `e.f` のラベルは、そのフィールドの宣言ラベルだけを使う。ほかのフィールドのラベルは混ぜない。これにより、`Customer` 全体は `pii` でも、`customer.id` は public のまま扱える。

再帰型（`type Node { value: String @pii, next: Node }`）では、`L(Node)` の計算中に再び `Node` が現れる。訪問済みの型に戻ったときは `∅` を返し、和集合を取る。これはラベルの最小不動点を求める計算になり、上の例では `L(Node) = {pii}` になる。相互再帰も同じ規則で打ち切る。

### 5.3 代入可能性

`T1 @L1` を `T2 @L2` として使えるのは、基底型が互換で、`L1 ⊆ L2` のときだけ。

```
String           -> String @pii          許可（public は PII として扱える）
String @pii      -> String               拒否（PII を public 文脈に渡せない）
String @pii      -> String @pii @secret  許可（より多く扱う文脈）
String @pii @secret -> String @pii       拒否（secret を扱えない文脈）
```

引数は `L(actual) ⊆ L(param)`、戻り値は `L(actual) ⊆ L(declared_return)` を検査する。関数が `@pii` を返すと宣言して public を返すのは安全（約束より少ない）。逆に public と宣言して PII を返すのは `E-IFLOW-RET`。

### 5.4 レコードとフィールド

```etsah
type Customer {
  id: Id
  email: String @pii
  api_key: String @secret
}
```

- `L(Customer) = {pii, secret}`。
- `customer.id` は `Id`、ラベル `∅`。
- `customer.email` は `String @pii`。
- `customer.api_key` は `String @secret`。
- `save_customer(customer)` の引数ラベルは `{pii, secret}`。`write(customers)` が `pii` だけを許可するなら `E-IFLOW-SINK`。秘密は別の sink に分けるか、保存前に `declassify(customer.api_key, secret, ...)` する。

### 5.5 型引数

v0.1 では型引数の中のラベルを扱わない。`List<String>` は public なリストとして扱い、要素のラベルは追跡しない。必要な場合は `List<String> @pii` のようにコンテナ全体にラベルを付ける。型引数を持つ型は現在のサンプルでは使っていないため、実装上の後退はない。

## 6. 静的検査

### 6.1 検査順序

1. 構文解析
2. 重複宣言（型・op・flow・ラベル・ポリシールール）
3. ラベル宣言の検査（`E-LABEL`）
4. 型宣言の検査（フィールドの型存在、フィールドのラベル存在）
5. op の型・効果・契約・`sends` の検査
6. flow の型・効果・契約の検査
7. 既存のネットワークポリシー検査（`--allow-net` / `--deny-net`）
8. 情報フローの検査（引数、戻り値、契約、sink、declassify）

ラベルにエラーがある場合も、型検査は続ける。ただし未宣言ラベルは public として扱い、連鎖的な誤検出を抑える。

ラベル宣言も `@` 注釈もないプログラムでは、情報フロー検査をスキップする。既存プログラムの診断・AST・ハッシュを変えないための高速パスである。`label` を1つでも宣言した時点で、そのプログラム全体に検査を適用する（fail closed）。`policy` だけがあってラベル宣言がない場合も高速パスを通るが、その `policy` が使うラベルは未宣言なので `E-LABEL` になる。

### 6.2 式のラベル推論

`Γ` は変数環境、`Γ(x) = τ @ L` は変数 `x` の型とラベル、`base(τ @ L) = τ`、`labels(τ @ L) = L` とする。

```
(LIT)    Γ ⊢ c : base(c) @ ∅

(VAR)    Γ(x) = τ @ L
         ─────────────────
         Γ ⊢ x : τ @ L

(FIELD)  Γ ⊢ e : T @ L     field(T, f) = τ @ Lf
         ─────────────────────────────────────
         Γ ⊢ e.f : τ @ Lf

(CALL)   op f(xi: τi @ Li) -> τr @ Lr
         Γ ⊢ ai : σi @ Mi     base(σi) ≤ τi     Mi ⊆ Li   （各 i）
         ──────────────────────────────────────────────────
         Γ ⊢ f(a1, ..., an) : τr @ Lr

(BIN)    Γ ⊢ e1 : τ1 @ L1     Γ ⊢ e2 : τ2 @ L2     binop(⊕, τ1, τ2) = τ
         ────────────────────────────────────────────────────────
         Γ ⊢ e1 ⊕ e2 : τ @ (L1 ∪ L2)

(UN)     Γ ⊢ e : τ @ L     unop(!, τ) = Bool
         ─────────────────────────────────
         Γ ⊢ !e : Bool @ L

(DEC)    Γ ⊢ e : τ @ L     (l = public ∧ L ≠ ∅) ∨ l ∈ L     allowed(scope, l)     valid_reason(r)
         ─────────────────────────────────────────────────────────────────────────────────────
         Γ ⊢ declassify(e, l, r) : τ @ (if l = public then ∅ else L \ {l})
```

補足。

- `L(literal) = ∅`。数値・文字列・真偽値のリテラルは public。
- `L(variable)` は宣言ラベル。`let` は初期化式から推論する。
- `L(e.field)` はフィールドの宣言ラベルだけ。レコード全体のラベルは混ぜない。
- `L(op(args))` は op の宣言戻り値ラベル。実装の実際の戻り値は `check_op` で別に検査する。
- `L(e1 + e2)`、`L(e1 == e2)`、`L(e1 && e2)` は両辺の和集合。比較結果の `Bool` にもラベルを付ける。`customer.email == "a@b"` の真偽は PII の情報を含むため、public として扱わない。
- `L(!e) = L(e)`。
- `declassify(e, public, r)` は `∅` を返す。`public` は「全ラベルを外す」特別な識別子。
- 型が `Unknown` の式は、ラベルも `∅` として扱い、連鎖的なエラーを抑える。

### 6.3 呼び出し

呼び出し `f(a1, ..., an)` について、各引数 `i` で次を検査する。

```
base(type_of(ai)) ≤ base(τi)      （既存の型検査）
labels(type_of(ai)) ⊆ Li          （新しいラベル検査）
```

違反は `E-IFLOW-ARG`。メッセージには引数名、期待ラベル、実際ラベルを出す。値は出さない。

```text
[ERROR] E-IFLOW-ARG (line 42): argument 'event' of track_event expects no labels, got pii
    fix: declassify(customer.email, pii, "reason") or change the parameter to String @pii
```

### 6.4 戻り値

`return e` と `impl` の式について、`labels(type_of(e)) ⊆ labels(declared_return)` を検査する。違反は `E-IFLOW-RET`。

op の `impl` が別の op の呼び出しなら、その戻り値ラベルをそのまま使う。flow の `return` も同じ規則。

### 6.5 契約

`pre` と `post` の式は public でなければならない。`labels(type_of(expr)) = ∅`。違反は `E-IFLOW-CONTRACT`。

```etsah
op find_customer(email: String @pii) -> Customer
  pre email != ""          # E-IFLOW-CONTRACT
  eff read(customers)
  impl customers.find(email)
```

これは厳しすぎるように見えるが、契約式の真偽は呼び出し側とログに現れる。PII を含む条件を許すと、契約検査そのものが漏洩経路になる。公開フィールド（`email_present: Bool` など）を別に持たせるか、契約を諦めて実行時ガードにする。

`post` の `result` は op の戻り値ラベルを持つ。`result` 全体、またはラベル付きフィールドを参照する `post` は書けない。public なフィールドだけを見る `post`（`result.status == "refunded"` など）は書ける。これも同じ理由による。

### 6.6 `sends` 経由の sink 検査

1. `eff` の各 effect について、`resolve(effect)` で `accepts` と `rejects` を求める。
2. `sends` で束縛された各引数 `p` について、`labels(Γ(p))` を検査する。
3. `labels & rejects ≠ ∅` なら `E-IFLOW-SINK`。
4. `labels ⊄ accepts` なら `E-IFLOW-SINK`。
5. 複数の引数が同じ sink へ流れる場合、引数ごとに検査する。和集合で判定しない。1つでも拒否ラベルを含めばエラー。

### 6.7 native op の `sends` 検査

`impl native` の op について、次を検査する。

1. `sends` の effect が `eff` に存在する（`E-SINK`）。
2. `sends` の引数が実在する（`E-SINK`）。
3. 非 read の effect が1つ以上あり、かつ「ラベル付き引数がある」「ラベル付き戻り値がある」「`read(...)` の効果がある」のいずれかなら、各非 read effect に `sends` がある（`E-SINK-UNBOUND`）。
4. `sends` の引数ラベルが sink の許可集合を満たす（`E-IFLOW-SINK`）。

`read` は source であり sink ではないため、`sends` を要求しない。

### 6.8 declassify の検査

1. 第2引数が宣言済みラベルまたは `public`（`E-DECLASS`）。
2. 第3引数が文字列リテラルで、長さ・改行の条件を満たす（`E-DECLASS`）。
3. 対象式のラベルに対象ラベルが含まれる（`E-DECLASS`）。
4. 現在のスコープ（op 名または flow 名）に対する `declassify` ポリシーがある（`E-DECLASS`）。
5. 対象式の基底型がレコードでない（`E-DECLASS`）。
6. `pre` / `post` の中ではない（`E-DECLASS`）。
7. すべて満たすなら `W-DECLASS` を出す。

`declassify` の結果は式のラベルを下げるが、元の変数のラベルは変わらない。`let masked = declassify(customer.email, pii, "...")` の `masked` は public、`customer.email` は `pii` のまま。

### 6.9 式実装のフロー

式実装の op は、呼び出し先の効果と引数ラベルから sink フローを推論する。ただし、呼び出し先の引数ラベル検査（6.3）と sink 検査（6.6）が推移的に効くため、追加の検査は不要である。

```
op send_receipt(to: String @pii) -> Receipt
  eff net(email)
  sends to -> net(email)
  impl native

flow receipt(id: Id) -> Receipt
  eff read(customers), net(email)
  let c = customers.get(id)
  return send_receipt(c.email)     # c.email は pii、to は pii、net(email) は pii を許可
```

`c.email` が `pii`、`send_receipt` の `to` が `pii`、`net(email)` が `pii` を許可するので通る。`c.api_key` を渡すと、`to` のラベル上限 `{pii}` を `{secret}` が超えるため `E-IFLOW-ARG`。仮に `to` を `@secret` にしても、`net(email)` が `secret` を拒否すれば `E-IFLOW-SINK`。

### 6.10 暗黙フロー（v0.2 で実装）

flow に `if` / `else` を追加し、条件式のラベルをプログラムカウンタ（PC）ラベルとして分岐内の値に合成する。実装は `etsah.py` の `_check_flow_block` にあり、`pc` を再帰的に渡す。

```
flow classify(id: Id) -> String
  let c = customers.get(id)
  if c.email == "blocked@example.com"   # 条件は pii
    return "blocked"                     # L(式) ∪ PC = {pii} なので E-IFLOW-RET
  else
    return "allowed"
```

規則は次のとおり。

- `if` の条件は `Bool`。条件のラベルを `PC` に加える。
- 分岐内の `let` は、束縛する値のラベルに `PC` を加える。
- 分岐内の `return` は `L(式) ∪ PC ⊆ L(戻り値型)` を検査する。
- ネストした `if` は `PC` を和集合で引き継ぐ。
- 分岐内の変数スコープは分岐内だけ。両方の分岐が `return` すれば、`if` の後ろは到達不能。
- 効果は、条件式と両方の分岐の和集合。
- 片方の分岐が `return` できるとき、`if` の後ろの文は、その条件と、戻りを守るネストした条件のラベルを帯びる。どちらの分岐も返さないとき、後ろの文の PC は上がらない。
- PC の下で実行する `write` / `net` / `spend` / `emit` は、そのラベルを受け入れる sink でなければならない。受け入れない sink は `E-IFLOW-SINK`。
- `if` は flow のみ。task の本体は `let` と `return` だけ。

条件を公開してよい場合は、条件式そのものを `declassify` する。`declassify` はラベルを1つ外すので、`Bool` の比較結果にも使える。許可された場合は `W-DECLASS` が残り、`PC` は空になる。

```
let blocked = declassify(c.email == "blocked@example.com", pii, "判定結果だけを公開する")
if blocked
  return "blocked"
```

代入・ループ・例外はないため、「分岐の後で変数を更新して合流する」形の暗黙フローは表現できない。`&&` / `||` は短絡評価をせず、両辺のラベルの和集合を結果に付ける保守的な扱いのままである。

## 7. 診断

### 7.1 コード表

| コード | 意味 | 例 |
|---|---|---|
| `E-LABEL` | 未宣言ラベル、重複宣言、public の再定義、`@public`、レコードへの注釈、型引数内のラベル、policy での public 指定 | `unknown label 'pii2'` |
| `E-IFLOW-ARG` | 引数のラベルが上限を超える | `argument 'event' of track_event expects no labels, got pii` |
| `E-IFLOW-RET` | 戻り値のラベルが宣言より高い | `flow returns pii, declares no labels` |
| `E-IFLOW-SINK` | sink が受け取れないラベル | `net(analytics) does not accept pii` |
| `E-IFLOW-CONTRACT` | pre/post にラベル付き値 | `precondition uses pii value` |
| `E-SINK` | `sends` の effect/引数が不正、native 以外での `sends` | `sends references undeclared effect net(email)` |
| `E-SINK-UNBOUND` | native op に `sends` がない | `op send_receipt has labeled data but no sends for net(email)` |
| `E-DECLASS` | 許可されない declassify、理由不足、レコード、契約内 | `declassify pii is not allowed in flow export` |
| `E-POLICY` | ポリシーの重複・矛盾・不正なルール | `sink net(email) both accepts and rejects secret` |
| `W-DECLASS` | 許可された declassify の監査警告 | `pii declassified in send_receipt` |

### 7.2 JSON の形

既存の `Diagnostic` に任意の `context` を足す。空なら出力しない。既存の JSON は変わらない。

```json
{
  "code": "E-IFLOW-SINK",
  "severity": "error",
  "line": 42,
  "message": "net(analytics) does not accept pii",
  "fix": "send a declassified value, or allow pii on net(analytics)",
  "context": {
    "sink": "net(analytics)",
    "actual_labels": ["pii"],
    "accepted_labels": [],
    "rejected_labels": []
  }
}
```

`context` のキーは診断コードごとに固定する。

| コード | context |
|---|---|
| `E-IFLOW-ARG` | `argument`, `expected_labels`, `actual_labels` |
| `E-IFLOW-RET` | `expected_labels`, `actual_labels` |
| `E-IFLOW-SINK` | `sink`, `accepted_labels`, `rejected_labels`, `actual_labels` |
| `E-IFLOW-CONTRACT` | `clause`, `actual_labels` |
| `E-SINK` | `sends`, `effect`, `parameter` |
| `E-SINK-UNBOUND` | `effect`, `labeled_parameters` |
| `E-DECLASS` | `label`, `scope`, `reason_kind`（`empty` / `newline` / `too_long` / `not_string` のいずれか。本文は出さない） |

### 7.3 メッセージ方針

- ラベル付きの**値**を含めない。変数名・型名・ラベル名・行番号だけを出す。
- 理由文字列の本文をエラーに含めない。「reason is empty」「reason contains a newline」のように種別だけを出す。
- 修正案は1つに絞る。`declassify` を勧める場合は、必要なポリシー行も示す。
- 同じ違反を重複して出さない。1つの式につき1つの `E-IFLOW-*`。

## 8. 例と期待診断

![PII漏洩を止める検査経路](pii-flow.png)

顧客のメールアドレスは `pii` のまま `send_receipt` を通り、`net(email)` で許可される。`track_event` にそのまま渡すと引数の契約で止まり、引数を `@pii` に変えても `net(analytics)` のポリシーで止まる。どうしても必要なときだけ、ポリシー・スコープ・ロール・理由付きの `declassify` で外す。

### 8.1 安全な例

```etsah
label pii "個人情報"
label secret "秘密情報"
label internal "社内情報"

type Customer {
  id: Id
  email: String @pii
  api_key: String @secret
}

policy
  sink net(email) accepts pii
  sink write(customers) accepts pii
  sink emit(audit) accepts internal
  sink net(*) rejects secret

op customers.get(id: Id) -> Customer
  eff read(customers)
  impl native

op send_receipt(to: String @pii, body: String) -> Receipt
  eff net(email)
  sends to, body -> net(email)
  impl native

op track_event(event: String) -> Bool
  eff net(analytics)
  sends event -> net(analytics)
  impl native

flow receipt(customer_id: Id) -> Receipt
  eff read(customers), net(email)
  let customer = customers.get(customer_id)
  return send_receipt(customer.email, "receipt")
```

期待: エラー0、警告0。

- `customer.email` は `pii`、`send_receipt.to` は `pii`、`net(email)` は `pii` を許可。
- `body` は public、`net(email)` は public を常に許可。
- `net(*) rejects secret` は `net(email)` にも適用されるが、`customer.email` は `secret` を持たない。

### 8.2 違反例: 引数

```etsah
flow leak(customer_id: Id) -> Bool
  eff read(customers), net(analytics)
  let customer = customers.get(customer_id)
  return track_event(customer.email)
```

期待:

```text
[ERROR] E-IFLOW-ARG (line 30): argument 'event' of track_event expects no labels, got pii
    fix: declassify(customer.email, pii, "reason") or change the parameter to String @pii
```

`track_event` の引数を `String @pii` に変えると、今度は `net(analytics)` が `pii` を許可しないため `E-IFLOW-SINK` になる。両方を直すには、`declassify` してから渡すか、`net(analytics)` に `pii` を許可する（推奨しない）。

### 8.3 違反例: 戻り値

```etsah
flow expose(customer_id: Id) -> String
  eff read(customers)
  let customer = customers.get(customer_id)
  return customer.email
```

期待:

```text
[ERROR] E-IFLOW-RET (line 34): flow returns pii, declares no labels
    fix: change the return type to String @pii, or declassify the value
```

### 8.4 違反例: 契約

```etsah
op find_customer(email: String @pii) -> Customer
  pre email != ""
  eff read(customers)
  impl customers.find(email)
```

期待:

```text
[ERROR] E-IFLOW-CONTRACT (line 40): precondition uses pii value
    fix: use a public field such as email_present, or remove the precondition
```

### 8.5 declassify を使う例

```etsah
op mask_email(email: String @pii) -> String @pii
  impl native

policy
  declassify pii in masked_email require role support

flow masked_email(customer_id: Id) -> Bool
  eff read(customers), net(analytics)
  let customer = customers.get(customer_id)
  let masked_value = mask_email(customer.email)
  let masked = declassify(masked_value, pii, "分析用にマスクする")
  return track_event(masked)
```

期待: エラー0、警告1。

```text
[WARNING] W-DECLASS (line 48): pii declassified in flow masked_email
    fix: review the declassification policy for role support
```

`mask_email` は値をマスクするが、ラベルは `pii` のまま返す。`declassify` がその結果を public に下げ、`track_event` と `net(analytics)` を通る。`require role support` はパターン層の `auth.roles` と照合する。`declassify` 自体は値を変換しないため、マスク処理を別の op に分けるのが正しい使い方である。

### 8.6 違反例: レコードの declassify

```etsah
flow anonymize(customer_id: Id) -> Bool
  eff read(customers)
  let customer = customers.get(customer_id)
  let masked = declassify(customer, pii, "顧客を匿名化する")
  return true
```

期待:

```text
[ERROR] E-DECLASS (line 52): cannot declassify a record type; declassify each field
    fix: declassify(customer.email, pii, "...")
```

### 8.7 違反例: native の sends 不足

```etsah
op save_customer(customer: Customer) -> Id
  eff write(customers)
  impl native
```

期待:

```text
[ERROR] E-SINK-UNBOUND (line 58): op save_customer has labeled data but no sends for write(customers)
    fix: add `sends customer -> write(customers)`
```

## 9. AST とハッシュ

### 9.1 方針

ラベル・ポリシー・`sends` はセキュリティ意味論の一部なので、AST ハッシュに含める。ただし、ラベルを使わない既存プログラムのハッシュは変えない。

そのため、`program_to_dict` には**空のとき省略する追加キー**として入れる。既存のキーの形は変えない。

`declassify` の理由文字列も AST に含めてハッシュする。理由を書き換えればハッシュが変わるため、「どの理由でラベルを外したか」が監査可能になる。

```json
{
  "labels": [{"name": "pii", "doc": "個人情報"}],
  "types": [
    {
      "name": "Customer",
      "opaque": false,
      "fields": {"api_key": "String", "email": "String", "id": "Id"},
      "field_labels": {"api_key": ["secret"], "email": ["pii"]}
    },
    {
      "name": "ApiKey",
      "opaque": true,
      "fields": {},
      "labels": ["secret"]
    }
  ],
  "ops": [
    {
      "name": "send_receipt",
      "params": [["to", "String"], ["body", "String"]],
      "param_labels": {"to": ["pii"]},
      "ret": "Receipt",
      "effects": [["net", "email"]],
      "sends": [{"args": ["body", "to"], "effect": ["net", "email"]}],
      "pre": [],
      "post": [],
      "impl": null,
      "native": true,
      "doc": null
    }
  ],
  "policy": {
    "sinks": [
      {"effect": ["net", "email"], "mode": "accepts", "labels": ["pii"]}
    ],
    "declassify": [
      {"label": "pii", "scope": "masked_email", "role": "support"}
    ]
  }
}
```

この例では `Customer` の `id` と `Receipt` の戻り値にラベルがないため、`field_labels` の `id`、`ret_labels`、`labels`（型）は省略されている。

### 9.2 正規化

- ラベル集合はソートして配列にする。
- `field_labels` のキーはフィールド名でソートする。
- `param_labels` のキーは引数名でソートする。
- `sends` は `(effect, args)` でソートし、`args` もソートする。
- `policy.sinks` は `(effect.kind, effect.target, mode, labels)` でソートする。
- `policy.declassify` は `(scope, label, role)` でソートする。
- 空の `labels` / `field_labels` / `param_labels` / `ret_labels` / `sends` / `policy` は出力しない。
- ラベル宣言の順序、ポリシールールの順序、`sends` の行順はハッシュに影響しない。

### 9.3 後方互換の確認

- `examples/refund_fixed.etsah` のハッシュが `bae8501e8b3f` のままであること。
- `tests/test_ast.py` の既存ケースがすべて通ること。
- ラベルだけを追加したプログラムと、ラベルの順序を入れ替えたプログラムのハッシュが一致すること。

## 10. パターン層との統合

### 10.1 manifest の `data_policy`

```json
{
  "name": "customer_portal",
  "slots": {
    "email": {"type": "String", "labels": ["pii"]}
  },
  "data_policy": {
    "labels": ["pii", "secret", "internal"],
    "sources": {"customers": ["pii"]},
    "sinks": {
      "net(email)": {"accepts": ["pii"]},
      "net(analytics)": {"accepts": []},
      "emit(audit)": {"accepts": ["internal"]}
    },
    "rejects": {"net(*)": ["secret"]},
    "declassify": [
      {"label": "pii", "in": "masked_email", "roles": ["support"]}
    ]
  }
}
```

- `labels` — パターンが使うラベル。
- `sources` — 読み込み元と、そのデータに付くラベル。
- `sinks` — パターンが要求する sink と許可ラベル。
- `rejects` — パターンが要求する拒否ラベル。アプリの拒否と和集合にする。
- `declassify` — パターンが要求する declassify とロール。
- `data_policy` がないパターンは public のみ。ラベル付きデータに適用すると失敗する（fail closed）。

アプリ側では、エンティティのフィールドに `@label` を付け、`policy` ブロックで sink と declassify を宣言する。

```text
app expense_flow
  stack nextjs, postgres
  entity Expense
    id: Id
    amount: Money @internal
    note: String @pii
  policy
    labels pii, internal, secret
    sink notify(manager) accepts pii, internal
    sink emit(audit) accepts internal
    sink net(*) rejects secret
    declassify pii in masked_email require role support, finance
  pattern approval Expense
    entity: Expense
    approver: manager
```

`sink` は `accepts` と `rejects` を同じ行に書ける。`net(*)` は同じ種類の sink すべてに適用される。アプリ側にルールがない sink は「public のみ」として扱う。エンティティ型のフィールド（`applicant: Employee`）は参照先のラベルも辿るため、リンク先の `pii` が報告から抜け落ちない。ラベルが1つも宣言されていないアプリでは、パターンの `data_policy` は能力宣言として扱い、検査をスキップする（既存アプリの互換性のため）。

### 10.2 マージ規則

アプリの `policy` とパターンの `data_policy` をマージする。

| 項目 | マージ |
|---|---|
| `labels` | 和集合。重複は許す。 |
| `sources` | 同じ source のラベルは和集合。 |
| `sinks.accepts` | 交差。両方が許可したラベルだけを許可する。 |
| `sinks.rejects` | 和集合。どちらかが拒否したら拒否する。 |
| `declassify` | 交差。両方に同じ `(label, scope, role)` がある場合だけ許可する。 |

パターンが要求する sink をアプリが許可しない場合、`E-POLICY`。パターンが `declassify` を要求し、アプリが許可しない場合も `E-POLICY`。

アプリの `policy` にその sink のルールがない場合、許可集合は空（public のみ）として扱う。パターンが `pii` を要求すれば交差は空になり、`E-POLICY` になる。これは fail closed の意図した動作である。

アプリ側にワイルドカード `net(*) accepts pii` があり、パターンが `net(email) accepts pii` を要求する場合、まずアプリ側の `resolve(net(email))` を計算してから交差する。解決後の許可集合が `{pii}` なら交差は `{pii}`、空なら `E-POLICY`。

### 10.3 `security.json`

`build` は次の形の `security.json` を出力する。

```json
{
  "schema_version": 1,
  "ast_hash": "bae8501e8b3f",
  "labels": ["internal", "pii", "secret"],
  "flows": [
    {
      "name": "receipt",
      "sources": ["read(customers)"],
      "sinks": ["net(email)"],
      "declassify": []
    }
  ],
  "sinks": {
    "net(email)": {"accepts": ["pii"], "rejects": ["secret"]},
    "emit(audit)": {"accepts": ["internal"], "rejects": []}
  },
  "declassify": [
    {"label": "pii", "in": "masked_email", "roles": ["support"]}
  ],
  "guarantees": [
    "no pii to net(analytics)",
    "no secret to net(*)"
  ],
  "unresolved": [
    "native op stripe.refund declares sends none; its implementation is trusted"
  ],
  "verified": true
}
```

`ast_hash` はパターン層のアプリ仕様（エンティティ、ラベル、パターン、policy）を正規化した SHA-256 の先頭12桁で、契約層の AST ハッシュとは別物である。`sinks` と `declassify` は設計時の schema への追加で、マージ後の許可・拒否をそのまま読めるようにした。`verified` は `E-POLICY` を含むエラーがないことを示す。警告（custom の未検査など）は `verified: true` のまま `unresolved` に残る。

`guarantees` は検査に成功した主張、`unresolved` は検査できず宣言を信頼している箇所。人間とAIの両方が、何が保証され、何が保証されていないかを読めるようにする。エラーがあるビルドでは `guarantees` は空になり、`verified` は `false` になる。

### 10.4 レポート表示

`report.html` に次を表示する。

- ラベルバッジ（`pii`、`secret`、`internal`）。
- sink ごとの許可ラベルと拒否ラベル。
- `declassify` の一覧（スコープ、ラベル、ロール、理由の有無）。
- `unresolved` の件数。0件なら「すべてのフローを静的検査済み」と表示する。

実装では、`unresolved` が1件以上なら件数と各項目を表示し、0件なら「すべてのフローを静的検査済み」と表示する。custom ブロックは実装を信頼するため、ラベル付きアプリでは `W-POLICY` の警告と `unresolved` の両方に残る。

## 11. 実装計画

### 11.0 実装状況（2026年11月）

v0.1 の静的検査は `etsah.py` に実装済みで、`tests/test_information_flow.py` の43件が通る。v0.2 の分岐と PC ラベルは `tests/test_flow_branches.py` の29件で確認する。パターン層の `data_policy` と `security.json` は `tests/test_data_policy.py` の25件で確認する。スイート全体は473件。`examples/information_flow_safe.etsah` はエラー0・警告1（`W-DECLASS`）、`examples/information_flow_leak.etsah` は `E-IFLOW-ARG` で失敗し、どちらも CI で確認している。`examples/refund_fixed.etsah` のハッシュは `bae8501e8b3f` のまま変わらない。

| 項目 | 状態 |
|---|---|
| `TypeRef` とラベル集合 | 実装済み |
| AST（`LabelDef`、`SinkRule`、`DeclassifyRule`、`PolicyDef`、`sends`） | 実装済み |
| パーサー（`label`、`policy`、`sends`、`@label`、`declassify`） | 実装済み |
| 式のラベル推論と引数・戻り値・契約の検査 | 実装済み |
| sink 解決と `sends` の必須条件 | 実装済み |
| `declassify` とポリシー検査 | 実装済み |
| `if` / `else` と PC ラベル（暗黙フロー） | 実装済み |
| パターン `data_policy` とアプリ `policy` のマージ | 実装済み |
| `security.json` とレポートの情報フロー表示 | 実装済み |
| AST ハッシュの後方互換 | 実装済み |
| サンプル2件と CI | 実装済み |
| `SPEC.md` / `README.md` | 実装済み |

### 11.1 v0.1（契約層の静的検査）

1. **型表現** — `class TypeRef(str)` を追加し、文字列としての基底型と `labels: frozenset[str]` を両立する。既存の `compatible` は基底型で比較する。`TypeRef` の `==` は基底型だけを比べるため、ラベルの比較には必ず `.labels` を使う。文字列操作（`re.sub` など）は `TypeRef` を通常の `str` に落としてラベルを失うため、型を組み立てる箇所では必ず `TypeRef` を作り直す。
2. **AST** — `LabelDef`, `SinkRule`, `DeclassifyRule`, `PolicyDef` を追加。`TypeDef` に不透明型のラベル、`OpDef` / `FlowDef` に `sends` を追加。`params` と `ret` は `TypeRef` にする。
3. **パーサー** — `label`、`policy`、`sends`、型の後置ラベル、`declassify` 式を追加。既存の型文字列は互換アダプタで `TypeRef` に変換する。
4. **チェッカー** — `check_labels`, `infer_labels`, `check_sends`, `check_declassify`, `check_sink` を追加。`type_of` の戻り値を `TypeRef` にし、式のラベルを推論する。ラベル宣言・`@` 注釈・`policy` がすべて空なら情報フロー検査をスキップする高速パスを入れる。
5. **ハッシュ** — 9章の追加キーを空のとき省略する形で実装。既存ハッシュを維持する。
6. **CLI** — `check --json` の `context` を追加。`ast` にラベル・ポリシー・`sends` を追加。
7. **テスト** — `tests/test_information_flow.py` を追加。既存133件はそのまま通す。
8. **サンプル** — `examples/information_flow_safe.etsah` と `examples/information_flow_leak.etsah` を追加。後者は CI で失敗することを確認する。
9. **ドキュメント** — `SPEC.md` に情報フローの節を追加。`README.md` からこの設計書へリンクする。

### 11.2 v0.2（分岐とパターン層）

- `if` / `else` を flow に追加し、PC ラベルを実装する。**実装済み**（2026年11月）。
- パターン manifest の `data_policy`、マージ、`security.json`、レポートバッジを実装する。**実装済み**（2026年11月）。
- `patterns/etsahc.py` の `build` に `security.json` を追加する。**実装済み**。
- パターンの `auth.roles` と `declassify.require role` を照合する。**未実装**。現在は `policy` のロール名を文字列として照合し、`auth.roles` との整合は別途検査していない。

### 11.3 v0.3（実行時強制）

- 生成コードに `Labeled<T>` を渡す。
- sink アダプタだけがラベルを検査して unwrap する。
- 能力ブローカーと組み合わせ、native 実装が直接ネットワーク・ファイルに触れないようにする。
- 監査ログに `declassify` の理由・実行者・時刻を残す。

### 11.4 テストマトリクス

| ルール | テスト名 | 期待 |
|---|---|---|
| 未宣言ラベル | `test_unknown_label` | `E-LABEL` |
| public の再定義 | `test_public_label_rejected` | `E-LABEL` |
| `@public` | `test_public_annotation_rejected` | `E-LABEL` |
| ラベルの重複 | `test_duplicate_label_on_type` | `E-LABEL` |
| レコードへの注釈 | `test_record_annotation_rejected` | `E-LABEL` |
| 型引数内のラベル | `test_label_in_type_argument_rejected` | `E-LABEL` |
| public → pii 引数 | `test_public_to_pii_ok` | OK |
| pii → public 引数 | `test_pii_to_public_argument` | `E-IFLOW-ARG` |
| secret → pii 引数 | `test_secret_to_pii_argument` | `E-IFLOW-ARG` |
| pii 戻り値 → public | `test_pii_return_to_public` | `E-IFLOW-RET` |
| public 戻り値 → pii | `test_public_return_to_pii_ok` | OK |
| pre に pii | `test_pre_uses_pii` | `E-IFLOW-CONTRACT` |
| post に pii | `test_post_uses_pii` | `E-IFLOW-CONTRACT` |
| sink が pii を許可 | `test_sink_accepts_pii` | OK |
| sink が secret を拒否 | `test_sink_rejects_secret` | `E-IFLOW-SINK` |
| wildcard reject は上書き不可 | `test_wildcard_reject_absolute` | `E-IFLOW-SINK` |
| `sends` の未宣言 effect | `test_sends_unknown_effect` | `E-SINK` |
| `sends` の未知引数 | `test_sends_unknown_parameter` | `E-SINK` |
| native の `sends` 不足 | `test_native_requires_sends` | `E-SINK-UNBOUND` |
| `sends none` | `test_native_sends_none_ok` | OK |
| read 後に sink する native | `test_native_read_then_sink_requires_sends` | `E-SINK-UNBOUND` |
| read 後に sink、`sends none` | `test_native_read_then_sink_sends_none_ok` | OK |
| 式実装の `sends` | `test_sends_on_expression_impl_rejected` | `E-SINK` |
| declassify 許可 | `test_declassify_allowed` | `W-DECLASS` |
| declassify 未許可 | `test_declassify_not_allowed` | `E-DECLASS` |
| 理由が空 | `test_declassify_empty_reason` | `E-DECLASS` |
| 理由に改行 | `test_declassify_newline_reason` | `E-DECLASS` |
| 対象ラベルなし | `test_declassify_absent_label` | `E-DECLASS` |
| レコードの declassify | `test_declassify_record_rejected` | `E-DECLASS` |
| 契約内の declassify | `test_declassify_in_contract_rejected` | `E-DECLASS` |
| 二項演算の和集合 | `test_binop_label_union` | `E-IFLOW-RET` |
| 比較のラベル | `test_comparison_labeled` | `E-IFLOW-RET` |
| フィールドアクセスの分離 | `test_field_access_narrow_label` | OK |
| レコード全体の和集合 | `test_record_union_label` | `E-IFLOW-SINK` |
| ラベルなしのハッシュ | `test_hash_unchanged_without_labels` | `bae8501e8b3f` |
| ラベル順序非依存 | `test_label_order_irrelevant` | 同一ハッシュ |
| ポリシー順序非依存 | `test_policy_order_irrelevant` | 同一ハッシュ |
| ラベル未使用の高速パス | `test_no_labels_skips_information_flow` | OK |
| 既存テスト | `tests/` 全体 | 231件成功 |

### 11.5 受け入れ条件

- `python3 -m unittest discover -s tests` が全件成功する。
- `python3 etsah.py check examples/information_flow_safe.etsah` がエラー0。
- `python3 etsah.py check examples/information_flow_leak.etsah` が `E-IFLOW-ARG` または `E-IFLOW-SINK` で失敗する。
- `examples/refund_fixed.etsah` のハッシュが `bae8501e8b3f` のまま。
- `human-view.html` の再生成差分がない。
- CI が成功する。

## 12. 限界

- ラベルは「宣言の正しさ」に依存する。機密フィールドのラベル漏れは検出できない。
- 静的なラベルだけでは、custom コードや native 実装の内部までは守れない。実行時の能力ブローカーと `Labeled<T>` が必要。
- 分岐は早期 `return` だけで、代入・ループ・例外がない。早期 `return` の後ろの文と、分岐中の sink は PC ラベルで検査する。分岐の後で変数を更新して合流する形は書けない。例外・タイミングによる漏洩は将来の課題。
- ラベルは暗号化ではない。`secret` とラベルしても、保存時・通信時の暗号化は別途必要。
- ネイティブ op の `sends` は宣言を信頼する。悪意ある native 実装は、ランタイムの能力制限でしか止められない。
- 完全な情報フロー制御は決定不能な部分を含む。Etsah は「高リスクなフローを止める実用的な型システム」を目指す。
- 型引数の中のラベルを追跡しないため、`List<String @pii>` のようなコレクションの要素ラベルは表現できない。v0.2 以降の課題。

## 13. 未決事項

設計を実装する前に、次の6点を決めたい。

1. **`@public` の禁止** — 現在は `E-LABEL`。`@public` を許して無視する方が移行しやすいか、禁止する方が安全か。
2. **レコードの `declassify` 禁止** — 現在は `E-DECLASS`。フィールド単位で外す方針でよいか。
3. **wildcard reject の絶対性** — `net(*) rejects secret` を `net(email) accepts secret` で上書きできない設計でよいか。上書きを許すなら、監査ログに「上書きした」ことを残す必要がある。
4. **ラベル名の大文字禁止** — `PII` のような大文字を許さない設計でよいか。既存の型名は大文字始まりなので、ラベルは小文字に統一する意図。
5. **`policy` と CLI の `--allow-net` の統合** — 現在は別系統。`policy` に `allow net(...)` を追加して一本化するか、デプロイ設定とデータポリシーを分けたままにするか。
6. **パターンの `data_policy` を必須にするか** — 現在は任意で、ない場合は public のみ。ラベル付きデータを扱うパターンには必須にした方がよいか。

1〜5 は v0.1 の実装で今の挙動（`@public` は `E-LABEL`、レコードの `declassify` は `E-DECLASS`、wildcard reject は上書き不可、ラベル名は小文字のみ）を採用した。6 の `data_policy` は v0.2 でパターン層に入れ、ラベル付きアプリで `data_policy` のないパターンを `E-POLICY` にする fail closed を採用した。`policy` と `--allow-net` の統合は未決のままで、現在は「データのラベル」と「ネットワーク先の許可」を別々に検査している。
