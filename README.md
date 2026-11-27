# Etsah

**Contracts for AI agents: declare effects and approvals, catch unsafe automations before they run.**

[日本語](README.ja.md) · [Spec (English)](SPEC.en.md) · [Spec (Japanese)](SPEC.md)

[![MIT license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![ci](https://github.com/RyujiHashimoto/etsah/actions/workflows/ci.yml/badge.svg)](https://github.com/RyujiHashimoto/etsah/actions/workflows/ci.yml)
[![playground](https://img.shields.io/badge/playground-try%20it-2f6f4e)](docs/playground/index.html)

It stops an agent from emailing a customer, charging a card, changing a contract, or calling a host that is not allowlisted, before any of those calls run. An AI writes the specification. A separate checker verifies it. A human reads the summary and the GUI.

Author: Ryuji Hashimoto (J-PROUT Inc.)

![etsah check fails with E-POLICY, then passes after the task names an approval gate](docs/check-demo.svg)

```etsah
# before — E-POLICY, the send has no approval gate
task draft_reply(ticket_id: Id) -> Bool
  eff read(tickets), emit(mail)

# after — ask support_lead, then send
task draft_reply(ticket_id: Id) -> Bool
  eff read(tickets), emit(mail)
  gate approve_reply
```

[Try it in your browser](docs/playground/index.html). The page is static files under `docs/playground/`. It goes live once GitHub Pages is enabled for this repository, with `/docs` as the site source. This repository does not enable Pages by itself.

## Quickstart

Python 3.12 and the standard library. No extra packages. The package is not on PyPI. Clone the repository and install that checkout.

```bash
git clone https://github.com/RyujiHashimoto/etsah.git
cd etsah
python3 -m venv .venv
.venv/bin/python -m pip install .
```

On Debian and Ubuntu, `python3 -m venv` needs the `python3.12-venv` package. On Windows, the programs are `.venv\Scripts\python` and `.venv\Scripts\etsah` (and `.venv\Scripts\etsahc`, `.venv\Scripts\etsah-mcp`).

The install provides `etsah`, `etsahc`, and `etsah-mcp`. Unless a block is labeled Windows, the commands below use the Linux and macOS paths. Run them from the clone. A fresh install ends with:

```text
Successfully installed prout-lang-0.1.0
```

`pip install .` may download the build backend (setuptools) into a temporary build environment. The installed program has no third-party dependencies.

### A first check

`examples/refund_fixed.etsah` is a repaired refund for a fictional order. The checker reads the source. It does not call a payment API.

```bash
.venv/bin/etsah check examples/refund_fixed.etsah
```

```text
Etsah check: examples/refund_fixed.etsah
AST hash: bae8501e8b3f
[WARNING] W-PRE (line 45): precondition of refund needs a runtime guard: (order.status == "paid")

0 error(s), 1 warning(s)
```

The exit code is 0. The warning names a runtime guard.

### An agent policy

`examples/agents/support_reply.etsah` is a fictional support desk (Harbor Lane Support, ticket T-1042). The task may read the ticket. Emailing the customer is `emit(mail)`, marked external, and the task names `gate approve_reply`. The checker reads the source. It does not send mail.

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

The exit code is 0.

`check --json` prints one object. `diagnostics` is empty, and `manifest.signature` is `null`.

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

The exit code is 0.

`examples/agents/support_reply_broken.etsah` is the same desk with the gate left off the task. That check exits 1. The diagnostic line is:

```text
[ERROR] E-POLICY (line 35): task draft_reply: emit to an external party has no approval gate (emit(mail))
```

### Windows

CI runs on `ubuntu-latest` only. These Windows lines are the path translation of the Linux block above. They are not a recorded run.

Command Prompt:

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

### Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

Tests use the standard library's `unittest`. A passing run ends with `OK` and exits 0.

`tests/` is not a package, so a name that starts with `tests.` never puts that directory on the module path and the import of `support` fails. Run one class with `tests` on `PYTHONPATH`:

```bash
PYTHONPATH=tests python3 -m unittest test_checker.TestCli
```

### Pattern build and the MCP server

```bash
.venv/bin/etsahc build patterns/examples/expense.etsah --out patterns/build/expense
```

```text
Etsah Patterns: expense
stack: nextjs, postgres  freedom: guided
entities: 2  patterns: 3  custom: 1
spec lines: 28  generated lines: 1,264  expansion: 45.1x
OK — no issues
wrote patterns/build/expense/report.html
wrote patterns/build/expense/demo.html
wrote patterns/build/expense/manifest.json
```

The command expands a 28-line expense spec and writes `patterns/build/expense/demo.html`. Open that file in a browser. No server. Submit, approve, send back, and reject. The approved step runs only after approval. `patterns/build/` is gitignored.

```bash
.venv/bin/etsah-mcp --help
```

```text
usage: etsah-mcp [mcp]
       etsah-mcp guard --contract FILE [--agent NAME] [--allow-net HOST] [--deny-net HOST] [--approval-key-file FILE] [--nonce-file FILE] [--approval-token TOKEN] [--allow-legacy-approval-token] -- UPSTREAM...
       etsah-mcp approve --contract FILE --gate NAME --op NAME --approver ID (--args JSON | --args-file FILE) (--ttl SECONDS | --expires-at UNIX)
```

`etsah-mcp` with no arguments is the stdio server. It reads newline-delimited JSON-RPC and returns when stdin closes.

Five-minute tour: [docs/en/agents-quickstart.md](docs/en/agents-quickstart.md). How this differs from a Cedar or OPA policy plus a wrapper: [docs/en/comparison.md](docs/en/comparison.md).

## Check a pull request

The composite action at the root of this repository checks `.etsah` files a pull request changes. Each diagnostic is a GitHub annotation (`::error file=...,line=...::`). An error fails the job. A warning does not. It uses the Python standard library and the checker in this repository. Tags are not published. Pin `@main`.

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

`glob` replaces the file set. `scope` is `pull-request` (the default) or `all`. On an event other than a pull request, the default scope checks every match. `allow-net` and `deny-net` are comma-separated host names and are passed through to the checker. This repository's CI calls the action with `uses: ./` on a passing file and on a broken file.

## Renamed from prout

The language is Etsah. For one minor version, the old commands `prout`, `proutc`, and `prout-mcp` still run and write `新名称は etsah` to stderr. `.prout` files still check. `import prout` and the other old module names re-export the `etsah` modules. `PROUT_*` environment variables are still read when the matching `ETSAH_*` variable is unset, and approval tokens that start with `prout1.` still verify. The repository URL is https://github.com/RyujiHashimoto/etsah.

## What the checker enforces

| Bound | On this tree |
|---|---|
| Effects (`read`, `write`, `net`, `spend`, `emit`) match the operations a flow or task calls | `E-EFF` |
| A task's effects stay inside its agent's `allow` list | `E-POLICY` |
| External `emit` / `net`, every `spend`, and a `write` marked as a contract change name a human `gate` | `E-POLICY` |
| Network targets sit on the allow list (default `stripe`; override with `--allow-net`). A host given with `--deny-net` is rejected even when `--allow-net` names it too (`examples/lantern_status.etsah`). | `E-POLICY` |
| Spend budgets are integer yen, and per-run does not exceed the period. Hourly `net` and `emit` limits are static | Declared and checked. Those running totals are not counted. `limit calls` and `limit spend` are counted by `etsah guard` |
| `audit ast_hash` on the agent | Required. The full id is 64 hex digits. `etsah check` prints the 12-digit prefix |
| Preconditions in linear arithmetic, including `Decimal` and calendar `Date` | False is `E-PRE`. Unproved is a visible `W-PRE` guard |
| Information-flow labels (`pii`, `secret`) when the program declares them | `E-IFLOW-*`, `E-DECLASS`. Programs with no labels keep their old AST hash |

The checker does not run cron, store the remaining integer-yen budget, or store idempotency keys. A gate stops the static check. It does not send the approval email. `impl` may be Python, Rust, or SQL. Etsah records what that implementation may read, write, charge, and promise.

`etsah check --json` prints one object. `hash` is 12 hex digits. `diagnostics` each have `code`, `severity`, `line`, and `message`, and `fix`, `detail`, or `context` only when set. A file with tasks also has `audit_id` (64 hex digits) and `plan`. `manifest` lists each agent's allow list, budgets, limits, and named gates. `manifest.signature` is `null`. Exit 0 means no errors, warnings included. Exit 1 means at least one error. Exit 2 means the file did not parse. The same rules are in [SPEC.md](SPEC.md) and [SPEC.en.md](SPEC.en.md).

Full rules: [SPEC.md](SPEC.md) (Japanese, the original) and [SPEC.en.md](SPEC.en.md).

## Contract layer

A repaired refund, abbreviated from [`examples/refund_fixed.etsah`](examples/refund_fixed.etsah). The flow's effects must equal the effects of the operations it calls.

```etsah
op refund(order: Order, amount: Money) -> Receipt
  doc "注文を指定額だけ返金する"
  eff write(payments), net(stripe)
  pre amount <= order.total
  pre order.status == "paid"
  post result.status == "refunded"
  impl stripe.refund(order, amount)

flow handle_refund(order_id: Id) -> Receipt
  eff read(orders), write(payments), net(stripe)
  let order = orders.get(order_id)
  let receipt = refund(order, order.total)
  return receipt
```

`python3 etsah.py check examples/refund_fixed.etsah` exits 0 with one runtime guard (`W-PRE`: `order.status == "paid"`) and AST hash `bae8501e8b3f`. `python3 render.py` rebuilds [`human-view.html`](human-view.html) from the AST.

![Human view generated from the AST](docs/human-view.png)

An agent envelope, abbreviated from [`examples/agents/support_reply.etsah`](examples/agents/support_reply.etsah):

```etsah
policy
  external emit(mail)

agent support_reply
  allow read(tickets), emit(mail)
  budget run 0 period 0
  limit emit 20 hour
  audit ast_hash

gate approve_reply
  approver support_lead
  before emit(mail)
  on_approved mail.send_reply

task draft_reply(ticket_id: Id) -> Bool
  agent support_reply
  eff read(tickets), emit(mail)
  gate approve_reply
  key ticket_id
  retry 2 escalate support_lead
  let ticket = tickets.read(ticket_id)
  let sent = mail.send_reply(ticket)
  return sent
```

`budget run 0` is legal here because this agent is not allowed to `spend`. An agent that may `spend` needs a positive integer-yen budget. Design notes: [docs/ai_automation.md](docs/ai_automation.md).

## MCP

The same check is an MCP server. It speaks stdio and uses only the standard library. There is no `mcp` package to install.

```bash
python3 etsah.py mcp
python3 etsah.py import mcp examples/agents/tools/mcp_tools.json
python3 etsah.py guard --contract examples/agents/support_reply.etsah -- python3 path/to/upstream_server.py
```

`etsah_check`, `etsah_import_tools`, `etsah_explain`, and `etsah_check_plan` are read-only. `etsah guard` forwards `tools/list` to an upstream server and applies the contract before each `tools/call`. `etsah approve` issues a signed, expiring, one-time approval; `ASK` is forwarded only when that approval matches the contract, the gate, the tool, and the argument hash. `limit calls` and `limit spend` are counted in a ledger file, and a signed approval does not raise those caps. Setup: [docs/en/mcp.md](docs/en/mcp.md) and [docs/mcp.ja.md](docs/mcp.ja.md).

## Prompt injection

A fictional inquiry, Q-4401 from `guest@example.invalid`, tells Harbor Lane Desk to ignore its rules, forward a vault token, and refund 9000.00 JPY. Two contracts show the same story.

The leaking contract lets the secret token reach `mail.forward`. The check rejects it:

```bash
python3 etsah.py check examples/prompt_injection_leak.etsah
```

Expected: exit 1, `E-IFLOW-ARG` at `mail.forward` (`argument 'note' of mail.forward expects no labels, got secret`).

The safe contract keeps the token inside the vault and answers with a fixed public sentence. External mail and the refund wait for `desk_lead`. The refund is a decimal in JPY and is capped at 1000.00 per day.

```bash
python3 etsah.py check examples/prompt_injection_safe.etsah
```

Expected: exit 0, no errors.

Against that safe contract, `etsah guard` treats the calls as follows.

| Call | Verdict | Why |
|---|---|---|
| `inbox.read` | `ALLOW` | A read inside the agent allow list |
| `mail.reply` | `ASK` | External `emit(mail)` waits for `desk_lead` |
| `mail.forward` | `BLOCK` | The op is not in the contract |
| `payments.refund` of `100.00` with a matching signed approval | forwarded | Under the 1000.00 JPY daily cap |
| `payments.refund` of `9000.00` with a matching signed approval | `BLOCK` | The signature fits the call, and the cap still rejects it |

The approval is issued with `etsah approve` for that op, gate `approve_reply`, approver `desk_lead`, and the same argument object the client will send. The guard needs the same HMAC key, a nonce file, and `--ledger-file`.

## Pattern layer

App specs fill slots. The compiler checks them and estimates database, API, UI, and test files. It does not yet write those source files.

```bash
python3 patterns/etsahc.py find "approval"
python3 patterns/etsahc.py find "申請 承認"
```

`find "申請 承認"` returns `approval`. The registry has 27 manifests, including `overtime_agreement`, `ai_task`, and `approval_gate`. Patterns are JSON, so another agent can discover one from [`patterns/registry/index.json`](patterns/registry/index.json) without reading the compiler.

[`patterns/bench/report.json`](patterns/bench/report.json) compares specs before and after `approval` existed. Reuse is the generated-line estimate divided by AI-written lines (spec plus custom).

| Case | Spec | Custom | AI-written lines | Generated estimate | Reuse |
|---|---:|---:|---:|---:|---:|
| Expense, before `approval` | 24 | 180 | 204 | 981 | 4.8× |
| Expense, after `approval` | 28 | 30 | 58 | 1,264 | 21.8× |
| Travel request, `approval` reused | 33 | 0 | 33 | 1,386 | 42.0× |
| Equipment purchase, `approval` reused | 34 | 0 | 34 | 1,394 | 41.0× |
| PC lending (bench only) | 28 | 35 | 63 | 1,269 | 20.1× |

The longer trial write-up is in [README.ja.md](README.ja.md).

## Industry example: a staffing desk

[`patterns/examples/staffing.etsah`](patterns/examples/staffing.etsah) is one industry, not the center of the language. It is a fictional staffing desk: staff, openings, a dispatch contract, a timesheet the client site must approve, a 36-agreement whose limits are slots, and a bill rate at or above the pay rate. Omitted agreement limits default to the general statutory ceilings, and a higher cap is an error. The conflict date is both the date the client notified and a date calculated from the earliest dispatch start at the same office and for the same person and organizational unit. The compiler reads the spec only. Live hours and stored contracts are described as runtime checks and are not evaluated. Social-insurance rates and equal-pay determinations are out of scope.

```bash
python3 patterns/etsahc.py build patterns/examples/staffing.etsah --out patterns/build/staffing
```

Design and limits: [docs/staffing.md](docs/staffing.md). The contract layer can also write those dates and decimal rates directly. See `examples/staffing_dates.etsah`. Copy-and-fill templates for a timesheet, an assignment, and a bill rate, with one fictional filled example each, are in [examples/templates/README.md](examples/templates/README.md).

## Features

- Contract language: types, `op`, `flow`, `agent`, `task`, `gate`, effects, `pre` / `post`, `impl`.
- Effect completeness, network allow list, human gates for external mail, payment, and contract changes, integer-yen budgets, hourly rate limits, retry bounds, and a required audit hash.
- `Decimal` and calendar `Date` in preconditions. `1/2` is still not given one meaning.
- Information-flow labels, including implicit flow through `if` / `else` branches. A condition's labels become the program-counter label of the values returned inside it, of the statements that follow a branch which can return, and of the sinks those statements run. Programs that do not use labels keep their previous AST hash.
- Diagnostics an agent can repair from: code, location, expected, actual, suggestion.
- Human HTML from the AST (`render.py`) and a static browser playground (`docs/playground/`).
- Import of MCP and OpenAI tool lists into `op` declarations (`etsah import`).
- MCP server and a stdio guard (`etsah mcp`, `etsah guard`, `etsah-mcp`).
- 27 pattern manifests, `find`, and an approval `demo.html`.
- CI on GitHub Actions.

## Status

Prototype. No release tag. Not published to PyPI. `build` emits a report, a demo, and a manifest. It does not emit API or database source. Cron does not run, budget counters are not stored, and there is no idempotency store. Branches are early returns only; there is no assignment, loop, or exception.

## Documentation

| Doc | Language | Contents |
|---|---|---|
| [SPEC.md](SPEC.md) | Japanese, original | Contract language, v0.6 |
| [SPEC.en.md](SPEC.en.md) | English translation | Same document. If they disagree, `SPEC.md` wins |
| [docs/en/agents-quickstart.md](docs/en/agents-quickstart.md) | English | Five-minute agent check |
| [docs/en/comparison.md](docs/en/comparison.md) | English | Cedar, OPA, guardrails, effect systems |
| [docs/en/mcp.md](docs/en/mcp.md) | English | MCP server and guard |
| [docs/mcp.ja.md](docs/mcp.ja.md) | Japanese | MCP server and guard |
| [docs/diagnostics.md](docs/diagnostics.md) | English | Every diagnostic code the contract and pattern checkers emit, with its layer and meaning. If it disagrees with `SPEC.md`, `SPEC.md` wins |
| [docs/playground/index.html](docs/playground/index.html) | Browser | Static checker. Needs GitHub Pages to be public |
| [docs/ai_automation.md](docs/ai_automation.md) | Japanese | Agent and task design |
| [docs/information-flow.md](docs/information-flow.md) | Japanese | Label design and current limits |
| [docs/staffing.md](docs/staffing.md) | English summary, then Japanese | Staffing example |
| [patterns/PATTERNS.md](patterns/PATTERNS.md) | Japanese | Pattern language |
| [CONTRIBUTING.md](CONTRIBUTING.md) | English, short Japanese section | Patterns, review, tests |
| [SECURITY.md](SECURITY.md) | English | How to report a vulnerability |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | English | Contributor Covenant 2.1 |
| [CHANGELOG.md](CHANGELOG.md) | English | Unreleased notes |
| [docs/launch-checklist.md](docs/launch-checklist.md) | Japanese | Owner decisions before a public launch |

## Repository

```
action.yml               composite action for pull-request checks
scripts/etsah_action.py  GitHub annotations for that action
etsah.py                 contract parser, checker, CLI
etsah_api.py             check_source and check_file
etsah_import.py          MCP and OpenAI tool lists become ops
etsah_mcp/               MCP server (etsah mcp) and guard (etsah guard)
render.py                AST to human HTML
patterns/etsahc.py       pattern compiler (build, find, add, index, bench)
patterns/registry/       27 manifests and index.json
examples/agents/         agent contracts, including broken ones
docs/playground/         browser checker (static, Pyodide)
```

## Contributing

New patterns are JSON in `patterns/registry/`. Human review is required, including when an agent wrote the patch. See [CONTRIBUTING.md](CONTRIBUTING.md). Agents changing this repository should start with [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE). Copyright (c) 2026 Ryuji Hashimoto (J-PROUT Inc.).
