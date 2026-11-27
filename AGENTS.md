# AGENTS.md

Etsah is a **contract language (a DSL) for AI agents**. You declare
effects (`read`, `write`, `net`, `spend`, `emit`), budgets, and approval
gates; a deterministic checker rejects unsafe automations before they
run. It is a language with a small toolchain around it, not a
general-purpose programming language and not a framework.

Author: Ryuji Hashimoto (J-PROUT Inc.)
License: MIT (see `LICENSE`)
Repository: https://github.com/RyujiHashimoto/etsah

The language is Etsah. For one minor version, `prout`, `proutc`, and `prout-mcp` still run and write a deprecation warning to stderr. `.prout` files still check. `import prout` and the other old module names re-export the `etsah` modules. The repository URL stays https://github.com/RyujiHashimoto/etsah.

`python3 -m unittest discover -s tests` reports 473 tests, all passing, on Python 3.12.

`action.yml` is a composite action. Another repository calls it with `uses: RyujiHashimoto/etsah@main` after `actions/checkout`. Tags are not published. The `verify` job calls the same action with `uses: ./` on a passing file and on `examples/ai_tasks_broken.etsah`.

## Build, test, and run

Python 3.12, standard library only. `pyproject.toml` lists no
third-party dependencies. Nothing is published to PyPI. CI is the
`verify` job in `.github/workflows/ci.yml` and uses Python 3.12.
That job also creates a virtualenv outside the checkout, runs
`pip install .`, and smoke-tests `etsah`, `etsahc`, `etsah-mcp`,
`prout`, `proutc`, and `prout-mcp` from that install. The legacy
commands must warn on stderr.

```bash
git clone https://github.com/RyujiHashimoto/etsah.git
cd etsah

python3 -m unittest discover -s tests

python3 etsah.py check examples/refund_fixed.etsah
python3 etsah.py check examples/ai_tasks.etsah
python3 etsah.py check examples/agents/support_reply.etsah
python3 etsah.py check examples/information_flow_safe.etsah \
  --allow-net email --allow-net analytics

python3 etsah.py ast examples/refund_fixed.etsah

python3 etsah.py import mcp examples/agents/tools/mcp_tools.json
python3 etsah.py import openai examples/agents/tools/openai_tools.json

python3 render.py
```

`check` accepts `--allow-net` (repeatable), `--deny-net` (repeatable),
and `--json`. A file with no errors exits 0, including when the only
findings are warnings. A file with errors exits 1. A parse failure
exits 2. `import` also exits 2 when the tool list cannot be read.

Subcommands of `python3 etsah.py` are `check`, `ast`, `import`
(`mcp` or `openai`), `mcp`, `guard`, and `approve`.

`python3 etsah.py mcp` is the stdio MCP server. It speaks
newline-delimited JSON-RPC and returns when stdin closes. The same
server is the `etsah-mcp` console script after a local install. Tool
names and the guard are documented in `docs/en/mcp.md` and
`docs/mcp.ja.md`.

`python3 etsah.py approve` issues one signed, expiring, one-time
approval for an `ASK` tool call. The HMAC key comes from the
environment (`ETSAH_APPROVAL_KEY`) or from `--approval-key-file`. Do
not put a key on the command line, and do not commit a key. Details
are in `docs/en/mcp.md`.

`etsah guard` counts `limit calls` and `limit spend` in
`ETSAH_LEDGER_FILE` or `--ledger-file`. A call past the cap is blocked
even when a signed approval is present. The integer-yen `budget` is
still only a static ceiling.

`python3 render.py` rewrites `human-view.html`. Do not edit that file
by hand. CI fails if the committed file drifts.

Pattern layer, from `patterns/` (this is also what CI runs):

```bash
cd patterns
python3 etsahc.py index
python3 etsahc.py find "申請 承認"
python3 etsahc.py build examples/shop.etsah
python3 etsahc.py build examples/expense.etsah
python3 etsahc.py build examples/ai_automation.etsah
python3 etsahc.py build examples/data_policy.etsah
python3 etsahc.py build examples/data_policy_leak.etsah  # exits 1 with E-POLICY
python3 etsahc.py bench
python3 growth_report.py
```

`etsahc.py` subcommands are `build`, `find`, `add`, `index`, and
`bench`. After `index`, `registry/index.json` must match the manifests;
CI fails if that file is stale. Generated files under `patterns/build/`
are gitignored. The registry has 27 manifests. `build` writes
`report.html`, `manifest.json`, and `security.json`; the security file
records labels, flows, merged sinks, guarantees, unresolved items, and
whether policy checks passed.

