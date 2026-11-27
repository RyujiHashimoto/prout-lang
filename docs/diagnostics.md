# Diagnostic codes

This page lists the diagnostic codes the checkers already emit. It does not change checker behavior. If a sentence here and [SPEC.md](../SPEC.md) disagree, `SPEC.md` wins.

The contract checker is `etsah.py`. The pattern checker is `patterns/etsahc.py`. `E-DUP`, `E-TYPE`, and `E-POLICY` are emitted by both, so each of those codes has one row.

| Code | Layer | Meaning |
|---|---|---|
| `E-PARSE` | contract | The source is not a valid program, and the checker stops at the first syntax error (`tests/test_checker.py`). |
| `E-LABEL` | contract | A label name is reserved, duplicated, malformed, or written where an annotation is not allowed (`tests/test_information_flow.py`). |
| `E-DUP` | both | The contract checker and the pattern checker both reject a name or an effect that is declared twice. |
| `E-TYPE` | both | A type, operator, argument, field, or return value does not match its declaration (`examples/refund_broken.etsah`). |
| `E-UNDEF` | contract | A variable, operation, or agent is used but not declared (`tests/test_checker.py`). |
| `E-FIELD` | contract | A type has no field with that name (`tests/test_checker.py`). |
| `E-ARITY` | contract | A call passes a different number of arguments than the operation declares (`tests/test_checker.py`). |
| `E-DECLASS` | contract | `declassify` is missing an argument, names a label the policy does not allow in this place, or is used on a whole record or inside `pre` or `post` (`tests/test_information_flow.py`). |
| `W-DECLASS` | contract | An allowed `declassify` is recorded so a person can review it (`examples/information_flow_safe.etsah`). |
| `E-EFF` | contract | The declared effects are not the effects the body performs, or an effect kind is unknown (`examples/refund_broken.etsah`). |
| `E-FLOW` | contract | A flow or task does not end in a return, or a statement comes after the return. |
| `E-PRE` | contract | A precondition is false at a call site the checker could evaluate (`examples/refund_broken.etsah`). |
| `W-PRE` | contract | The checker could not prove a precondition, so the call still needs a runtime guard (`tests/test_checker.py`). |
| `E-IFLOW-CONTRACT` | contract | A precondition or postcondition reads a labeled value (`tests/test_information_flow.py`). |
| `E-IFLOW-RET` | contract | A returned value carries a label the declared return type does not (`tests/test_information_flow.py`). |
| `E-IFLOW-ARG` | contract | An argument carries a label the parameter does not accept (`examples/prompt_injection_leak.etsah`). |
| `E-IFLOW-SINK` | contract | A labeled value reaches a sink that rejects that label or does not accept it (`tests/test_information_flow.py`). |
| `E-SINK` | contract | A `sends` clause is not on a native operation, or it names an unknown parameter, a `read`, or an effect the operation does not declare (`tests/test_information_flow.py`). |
| `E-SINK-UNBOUND` | contract | A native operation moves labeled data and does not say which argument reaches an effect (`tests/test_information_flow.py`). |
| `E-POLICY` | both | A contract allow list, gate, network host, budget, or rate limit is broken; a pattern manifest policy fails; or a pattern `data_policy` requires a label the app policy does not allow (`patterns/examples/data_policy_leak.etsah`). |
| `W-BUDGET` | contract | A task spends money and no `limit spend` gives that spend a decimal amount (`tests/test_ledger.py`). |
| `E-ENTITY` | pattern | An entity has no fields, or it is missing a field a pattern needs. |
| `E-TARGET` | pattern | A pattern names no entity, or the entity or slot target is not declared. |
| `E-SLOT` | pattern | A required slot is missing, or its value is not one the pattern accepts. |
| `W-POLICY` | pattern | A pattern policy warning: an exempt slot is set and the message is not a legal opinion, or a labeled app contains a custom block whose implementation is trusted. |
| `E-HOOK` | pattern | A hook points at a missing target, an ambiguous target, or a target that was not applied. |
| `E-STACK` | pattern | The specification declares no stack target. |
| `E-FREEDOM` | pattern | `freedom` is not `strict`, `guided`, or `open`, or strict mode contains a custom block. |
| `E-PATTERN` | pattern | The pattern name is not in the registry. |
| `W-SLOT` | pattern | A slot name is not part of the pattern, so the checker ignored it. |
| `E-CONFLICT` | pattern | The same pattern is applied twice where only one application is allowed. |
| `E-CUSTOM` | pattern | A custom block is missing a positive `lines` estimate or, in guided mode, a `why`. |
| `E-SCENE` | pattern | A scene has no unique id, or a choice points at a scene that is not declared. |
| `E-HOOK-ARG` | pattern | A hook argument does not match the target slot or the roles declared by `auth`. |