Optional local install from the repository root. This does not upload
the package:

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/etsah check examples/agents/support_reply.etsah
```

`pyproject.toml` also declares `etsahc` and `etsah-mcp`. `etsah-mcp` is
the stdio server and returns when stdin closes. Installed `etsahc`
imports `staffing_rules` from the package. `python3 etsahc.py` from
`patterns/` still imports that module as a neighbor. `proutc` remains
for one minor version and warns.

How to add a pattern, the review bar, and versioning are in
[CONTRIBUTING.md](CONTRIBUTING.md). Vulnerability reports follow
[SECURITY.md](SECURITY.md). Conduct follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Branch and pull request workflow

- Work on a topic branch and open a pull request into `main`.
- Do not commit directly to `main`.
- Do not force-push.
- Do not delete branches, and do not delete the repository.
- Wait for the `verify` job to pass. If your branch conflicts with
  `main`, merge `main` into your branch and fix the conflict. Do not
  force-push over history.
- Human review is required, including for AI-authored pull requests.
  Use `.github/PULL_REQUEST_TEMPLATE.md`.

## Project rules

1. **Pure Python, standard library only.** Do not add a third-party
   package, a `requirements.txt`, or a new runtime dependency.
2. **MIT license.** Contributions are accepted under the MIT license.
   Credit the author only as **Ryuji Hashimoto (J-PROUT Inc.)**. Do not
   add personal contact details, phone numbers, or private addresses.
3. **Do not refactor the validator in `etsah.py`.** Do not rewrite or
   restructure the checker, parser, or lexer for cleanliness. Keep
   changes to `etsah.py` as small as the task requires. If a change
   alters the meaning of syntax, types, effects, or preconditions,
   update `SPEC.md` and `SPEC.en.md` in the same pull request. A change
   that makes existing `.etsah` files fail is a major change.
4. **Sample data is fictional.** Examples, tests, and screenshots must
   use invented names, tickets, amounts, and dates. Do not commit real
   customer data, real personal data, or live credentials.
5. **Do not publish.** Do not register or reserve names, buy domains,
   upload to PyPI, push a release tag, list the project in an MCP
   registry or an "awesome" list, or post an announcement, unless
   Ryuji Hashimoto (J-PROUT Inc.) has approved that exact action.

## Coding style

- Python: 4-space indent, `from __future__ import annotations`, type
  hints on new functions, `dataclass` for structured values, and
  stdlib `unittest`. Tests must run under
  `python3 -m unittest discover -s tests` with no extra packages.
- Contract and pattern source: **2-space indent**, as in
  `examples/ai_tasks.etsah` and the other files under `examples/` and
  `patterns/examples/`.
- Match the surrounding file. Prefer a new test or example over a
  comment that restates the code.
- Reuse a diagnostic code that already exists in `etsah.py` and
  `SPEC.md` when the meaning matches (`E-POLICY`, `E-EFF`, `E-PRE`,
  `E-TYPE`, `E-FLOW`, `E-IFLOW-ARG`, `E-IFLOW-CONTRACT`, `E-IFLOW-RET`,
  `E-IFLOW-SINK`, and the others defined there). Do not invent a synonym.

## Adding an example

1. Add a pair when you are showing a check: a broken file that must
   fail, and a fixed file that must pass, following
   `examples/refund_broken.etsah` / `examples/refund_fixed.etsah` and
   `examples/agents/support_reply_broken.etsah` /
   `examples/agents/support_reply.etsah`.
2. Use the `.etsah` extension.
3. Keep the data fictional. Stay inside the documented effects:
   `read`, `write`, `net`, `spend`, `emit`.
4. Wire the new files into CI if they are part of the public contract
   (a passing `check`, or a broken file whose diagnostic text is
   asserted), the same way `.github/workflows/ci.yml` already does.
5. Do not hand-edit `human-view.html`. Regenerate it with
   `python3 render.py` when a change should show up there.

## Adding a test

1. Put cases in `tests/test_*.py`. Shared helpers live in
   `tests/support.py` (`snippet`, `parse`, `check`, `codes`).
2. Name each test after the behaviour it pins down.
3. For contract snippets, indent the program with the Python test and
   let `snippet()` dedent it. The language itself keeps 2-space indent.
4. Run:

   ```bash
   python3 -m unittest discover -s tests
   ```

5. If you change a pattern manifest, also run `python3 etsahc.py index`
   from `patterns/` and include the updated `registry/index.json`.

## Adding a pattern

Patterns are JSON manifests in `patterns/registry/`, not code. Follow
[CONTRIBUTING.md](CONTRIBUTING.md): add `patterns/registry/<name>.json`,
regenerate `index.json`, and make sure `python3 etsahc.py build` and
`python3 etsahc.py bench` still pass. Add a pattern only when the same
shape has appeared at least twice. Review checks (intent, slots,
effects, determinism, examples, bench, hook arguments) are in
`CONTRIBUTING.md`.

## Contributing as an AI

This section is the AI-facing counterpart to `CONTRIBUTING.md`. There
is no separate `CONTRIBUTING-AI.md`. Read this file and `llms.txt`
before changing code. Follow the links in `llms.txt` only as far as
the task requires.

- Open a branch and a pull request, as above. In the pull request body,
  include: the model you used and the time you generated the change
  (timezone Asia/Tokyo); the exact commands you ran and their output
  (`python3 -m unittest discover -s tests`, plus `etsahc.py build` and
  `bench` when you touch the pattern layer); what you changed and why;
  and any part you left as `custom` rather than a new pattern.
- Do not start a broad cleanup of `etsah.py`. If you are unsure whether
  a change is a refactor of the validator, stop and describe the change
  in the pull request instead of applying it.
- AI contributors are welcome and stand on equal footing with human
  contributors. Ryuji Hashimoto (J-PROUT Inc.) makes the final decisions.
- Do not submit this project to an MCP registry, an "awesome" list, a
  package index, or a public announcement from a contribution.

## On this tree, and not on this tree

Present on `main`:

- `pyproject.toml`. After a local `pip install .`, `etsah` and
  `etsah-mcp` run. Run the pattern compiler as `python3 etsahc.py`
  from `patterns/` (see above)
- An MCP stdio server (`python3 etsah.py mcp`), a guard
  (`python3 etsah.py guard`), and signed one-time approvals
  (`python3 etsah.py approve`)
- A static browser playground at `docs/playground/index.html`
- 27 pattern manifests under `patterns/registry/`

Not on this tree, and not something to invent in a drive-by pull request:

- A release tag or a PyPI package
- An MCP registry listing or any other public announcement
- GitHub Pages configuration (the playground files are in the repo; this
  repository does not enable Pages by itself)
