#!/usr/bin/env python3
"""
Etsah Patterns v0.2 — a growing pattern language prototype.

Patterns are data, not code. The compiler loads every manifest in registry/,
validates app specs against the manifests, and expands each pattern into a
deterministic set of database, API, UI, and test artifacts.

Commands:
    build   validate and expand an app spec, and write security.json
    find    search the registry by intent, tag, name, or slot
    add     validate a manifest and add it to the registry
    index   rebuild registry/index.json for AI discovery
    bench   compare apps with and without a reusable pattern
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, Union

try:
    from . import staffing_rules
except ImportError:
    # `python3 etsahc.py` from this directory has no package parent.
    import staffing_rules

ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "registry"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@dataclass
class Diagnostic:
    code: str
    message: str
    line: int = 0
    severity: str = "error"

    def render(self) -> str:
        loc = f"line {self.line}" if self.line else "no line"
        return f"[{self.severity.upper()}] {self.code} ({loc}): {self.message}"


# ---------------------------------------------------------------------------
# Source model
# ---------------------------------------------------------------------------

@dataclass
class Line:
    indent: int
    text: str
    no: int


def strip_comment(s: str) -> str:
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
    return "".join(out).rstrip()


def preprocess(text: str) -> list[Line]:
    lines: list[Line] = []
    for no, raw in enumerate(text.splitlines(), 1):
        s = strip_comment(raw)
        if not s.strip():
            continue
        leading = s[: len(s) - len(s.lstrip(" \t"))]
        if "\t" in leading:
            raise ValueError(f"tabs are not allowed for indentation (line {no})")
        indent = len(leading)
        if indent % 2 != 0:
            raise ValueError(f"indentation must be a multiple of 2 spaces (line {no})")
        lines.append(Line(indent, s.strip(), no))
    return lines


@dataclass
class Entity:
    name: str
    fields: dict[str, str] = field(default_factory=dict)
    # Field-level information-flow labels: `email: String @pii @internal`.
    field_labels: dict[str, list[str]] = field(default_factory=dict)
    line: int = 0


@dataclass
class AppPolicy:
    """App-level information-flow policy declared in a `policy` block."""

    labels: list[str] = field(default_factory=list)
    # effect -> {"accepts": [label], "rejects": [label]}
    sinks: dict[str, dict] = field(default_factory=dict)
    # [{"label": "pii", "in": "masked", "roles": ["support"]}]
    declassify: list[dict] = field(default_factory=list)


@dataclass
class PatternUse:
    name: str
    target: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class CustomBlock:
    name: str
    why: str = ""
    lines: int = 0
    line: int = 0


@dataclass
class App:
    name: str
    stack: list[str] = field(default_factory=list)
    freedom: str = "guided"
    entities: list[Entity] = field(default_factory=list)
    patterns: list[PatternUse] = field(default_factory=list)
    customs: list[CustomBlock] = field(default_factory=list)
    policy: AppPolicy = field(default_factory=AppPolicy)
    source: str = ""


def parse_string(s: str, line: int) -> str:
    s = s.strip()
    if not (s.startswith('"') and s.endswith('"')):
        raise ValueError(f"expected a quoted string (line {line})")
    return json.loads(s)


def slot_value(raw: str) -> str:
    """Return a slot value without its optional JSON string quotes."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            return parse_string(raw, 0)
        except ValueError:
            return raw[1:-1]
    return raw


def role_names(raw: str) -> set[str]:
    """Parse the comma-separated `roles` slot of an auth pattern."""
    return {part.strip().strip('"') for part in slot_value(raw).split(",") if part.strip()}


def parse_app(text: str) -> App:
    lines = preprocess(text)
    if not lines:
        raise ValueError("empty app spec")
    first = lines[0]
    m = re.fullmatch(r"app\s+([A-Za-z_]\w*)", first.text)
    if not m or first.indent != 0:
        raise ValueError("the first line must be `app <name>` at column 0")
    app = App(m.group(1), source=text)
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.indent != 2:
            raise ValueError(f"app declarations must be indented by 2 spaces (line {line.no})")
        if line.text.startswith("stack "):
            app.stack = [x.strip() for x in line.text[6:].split(",") if x.strip()]
            i += 1
        elif line.text.startswith("freedom "):
            app.freedom = line.text[8:].strip()
            i += 1
        elif line.text.startswith("entity "):
            entity, i = parse_entity(lines, i)
            app.entities.append(entity)
        elif line.text.startswith("pattern "):
            pattern, i = parse_pattern(lines, i)
            app.patterns.append(pattern)
        elif line.text.startswith("custom "):
            custom, i = parse_custom(lines, i)
            app.customs.append(custom)
        elif line.text == "policy":
            app.policy, i = parse_policy(lines, i)
        else:
            raise ValueError(f"unknown declaration {line.text!r} (line {line.no})")
    return app


def parse_entity(lines: list[Line], i: int) -> tuple[Entity, int]:
    line = lines[i]
    m = re.fullmatch(r"entity\s+([A-Za-z_]\w*)", line.text)
    if not m:
        raise ValueError(f"bad entity header {line.text!r} (line {line.no})")
    entity = Entity(m.group(1), line=line.no)
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        fm = re.fullmatch(
            r"([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*(?:<[^>]+>)?)(?:\s+((?:@[A-Za-z_]\w*\s*)+))?",
            lines[i].text,
        )
        if not fm:
            raise ValueError(f"bad field {lines[i].text!r} (line {lines[i].no})")
        entity.fields[fm.group(1)] = fm.group(2)
        if fm.group(3):
            entity.field_labels[fm.group(1)] = re.findall(r"@([A-Za-z_]\w*)", fm.group(3))
        i += 1
    return entity, i


def parse_pattern(lines: list[Line], i: int) -> tuple[PatternUse, int]:
    line = lines[i]
    m = re.fullmatch(r"pattern\s+([A-Za-z_]\w*)(?:\s+([A-Za-z_]\w*))?", line.text)
    if not m:
        raise ValueError(f"bad pattern header {line.text!r} (line {line.no})")
    pattern = PatternUse(m.group(1), m.group(2) or "", line=line.no)
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        sm = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", lines[i].text)
        if not sm:
            raise ValueError(f"bad slot {lines[i].text!r} (line {lines[i].no})")
        pattern.slots[sm.group(1)] = sm.group(2).strip()
        i += 1
    return pattern, i


def parse_custom(lines: list[Line], i: int) -> tuple[CustomBlock, int]:
    line = lines[i]
    m = re.fullmatch(r"custom\s+([A-Za-z_]\w*)", line.text)
    if not m:
        raise ValueError(f"bad custom header {line.text!r} (line {line.no})")
    custom = CustomBlock(m.group(1), line=line.no)
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        text = lines[i].text
        if text.startswith("why "):
            custom.why = parse_string(text[4:], lines[i].no)
        elif text.startswith("lines "):
            try:
                custom.lines = int(text[6:].strip())
            except ValueError:
                raise ValueError(f"custom lines must be an integer (line {lines[i].no})")
        else:
            raise ValueError(f"unknown custom property {text!r} (line {lines[i].no})")
        i += 1
    return custom, i


_SINK_EFFECT = re.compile(r"(read|write|net|spend|emit|notify)\(([A-Za-z0-9_.*-]+)\)")


def _label_list(raw: str, line: int) -> list[str]:
    labels = [part.strip() for part in raw.split(",") if part.strip()]
    for label in labels:
        if not re.fullmatch(r"[A-Za-z_]\w*", label):
            raise ValueError(f"bad label {label!r} (line {line})")
    return labels


def parse_policy(lines: list[Line], i: int) -> tuple[AppPolicy, int]:
    """Parse a `policy` block at indent 2 with rules at indent 4.

    policy
      labels pii, secret
      sink net(email) accepts pii
      sink net(*) rejects secret
      declassify pii in masked_email require role support
    """
    policy = AppPolicy()
    i += 1
    while i < len(lines) and lines[i].indent == 4:
        text = lines[i].text
        no = lines[i].no
        if text.startswith("labels "):
            for label in _label_list(text[7:], no):
                if label not in policy.labels:
                    policy.labels.append(label)
        elif text.startswith("sink "):
            m = re.fullmatch(r"sink\s+([a-z]+\([^)]*\))\s*(.*)", text)
            if not m or not _SINK_EFFECT.fullmatch(m.group(1)):
                raise ValueError(f"bad sink rule {text!r} (line {no})")
            effect = m.group(1)
            rest = m.group(2)
            rule = policy.sinks.setdefault(effect, {"accepts": [], "rejects": []})
            clauses = list(
                re.finditer(r"(accepts|rejects)\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)", rest)
            )
            if not clauses:
                raise ValueError(f"sink rule needs accepts or rejects (line {no})")
            consumed = 0
            for clause in clauses:
                if rest[consumed:clause.start()].strip():
                    raise ValueError(f"bad sink rule {text!r} (line {no})")
                consumed = clause.end()
                key = clause.group(1)
                for label in _label_list(clause.group(2), no):
                    if label not in rule[key]:
                        rule[key].append(label)
            if rest[consumed:].strip():
                raise ValueError(f"bad sink rule {text!r} (line {no})")
        elif text.startswith("declassify "):
            m = re.fullmatch(
                r"declassify\s+([A-Za-z_]\w*)\s+in\s+([A-Za-z_]\w*)\s+require\s+role\s+(.+)",
                text,
            )
            if not m:
                raise ValueError(f"bad declassify rule {text!r} (line {no})")
            roles = [part.strip() for part in m.group(3).split(",") if part.strip()]
            if not roles:
                raise ValueError(f"declassify needs a role (line {no})")
            policy.declassify.append({"label": m.group(1), "in": m.group(2), "roles": roles})
        else:
            raise ValueError(f"unknown policy rule {text!r} (line {no})")
        i += 1
    return policy, i


# ---------------------------------------------------------------------------
# Pattern manifests
# ---------------------------------------------------------------------------

@dataclass
class PatternManifest:
    name: str
    version: str
    intent: str
    tags: list[str] = field(default_factory=list)
    slots: dict[str, dict] = field(default_factory=dict)
    states: list[str] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    effects: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    requires_fields: dict[str, str] = field(default_factory=dict)
    # Enforced field types. `requires_fields` stays name-only: existing
    # manifests say `status: String` while specs use `ApprovalStatus`.
    field_types: dict[str, str] = field(default_factory=dict)
    constraints: list[dict] = field(default_factory=list)
    requires_patterns: list[str] = field(default_factory=list)
    requires_reason: str = ""
    notes: list[str] = field(default_factory=list)
    # Role slots that must be members of `auth.roles` when auth declares roles.
    bound_roles: list[str] = field(default_factory=list)
    # The pattern header must carry a name (`pattern approval_gate SendMail`).
    requires_target: bool = False
    # Cross-slot rules for an automated task. Empty on every other pattern.
    automation: dict = field(default_factory=dict)
    # Information-flow contract. None means "public data only" (fail closed
    # when the app declares labels).
    data_policy: Optional[dict] = None

    def to_index(self) -> dict:
        data = {
            "name": self.name,
            "version": self.version,
            "intent": self.intent,
            "tags": self.tags,
            "slots": {
                k: {"required": bool(v.get("required")), "desc": v.get("desc", "")}
                for k, v in self.slots.items()
            },
            "states": self.states,
            "effects": self.effects,
            "examples": self.examples,
            "path": f"registry/{self.name}.json",
        }
        if self.field_types:
            data["field_types"] = self.field_types
        if self.constraints:
            data["constraints"] = [
                {"id": c.get("id", ""), "message": c.get("message", "")} for c in self.constraints
            ]
        if self.requires_patterns:
            data["requires_patterns"] = self.requires_patterns
        if self.requires_target:
            data["requires_target"] = True
        if self.automation:
            data["automation"] = {
                "gates": [
                    {"action": gate.get("action", ""), "message": gate.get("message", "")}
                    for gate in self.automation.get("gates", [])
                ],
                "change_message": self.automation.get("change_message", ""),
            }
        if self.data_policy is not None:
            data["data_policy"] = {
                "labels": sorted(_policy_labels(self.data_policy)),
                "sinks": sorted(self.data_policy.get("sinks", {})),
            }
        return data


def manifest_from_dict(data: dict) -> PatternManifest:
    return PatternManifest(
        name=data.get("name", ""),
        version=data.get("version", "0.0.0"),
        intent=data.get("intent", ""),
        tags=data.get("tags", []),
        slots=data.get("slots", {}),
        states=data.get("states", []),
        transitions=data.get("transitions", []),
        hooks=data.get("hooks", {}),
        effects=data.get("effects", []),
        artifacts=data.get("artifacts", []),
        examples=data.get("examples", []),
        provenance=data.get("provenance", {}),
        requires_fields=data.get("requires_fields", {}),
        field_types=data.get("field_types", {}),
        constraints=data.get("constraints", []),
        requires_patterns=data.get("requires_patterns", []),
        requires_reason=data.get("requires_reason", ""),
        notes=data.get("notes", []),
        bound_roles=data.get("bound_roles", []),
        requires_target=bool(data.get("requires_target", False)),
        automation=data.get("automation") or {},
        data_policy=data.get("data_policy"),
    )


def _policy_labels(policy: dict) -> set[str]:
    """Every label named by a data_policy, including nested rules."""
    labels = set(policy.get("labels", []) or [])
    for value in (policy.get("sources", {}) or {}).values():
        labels.update(value or [])
    for rule in (policy.get("sinks", {}) or {}).values():
        labels.update((rule or {}).get("accepts", []) or [])
    for value in (policy.get("rejects", {}) or {}).values():
        labels.update(value or [])
    for rule in policy.get("declassify", []) or []:
        if isinstance(rule, dict) and rule.get("label"):
            labels.add(rule["label"])
    return labels


def _validate_data_policy(policy: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(policy, dict):
        return ["data_policy must be an object"]
    labels = policy.get("labels", [])
    if not isinstance(labels, list) or any(
        not isinstance(label, str) or not re.fullmatch(r"[A-Za-z_]\w*", label) for label in labels
    ):
        errors.append("data_policy.labels must be a list of label names")
        labels = []
    known = set(labels)

    sources = policy.get("sources", {})
    if not isinstance(sources, dict):
        errors.append("data_policy.sources must be an object")
        sources = {}
    for source, source_labels in sources.items():
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?", source):
            errors.append(f"data_policy source {source!r} is not an entity or entity.field")
        if not isinstance(source_labels, list) or not source_labels:
            errors.append(f"data_policy source {source!r} needs a non-empty label list")
            continue
        for label in source_labels:
            if label not in known:
                errors.append(f"data_policy source {source!r} uses undeclared label {label!r}")

    sinks = policy.get("sinks", {})
    if not isinstance(sinks, dict):
        errors.append("data_policy.sinks must be an object")
        sinks = {}
    for effect, rule in sinks.items():
        if not isinstance(effect, str) or not _SINK_EFFECT.fullmatch(effect):
            errors.append(f"data_policy sink {effect!r} is not an effect like net(email)")
            continue
        if not isinstance(rule, dict):
            errors.append(f"data_policy sink {effect!r} must be an object")
            continue
        accepts = rule.get("accepts", [])
        if not isinstance(accepts, list):
            errors.append(f"data_policy sink {effect!r} accepts must be a list")
            continue
        for label in accepts:
            if label not in known:
                errors.append(f"data_policy sink {effect!r} accepts undeclared label {label!r}")

    rejects = policy.get("rejects", {})
    if not isinstance(rejects, dict):
        errors.append("data_policy.rejects must be an object")
        rejects = {}
    for effect, rejected in rejects.items():
        if not isinstance(effect, str) or not _SINK_EFFECT.fullmatch(effect):
            errors.append(f"data_policy reject {effect!r} is not an effect like net(*)")
            continue
        if not isinstance(rejected, list):
            errors.append(f"data_policy reject {effect!r} must be a label list")
            continue
        for label in rejected:
            if label not in known:
                errors.append(f"data_policy reject {effect!r} uses undeclared label {label!r}")

    declassify = policy.get("declassify", [])
    if not isinstance(declassify, list):
        errors.append("data_policy.declassify must be a list")
        declassify = []
    for rule in declassify:
        if not isinstance(rule, dict):
            errors.append("data_policy.declassify entries must be objects")
            continue
        label = rule.get("label")
        scope = rule.get("in") or rule.get("scope")
        roles = rule.get("roles", [rule.get("role")] if rule.get("role") else [])
        if label not in known:
            errors.append(f"data_policy declassify uses undeclared label {label!r}")
        if not isinstance(scope, str) or not re.fullmatch(r"[A-Za-z_]\w*", scope):
            errors.append("data_policy declassify needs an `in` scope")
        if not isinstance(roles, list) or not roles or any(not isinstance(r, str) for r in roles):
            errors.append("data_policy declassify needs a role or roles")
    return errors


def validate_manifest(m: PatternManifest) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[a-z][a-z0-9_]*", m.name):
        errors.append("name must be lower_snake_case")
    if not m.intent:
        errors.append("intent is required")
    if not m.artifacts:
        errors.append("at least one artifact is required")
    for slot_name, spec in m.slots.items():
        if "type" not in spec:
            errors.append(f"slot {slot_name!r} is missing a type")
    for a in m.artifacts:
        if "path" not in a:
            errors.append("artifact is missing a path")
        if "lines" not in a:
            errors.append(f"artifact {a.get('path', '?')} is missing a line estimate")
    if m.states:
        known = set(m.states)
        for t in m.transitions:
            if t.get("from") not in known:
                errors.append(f"transition from unknown state {t.get('from')!r}")
            if t.get("to") not in known:
                errors.append(f"transition to unknown state {t.get('to')!r}")
            if not t.get("on"):
                errors.append("transition is missing an event name")
    measure_kinds = {"date", "rate", "duration"}
    comparisons = {">=", ">", "<=", "<", "==", "!="}
    for slot_name, spec in m.slots.items():
        if spec.get("type") == "enum" and not spec.get("values"):
            errors.append(f"slot {slot_name!r} enum needs values")
    for fname, ftype in m.field_types.items():
        if not isinstance(ftype, str) or not re.fullmatch(r"[A-Za-z_]\w*", ftype):
            errors.append(f"field {fname!r} has a bad type {ftype!r}")
    for constraint in m.constraints:
        cid = constraint.get("id") or "?"
        if constraint.get("kind") not in measure_kinds:
            errors.append(f"constraint {cid} kind must be date, rate, or duration")
        if constraint.get("op") not in comparisons:
            errors.append(f"constraint {cid} has an unknown comparison")
        for key in ("left", "right", "message"):
            if not constraint.get(key):
                errors.append(f"constraint {cid} is missing {key}")
    for name in m.requires_patterns:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            errors.append(f"requires_patterns entry {name!r} is not a pattern name")
    for role_slot in m.bound_roles:
        if role_slot not in m.slots:
            errors.append(f"bound role slot {role_slot!r} is not a slot")
    if m.automation:
        errors.extend(_validate_automation(m))
    if m.data_policy is not None:
        errors.extend(_validate_data_policy(m.data_policy))
    return errors


_AUTOMATION_STRINGS = (
    "effects_slot",
    "audience_slot",
    "steps_slot",
    "trigger_slot",
    "entity_slot",
    "gate_slot",
    "gate_pattern",
    "gate_action_slot",
    "gate_audience_slot",
    "gate_entity_slot",
    "change_slot",
    "change_value",
    "change_action",
    "change_message",
    "spend_run_slot",
    "spend_period_slot",
    "net_limit_slot",
    "emit_limit_slot",
    "retries_slot",
    "escalate_slot",
    "idempotency_slot",
    "audit_slot",
    "audit_value",
)
# Slots that live on the task pattern. Gate action/audience/entity live on the gate.
_AUTOMATION_TASK_SLOTS = (
    "effects_slot",
    "audience_slot",
    "steps_slot",
    "trigger_slot",
    "entity_slot",
    "gate_slot",
    "change_slot",
    "spend_run_slot",
    "spend_period_slot",
    "net_limit_slot",
    "emit_limit_slot",
    "retries_slot",
    "escalate_slot",
    "idempotency_slot",
    "audit_slot",
)


def _validate_automation(m: PatternManifest) -> list[str]:
    auto = m.automation
    errors: list[str] = []
    for key in _AUTOMATION_STRINGS:
        if not isinstance(auto.get(key), str) or not auto.get(key):
            errors.append(f"automation.{key} must be a non-empty string")
    triggers = auto.get("trigger_slots")
    if not isinstance(triggers, dict) or not triggers:
        errors.append("automation.trigger_slots must be a non-empty object")
    elif any(not isinstance(name, str) or not isinstance(slot, str) or not slot for name, slot in triggers.items()):
        errors.append("automation.trigger_slots values must be slot names")
    gates = auto.get("gates")
    if not isinstance(gates, list) or not gates:
        errors.append("automation.gates must be a non-empty list")
    else:
        for gate in gates:
            if not isinstance(gate, dict):
                errors.append("automation gate must be an object")
                continue
            if gate.get("kind") not in _EFFECT_KINDS:
                errors.append(f"automation gate kind {gate.get('kind')!r} is not an effect")
            audiences = gate.get("audiences")
            if not isinstance(audiences, list) or not audiences or any(not isinstance(item, str) for item in audiences):
                errors.append("automation gate audiences must be a list of strings")
            if not gate.get("action") or not gate.get("message"):
                errors.append("automation gate needs an action and a message")
            if not isinstance(gate.get("match_audience"), bool):
                errors.append("automation gate match_audience must be a boolean")
    retries_max = auto.get("retries_max")
    if not isinstance(retries_max, int) or isinstance(retries_max, bool) or retries_max < 0:
        errors.append("automation.retries_max must be a non-negative integer")
    for key in _AUTOMATION_TASK_SLOTS:
        slot = auto.get(key)
        if isinstance(slot, str) and slot and slot not in m.slots:
            errors.append(f"automation.{key} {slot!r} is not a slot")
    if isinstance(triggers, dict):
        for slot in triggers.values():
            if isinstance(slot, str) and slot and slot not in m.slots:
                errors.append(f"automation trigger slot {slot!r} is not a slot")
    gate_pattern = auto.get("gate_pattern")
    if isinstance(gate_pattern, str) and gate_pattern and not re.fullmatch(r"[a-z][a-z0-9_]*", gate_pattern):
        errors.append(f"automation.gate_pattern {gate_pattern!r} is not a pattern name")
    return errors


def load_registry(path: Path) -> dict[str, PatternManifest]:
    registry: dict[str, PatternManifest] = {}
    for p in sorted(path.glob("*.json")):
        if p.name == "index.json":
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        m = manifest_from_dict(data)
        errors = validate_manifest(m)
        if errors:
            raise ValueError(f"invalid manifest {p.name}: {'; '.join(errors)}")
        if m.name in registry:
            raise ValueError(f"duplicate manifest name {m.name!r} in {p.name}")
        if p.stem != m.name:
            raise ValueError(
                f"manifest filename {p.name!r} does not match declared name {m.name!r}"
            )
        registry[m.name] = m
    missing: list[str] = []
    for manifest in registry.values():
        for name in manifest.requires_patterns:
            if name not in registry:
                missing.append(f"{manifest.name} requires unknown pattern {name}")
        gate = manifest.automation.get("gate_pattern") if manifest.automation else ""
        if gate and gate not in registry:
            missing.append(f"{manifest.name} automation gate pattern {gate!r} is not registered")
    if missing:
        raise ValueError("registry references: " + "; ".join(missing))
    return registry


def write_index(registry_dir: Path) -> Path:
    registry = load_registry(registry_dir)
    index = {
        "version": 1,
        "patterns": [registry[name].to_index() for name in sorted(registry)],
    }
    out = registry_dir / "index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Measures: dates, decimal rates, durations
# ---------------------------------------------------------------------------

Measure = Union[date, Decimal]
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DECIMAL = re.compile(r"\d+(\.\d+)?\Z")
_CLOCK = re.compile(r"(\d+)h(\d{1,2})m\Z")
_COUNT = re.compile(r"0|[1-9]\d*\Z")
_IDENT = re.compile(r"[A-Za-z_]\w*\Z")
_WEBHOOK = re.compile(r"[A-Za-z_][\w./-]*\Z")
# Same five kinds as the contract layer. This module does not import etsah.py.
_EFFECT_KINDS = {"read", "write", "net", "spend", "emit"}
_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_COMPARISONS = {
    ">=": lambda left, right: left >= right,
    ">": lambda left, right: left > right,
    "<=": lambda left, right: left <= right,
    "<": lambda left, right: left < right,
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
}


def parse_date(value: str) -> Optional[date]:
    """ISO calendar date. `2026-4-1` and impossible days are rejected."""
    value = value.strip()
    if not _DATE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_rate(value: str) -> Optional[Decimal]:
    """Non-negative decimal hourly rate. No exponent and no leading sign."""
    value = value.strip()
    if not _DECIMAL.fullmatch(value):
        return None
    return Decimal(value)


def parse_duration(value: str) -> Optional[Decimal]:
    """Hours as a decimal (`7.5`) or a clock (`7h30m`). Minutes must be < 60."""
    value = value.strip()
    clock = _CLOCK.fullmatch(value)
    if clock:
        minutes = int(clock.group(2))
        if minutes >= 60:
            return None
        return Decimal(clock.group(1)) + (Decimal(minutes) / Decimal(60))
    if not _DECIMAL.fullmatch(value):
        return None
    return Decimal(value)


def parse_count(value: str) -> Optional[int]:
    """Non-negative integer. `0` is allowed; leading zeros are not."""
    value = value.strip()
    if not _COUNT.fullmatch(value):
        return None
    return int(value)


def _cron_atom(part: str, lo: int, hi: int) -> bool:
    step = None
    if "/" in part:
        base, step_text = part.split("/", 1)
        if not _COUNT.fullmatch(step_text) or int(step_text) == 0:
            return False
        step = int(step_text)
        part = base
    if part == "*":
        return step is None or step <= (hi - lo + 1)
    if "-" in part:
        start_text, end_text = part.split("-", 1)
        if not (start_text.isdigit() and end_text.isdigit()):
            return False
        start, end = int(start_text), int(end_text)
        if start > end or start < lo or end > hi:
            return False
        return step is None or step <= (end - start + 1)
    if part.isdigit() and lo <= int(part) <= hi:
        return step is None
    return False


def parse_cron(value: str) -> bool:
    """Five numeric cron fields: minute hour day-of-month month day-of-week.

    Names (`MON`) are rejected. `*` , `a-b`, `*/n`, and comma lists are accepted.
    """
    fields = value.strip().split()
    if len(fields) != 5:
        return False
    for field_text, (lo, hi) in zip(fields, _CRON_RANGES):
        if not field_text or any(not _cron_atom(part, lo, hi) for part in field_text.split(",")):
            return False
    return True


def split_csv(raw: str) -> list[str]:
    return [part.strip().strip('"') for part in slot_value(raw).split(",") if part.strip()]


def parse_name_list(raw: str) -> Optional[list[str]]:
    parts = split_csv(raw)
    if not parts or any(_IDENT.fullmatch(part) is None for part in parts):
        return None
    return parts


def parse_effect_list(raw: str) -> Optional[list[str]]:
    parts = split_csv(raw)
    if not parts or any(part not in _EFFECT_KINDS for part in parts) or len(parts) != len(set(parts)):
        return None
    return parts


def parse_measure(kind: str, value: str) -> Optional[Measure]:
    if kind == "date":
        return parse_date(value)
    if kind == "rate":
        return parse_rate(value)
    if kind == "duration":
        return parse_duration(value)
    return None


def _resolve_measure(token: str, slots: dict[str, str], declared: dict, kind: str) -> tuple[Optional[Measure], str]:
    """Return (value, status). Status is ok, absent, invalid, or unknown."""
    if token in declared:
        if token not in slots:
            return None, "absent"
        parsed = parse_measure(kind, slots[token])
        return (parsed, "ok") if parsed is not None else (None, "invalid")
    parsed = parse_measure(kind, token)
    if parsed is not None:
        return parsed, "ok"
    return None, "unknown"


def check_pattern_policy(
    app: App,
    pattern: PatternUse,
    manifest: PatternManifest,
    entity: Optional[Entity],
    err,
    warn,
) -> None:
    """Slot shapes and manifest constraints. Empty on patterns that declare none."""
    names = {item.name for item in app.entities}
    if entity is not None:
        for fname, expected in manifest.field_types.items():
            actual = entity.fields.get(fname)
            if actual is None:
                err(
                    "E-ENTITY",
                    f"{entity.name} needs a `{fname}: {expected}` field for {pattern.name}",
                    pattern.line,
                )
            elif actual != expected:
                err(
                    "E-TYPE",
                    f"{entity.name}.{fname} must be {expected} for {pattern.name}, got {actual}",
                    pattern.line,
                )
    for slot_name, spec in manifest.slots.items():
        if slot_name not in pattern.slots:
            continue
        raw = pattern.slots[slot_name]
        kind = spec.get("type")
        if kind == "entity" and slot_name != "entity" and raw not in names:
            err("E-TARGET", f"slot {slot_name} references unknown entity {raw!r}", pattern.line)
        elif kind in ("date", "rate", "duration") and parse_measure(kind, raw) is None:
            err(
                "E-SLOT",
                f"slot {slot_name} must be a {kind}, got {raw!r}",
                pattern.line,
            )
        elif kind == "enum" and raw not in (spec.get("values") or []):
            allowed = ", ".join(spec.get("values") or [])
            err(
                "E-SLOT",
                f"slot {slot_name} must be one of {allowed}; got {raw!r}",
                pattern.line,
            )
        elif kind == "count" and parse_count(slot_value(raw)) is None:
            err(
                "E-SLOT",
                f"slot {slot_name} must be a non-negative integer, got {slot_value(raw)!r}",
                pattern.line,
            )
        elif kind == "cron" and not parse_cron(slot_value(raw)):
            err(
                "E-SLOT",
                f"slot {slot_name} must be a 5-field numeric cron, got {slot_value(raw)!r}",
                pattern.line,
            )
        elif kind == "effects" and parse_effect_list(raw) is None:
            err(
                "E-SLOT",
                f"slot {slot_name} must be a list of read, write, net, spend, emit; got {slot_value(raw)!r}",
                pattern.line,
            )
        elif kind == "names" and parse_name_list(raw) is None:
            err(
                "E-SLOT",
                f"slot {slot_name} must be a comma-separated list of names, got {slot_value(raw)!r}",
                pattern.line,
            )
        elif kind == "name" and _WEBHOOK.fullmatch(slot_value(raw)) is None:
            err(
                "E-SLOT",
                f"slot {slot_name} must be a name, got {slot_value(raw)!r}",
                pattern.line,
            )
        elif kind == "field":
            owner_name = pattern.slots.get(spec.get("of", ""), "")
            owner = next((item for item in app.entities if item.name == owner_name), None)
            if owner is not None and raw not in owner.fields:
                err(
                    "E-ENTITY",
                    f"{owner.name} needs field {raw!r} for {pattern.name} slot {slot_name}",
                    pattern.line,
                )
    for slot_name, spec in manifest.slots.items():
        if spec.get("type") != "field" or slot_name in pattern.slots or spec.get("required"):
            continue
        default = spec.get("default")
        owner_name = pattern.slots.get(spec.get("of", ""), "")
        owner = next((item for item in app.entities if item.name == owner_name), None)
        if default and owner is not None and default not in owner.fields:
            err(
                "E-ENTITY",
                f"{owner.name} needs field {default!r} for {pattern.name} slot {slot_name}",
                pattern.line,
            )
    for constraint in manifest.constraints:
        _check_constraint(pattern, manifest, constraint, err, warn)
    for needed in manifest.requires_patterns:
        if not any(other.name == needed for other in app.patterns):
            reason = f"; {manifest.requires_reason}" if manifest.requires_reason else ""
            err(
                "E-POLICY",
                f"{pattern.name} requires pattern {needed!r}{reason}",
                pattern.line,
            )
    _check_bound_roles(pattern, manifest, _auth_roles(app), err)
    if manifest.automation:
        _check_automation(app, pattern, manifest, err)


def _check_constraint(pattern: PatternUse, manifest: PatternManifest, constraint: dict, err, warn) -> None:
    kind = constraint.get("kind", "")
    op = constraint.get("op", "")
    compare = _COMPARISONS.get(op)
    if compare is None or kind not in ("date", "rate", "duration"):
        return
    left_token = str(constraint.get("left", ""))
    right_token = str(constraint.get("right", ""))
    left, left_status = _resolve_measure(left_token, pattern.slots, manifest.slots, kind)
    right, right_status = _resolve_measure(right_token, pattern.slots, manifest.slots, kind)
    if left_status == "absent" or right_status == "absent":
        return
    cid = constraint.get("id") or pattern.name
    if left_status == "unknown" or right_status == "unknown":
        err(
            "E-POLICY",
            f"{pattern.name}.{cid} references an unknown value",
            pattern.line,
        )
        return
    if left is None or right is None or left_status == "invalid" or right_status == "invalid":
        return
    if compare(left, right):
        return
    message = (
        f"{pattern.name}.{cid}: {constraint.get('message', 'constraint failed')} "
        f"({left_token} {op} {right_token} is false; {left} {op} {right})"
    )
    exempt_slot = constraint.get("exempt_slot")
    exempt_values = constraint.get("exempt_values") or []
    if exempt_slot and pattern.slots.get(exempt_slot) in exempt_values:
        warn("W-POLICY", message + " (exempt slot is set; this is not a legal opinion)", pattern.line)
        return
    err("E-POLICY", message, pattern.line)


def _auth_roles(app: App) -> set[str]:
    roles: set[str] = set()
    for item in app.patterns:
        if item.name == "auth" and "roles" in item.slots:
            roles |= role_names(item.slots["roles"])
    return roles


def _check_bound_roles(pattern: PatternUse, manifest: PatternManifest, auth_roles: set[str], err) -> None:
    if not auth_roles:
        return
    for slot_name in manifest.bound_roles:
        if slot_name not in pattern.slots:
            continue
        value = slot_value(pattern.slots[slot_name])
        if value not in auth_roles:
            err(
                "E-SLOT",
                f"slot {slot_name} role {value!r} is not in auth.roles ({', '.join(sorted(auth_roles))})",
                pattern.line,
            )


def _count_slot(slots: dict[str, str], name: str) -> Optional[int]:
    if name not in slots:
        return None
    return parse_count(slot_value(slots[name]))


def _check_automation(app: App, pattern: PatternUse, manifest: PatternManifest, err) -> None:
    """Trigger shape, step names, budgets, and approval gates for one task."""
    auto = manifest.automation
    slots = pattern.slots
    effects = parse_effect_list(slots[auto["effects_slot"]]) if auto["effects_slot"] in slots else None
    audience = slot_value(slots[auto["audience_slot"]]) if auto["audience_slot"] in slots else ""

    if auto["trigger_slot"] in slots:
        trigger = slot_value(slots[auto["trigger_slot"]])
        mapping = auto.get("trigger_slots") or {}
        needed = mapping.get(trigger)
        if isinstance(needed, str) and needed not in slots:
            err("E-SLOT", f"trigger {trigger} requires slot {needed!r}", pattern.line)
        for name, slot_name in mapping.items():
            if name != trigger and slot_name in slots:
                err(
                    "E-SLOT",
                    f"slot {slot_name!r} is only valid when trigger is {name}",
                    pattern.line,
                )

    if auto["steps_slot"] in slots and parse_name_list(slots[auto["steps_slot"]]) is not None:
        customs = {item.name for item in app.customs}
        applied = {item.name for item in app.patterns}
        for step in parse_name_list(slots[auto["steps_slot"]]) or []:
            if step not in customs and step not in applied:
                err(
                    "E-HOOK",
                    f"step {step!r} is not a custom block or an applied pattern",
                    pattern.line,
                )

    _check_task_budgets(pattern, auto, effects, err)
    _check_task_retries(pattern, auto, err)
    _check_task_gates(app, pattern, auto, effects, audience, err)


def _check_task_budgets(pattern: PatternUse, auto: dict, effects: Optional[list[str]], err) -> None:
    if effects is None:
        return
    slots = pattern.slots
    run = _count_slot(slots, auto["spend_run_slot"])
    period = _count_slot(slots, auto["spend_period_slot"])
    if run is not None and period is not None:
        if "spend" in effects and (run == 0 or period == 0):
            err(
                "E-POLICY",
                f"{pattern.name}: spend effect needs a positive per-run and per-period budget",
                pattern.line,
            )
        elif "spend" in effects and run > period:
            err(
                "E-POLICY",
                f"{pattern.name}: per-run spend exceeds the period budget ({run} <= {period} is false)",
                pattern.line,
            )
        elif "spend" not in effects and (run != 0 or period != 0):
            err(
                "E-POLICY",
                f"{pattern.name}: spend budget is set but spend is not an allowed effect",
                pattern.line,
            )
    _check_rate_limit(
        pattern,
        effects,
        _count_slot(slots, auto["net_limit_slot"]),
        "net",
        "net effect needs a positive hourly rate limit",
        "net rate limit is set but net is not an allowed effect",
        err,
    )
    _check_rate_limit(
        pattern,
        effects,
        _count_slot(slots, auto["emit_limit_slot"]),
        "emit",
        "emit effect needs a positive hourly rate limit",
        "emit rate limit is set but emit is not an allowed effect",
        err,
    )


def _check_rate_limit(pattern, effects, limit, kind, present_message, absent_message, err) -> None:
    if limit is None:
        return
    if kind in effects and limit == 0:
        err("E-POLICY", f"{pattern.name}: {present_message}", pattern.line)
    elif kind not in effects and limit != 0:
        err("E-POLICY", f"{pattern.name}: {absent_message}", pattern.line)


def _check_task_retries(pattern: PatternUse, auto: dict, err) -> None:
    value = _count_slot(pattern.slots, auto["retries_slot"])
    if value is None:
        return
    limit = auto.get("retries_max")
    if isinstance(limit, int) and not isinstance(limit, bool) and value > limit:
        err(
            "E-POLICY",
            f"{pattern.name}: retries must be from 0 through {limit}, got {value}",
            pattern.line,
        )


def _gates_by_target(app: App, gate_pattern: str) -> tuple[dict[str, PatternUse], set[str]]:
    found: dict[str, PatternUse] = {}
    ambiguous: set[str] = set()
    for other in app.patterns:
        if other.name != gate_pattern or not other.target:
            continue
        if other.target in found:
            ambiguous.add(other.target)
        found[other.target] = other
    return found, ambiguous


def _check_task_gates(
    app: App,
    pattern: PatternUse,
    auto: dict,
    effects: Optional[list[str]],
    audience: str,
    err,
) -> None:
    if effects is None or not audience:
        return
    slots = pattern.slots
    required: list[tuple[str, str, bool]] = []
    for rule in auto.get("gates") or []:
        if rule.get("kind") in effects and audience in (rule.get("audiences") or []):
            required.append((str(rule.get("action")), str(rule.get("message")), bool(rule.get("match_audience"))))
    change_slot = auto["change_slot"]
    change = slot_value(slots[change_slot]) if change_slot in slots else "none"
    if change == auto["change_value"]:
        required.append((str(auto["change_action"]), str(auto["change_message"]), True))

    named: list[str] = []
    gate_slot = auto["gate_slot"]
    if gate_slot in slots:
        parsed = parse_name_list(slots[gate_slot])
        if parsed is None:
            return
        named = parsed
    if not required:
        if named:
            err("E-POLICY", f"{pattern.name}: approval is set but this task has no gated effect", pattern.line)
        return
    if not named:
        for _action, message, _match in required:
            err("E-POLICY", f"{pattern.name}: {message}", pattern.line)
        return

    found, ambiguous = _gates_by_target(app, str(auto["gate_pattern"]))
    task_entity = slot_value(slots.get(auto["entity_slot"], "")) or pattern.target
    wanted: dict[str, tuple[str, bool]] = {}
    for action, message, match_audience in required:
        wanted.setdefault(action, (message, match_audience))
    covered: dict[str, str] = {}
    for name in named:
        gate = found.get(name)
        if name in ambiguous or gate is None:
            if name in ambiguous:
                err("E-POLICY", f"{pattern.name}: approval gate {name!r} is ambiguous", pattern.line)
            else:
                err("E-POLICY", f"{pattern.name}: approval gate {name!r} is not applied", pattern.line)
            continue
        action = slot_value(gate.slots.get(auto["gate_action_slot"], ""))
        gate_entity = slot_value(gate.slots.get(auto["gate_entity_slot"], ""))
        if task_entity and gate_entity != task_entity:
            err(
                "E-POLICY",
                f"{pattern.name}: approval gate {name!r} is for entity {gate_entity!r}, not {task_entity!r}",
                pattern.line,
            )
        if action not in wanted:
            err(
                "E-POLICY",
                f"{pattern.name}: approval gate {name!r} action {action!r} is not one of the gated effects",
                pattern.line,
            )
            continue
        if action in covered:
            err("E-POLICY", f"{pattern.name}: approval gate action {action!r} is named more than once", pattern.line)
            continue
        _action, match_audience = wanted[action]
        if match_audience:
            gate_audience = slot_value(gate.slots.get(auto["gate_audience_slot"], ""))
            if gate_audience != audience:
                err(
                    "E-POLICY",
                    f"{pattern.name}: approval gate {name!r} audience is {gate_audience!r}, not {audience!r}",
                    pattern.line,
                )
        covered[action] = name
    for action, (message, _match) in wanted.items():
        if action not in covered:
            err("E-POLICY", f"{pattern.name}: {message}", pattern.line)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def scene_choices(scene: PatternUse) -> list:
    choices = json.loads(scene.slots.get("choices", "[]"))
    if not isinstance(choices, list) or not all(
        isinstance(pair, list) and len(pair) == 2
        and all(isinstance(value, str) and value.strip() for value in pair)
        for pair in choices
    ):
        raise ValueError("choices must be an array of [label, scene ID] pairs")
    return choices


def validate(app: App, registry: dict[str, PatternManifest]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []

    def err(code: str, message: str, line: int = 0) -> None:
        diags.append(Diagnostic(code, message, line))

    def warn(code: str, message: str, line: int = 0) -> None:
        diags.append(Diagnostic(code, message, line, "warning"))

    if not app.stack:
        err("E-STACK", "stack is required; declare at least one target")
    if app.freedom not in ("strict", "guided", "open"):
        err("E-FREEDOM", f"freedom must be strict, guided, or open; got {app.freedom!r}")

    entity_names = {e.name for e in app.entities}
    seen_entities: set[str] = set()
    for e in app.entities:
        if e.name in seen_entities:
            err("E-DUP", f"duplicate entity {e.name!r}", e.line)
        seen_entities.add(e.name)
        if not e.fields:
            err("E-ENTITY", f"entity {e.name!r} has no fields", e.line)

    seen_patterns: set[tuple[str, str, str]] = set()
    seen_targets: set[tuple[str, str]] = set()
    custom_names = {c.name for c in app.customs}
    applied_patterns = {p.name for p in app.patterns}
    auth_count = 0
    auth_roles: set[str] = set()
    for p in app.patterns:
        if p.name == "auth" and "roles" in p.slots:
            auth_roles |= role_names(p.slots["roles"])
    for p in app.patterns:
        m = registry.get(p.name)
        if not m:
            err("E-PATTERN", f"unknown pattern {p.name!r}; run `etsahc find` to discover patterns", p.line)
            continue
        for slot_name, spec in m.slots.items():
            if spec.get("required") and slot_name not in p.slots:
                err("E-SLOT", f"pattern {p.name} requires slot {slot_name!r}", p.line)
            hook_raw = p.slots.get(slot_name)
            if spec.get("type") == "hook" and hook_raw:
                hook = slot_value(hook_raw)
                if hook not in custom_names:
                    if hook not in registry:
                        err(
                            "E-HOOK",
                            f"{p.name}.{slot_name}: {hook!r} is not a custom block or registered pattern",
                            p.line,
                        )
                    elif hook not in applied_patterns:
                        err(
                            "E-HOOK",
                            f"{p.name}.{slot_name}: add `pattern {hook}` to use this hook",
                            p.line,
                        )
        for slot_name in p.slots:
            if slot_name not in m.slots:
                diags.append(
                    Diagnostic(
                        "W-SLOT",
                        f"pattern {p.name} has no slot {slot_name!r}; it will be ignored",
                        p.line,
                        "warning",
                    )
                )
        target = p.slots.get("entity", p.target)
        if "entity" in m.slots:
            if not target:
                err("E-TARGET", f"pattern {p.name} requires an entity", p.line)
            elif target not in entity_names:
                err("E-TARGET", f"entity {target!r} is not declared", p.line)
        entity = next((e for e in app.entities if e.name == target), None)
        if entity:
            for fname in m.requires_fields:
                if fname not in entity.fields:
                    err("E-ENTITY", f"{entity.name} needs a `{fname}` field for {p.name}", p.line)
        # Applying the same pattern twice to the same target expands the
        # same artifacts twice. Patterns without a target (notify, scene)
        # may legitimately appear more than once, and auth has its own
        # singleton check below, so only target-bearing uses are keyed
        # here. Named uses (pattern ai_task RemindTimesheet) are distinct
        # instances by design — several tasks may serve one entity — so
        # the instance name is part of the key when it differs from the
        # resolved entity target.
        if p.name != "auth" and target:
            instance = p.target if p.target and p.target != target else ""
            key = (p.name, target, instance)
            if key in seen_patterns:
                err("E-CONFLICT", f"{p.name} is already applied to {target}", p.line)
            seen_patterns.add(key)
        check_pattern_policy(app, p, m, entity, err, warn)
        hook_spec = m.hooks.get("on_approved")
        hook_raw = p.slots.get("on_approved")
        if hook_spec and hook_raw:
            hook = slot_value(hook_raw)
            uses = [other for other in app.patterns if other.name == hook]
            if hook in custom_names or hook not in registry or not uses:
                # The generic hook-slot check above already reported these.
                pass
            elif len(uses) > 1:
                err(
                    "E-HOOK",
                    f"on_approved pattern {hook!r} is applied {len(uses)} times; the hook target is ambiguous",
                    p.line,
                )
            else:
                expected = hook_spec.get("pattern")
                if expected and hook != expected:
                    err(
                        "E-HOOK",
                        f"on_approved must reference the {expected!r} pattern, got {hook!r}",
                        p.line,
                    )
                else:
                    use = uses[0]
                    for arg in hook_spec.get("args", []):
                        if arg not in use.slots:
                            err(
                                "E-HOOK-ARG",
                                f"on_approved {hook!r} requires argument {arg!r}",
                                p.line,
                            )
                    target = registry[hook]
                    for arg, raw in use.slots.items():
                        spec = target.slots.get(arg)
                        if not spec:
                            continue
                        value = slot_value(raw)
                        allowed = spec.get("enum")
                        if allowed and value not in allowed:
                            err(
                                "E-HOOK-ARG",
                                f"on_approved {hook}.{arg} must be one of {', '.join(allowed)}; got {value!r}",
                                use.line,
                            )
                        if spec.get("type") == "role" and auth_roles and value not in auth_roles:
                            err(
                                "E-HOOK-ARG",
                                f"on_approved {hook}.{arg} role {value!r} is not in auth.roles ({', '.join(sorted(auth_roles))})",
                                use.line,
                            )
        if m.requires_target:
            if not p.target:
                err("E-SLOT", f"pattern {p.name} requires a name after the pattern keyword", p.line)
            else:
                key = (p.name, p.target)
                if key in seen_targets:
                    err("E-DUP", f"duplicate {p.name} name {p.target!r}", p.line)
                seen_targets.add(key)
        if p.name == "auth":
            auth_count += 1
            if auth_count > 1:
                err("E-CONFLICT", "auth can only be applied once", p.line)

    seen_custom: set[str] = set()
    for c in app.customs:
        if c.name in seen_custom:
            err("E-DUP", f"duplicate custom block {c.name!r}", c.line)
        seen_custom.add(c.name)
        if c.lines <= 0:
            err("E-CUSTOM", f"custom {c.name} needs a positive `lines` estimate", c.line)
        if app.freedom == "strict":
            err("E-FREEDOM", "strict mode does not allow custom blocks", c.line)
        if app.freedom == "guided" and not c.why:
            err("E-CUSTOM", f"custom {c.name} needs a `why` in guided mode", c.line)

    scenes = [p for p in app.patterns if p.name == "scene"]
    scene_ids = {p.target for p in scenes}
    seen_scenes = set()
    for scene in scenes:
        if not scene.target or scene.target in seen_scenes:
            err("E-SCENE", "each scene requires a unique scene ID", scene.line)
        seen_scenes.add(scene.target)
        try:
            choices = scene_choices(scene)
        except ValueError as ex:
            err("E-SCENE", str(ex), scene.line)
            continue
        for label, destination in choices:
            if destination not in scene_ids:
                err("E-SCENE", f"{label!r} links to unknown scene {destination!r}", scene.line)

    for note in staffing_rules.analyze(app):
        diags.append(Diagnostic(note.code, note.message, note.line, note.severity))

    security = analyze_security(app, registry)
    diags.extend(security.problems)
    return diags


# ---------------------------------------------------------------------------
# Information flow across patterns
# ---------------------------------------------------------------------------

@dataclass
class SecurityReport:
    labels: list[str] = field(default_factory=list)
    flows: list[dict] = field(default_factory=list)
    guarantees: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    sinks: dict[str, dict] = field(default_factory=dict)
    declassify: list[dict] = field(default_factory=list)
    problems: list[Diagnostic] = field(default_factory=list)
    checked: bool = False


def app_hash(app: App) -> str:
    """A stable 12-hex hash of the parsed app spec (the pattern-layer AST)."""
    payload = {
        "name": app.name,
        "stack": app.stack,
        "freedom": app.freedom,
        "entities": [
            {
                "name": e.name,
                "fields": e.fields,
                "labels": {key: sorted(value) for key, value in sorted(e.field_labels.items())},
            }
            for e in app.entities
        ],
        "patterns": [
            {"name": p.name, "target": p.target, "slots": p.slots} for p in app.patterns
        ],
        "customs": [{"name": c.name, "why": c.why, "lines": c.lines} for c in app.customs],
        "policy": {
            "labels": sorted(app.policy.labels),
            "sinks": {
                effect: {
                    "accepts": sorted(rule.get("accepts", [])),
                    "rejects": sorted(rule.get("rejects", [])),
                }
                for effect, rule in sorted(app.policy.sinks.items())
            },
            "declassify": sorted(
                (
                    {
                        "label": rule.get("label", ""),
                        "in": rule.get("in", ""),
                        "roles": sorted(rule.get("roles", [])),
                    }
                    for rule in app.policy.declassify
                ),
                key=lambda rule: (rule["label"], rule["in"], rule["roles"]),
            ),
        },
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _source_labels(app: App, name: str, field_name: str = "", seen: Optional[set[str]] = None) -> set[str]:
    """Labels reachable from an entity or one of its fields.

    A field whose type is another entity (for example `applicant: Employee`)
    carries that entity's labels, so a report cannot hide linked PII.
    """
    entity = next((e for e in app.entities if e.name == name), None)
    if entity is None:
        return set()
    seen = set(seen or ())
    if name in seen:
        return set()
    seen.add(name)
    labels: set[str] = set()
    if field_name:
        labels.update(entity.field_labels.get(field_name, []))
        linked = entity.fields.get(field_name, "")
        if linked in {e.name for e in app.entities}:
            labels |= _source_labels(app, linked, "", seen)
        return labels
    for field_labels in entity.field_labels.values():
        labels.update(field_labels)
    for field_type in entity.fields.values():
        if field_type in {e.name for e in app.entities}:
            labels |= _source_labels(app, field_type, "", seen)
    return labels


def _all_entity_labels(app: App) -> set[str]:
    labels = set(app.policy.labels)
    for entity in app.entities:
        for field_labels in entity.field_labels.values():
            labels.update(field_labels)
    return labels


def _resolve_effect(effect: str, pattern: PatternUse) -> str:
    """Substitute `{slot}` and bare slot names inside a sink effect."""
    out = effect
    for slot_name, raw in pattern.slots.items():
        out = out.replace("{" + slot_name + "}", slot_value(raw))
    m = _SINK_EFFECT.fullmatch(out)
    if not m:
        return out
    kind, inner = m.group(1), m.group(2)
    if inner in pattern.slots:
        inner = slot_value(pattern.slots[inner])
    elif inner == "entity" and pattern.target:
        inner = pattern.slots.get("entity", pattern.target)
    return f"{kind}({inner})"


def _resolve_source(source: str, pattern: PatternUse, entity: Optional[Entity]) -> tuple[str, str]:
    """Return (resolved entity or entity.field, field name or "")."""
    if source == "entity":
        name = pattern.slots.get("entity", pattern.target or (entity.name if entity else ""))
        return name, ""
    if source.startswith("entity."):
        name = pattern.slots.get("entity", pattern.target or (entity.name if entity else ""))
        return f"{name}.{source.split('.', 1)[1]}", source.split(".", 1)[1]
    if source in pattern.slots:
        value = slot_value(pattern.slots[source])
        return value, ""
    return source, ""


def _app_sink_rule(app: App, effect: str) -> tuple[set[str], set[str]]:
    kind = effect.split("(", 1)[0]
    accepts: set[str] = set()
    rejects: set[str] = set()
    for key in (effect, f"{kind}(*)"):
        rule = app.policy.sinks.get(key)
        if rule:
            accepts.update(rule.get("accepts", []))
            rejects.update(rule.get("rejects", []))
    return accepts, rejects


def _match_declassify(app: App, label: str, scope: str, roles: list[str]) -> Optional[dict]:
    for rule in app.policy.declassify:
        if rule.get("label") != label or rule.get("in") != scope:
            continue
        if set(roles) & set(rule.get("roles", [])):
            return rule
    return None


def analyze_security(app: App, registry: dict[str, PatternManifest]) -> SecurityReport:
    """Merge app and pattern policies, and report what is (not) guaranteed."""
    report = SecurityReport()

    def problem(code: str, message: str, line: int = 0, severity: str = "error") -> None:
        report.problems.append(Diagnostic(code, message, line, severity))

    app_labels = _all_entity_labels(app)
    if not app_labels:
        # No labels means there is nothing to track. Pattern data_policy is a
        # capability declaration, not data: it must not force every app that
        # uses the pattern to repeat a policy.
        report.labels = []
        report.guarantees.append("no labeled data is declared")
        return report

    pattern_labels: set[str] = set()
    for use in app.patterns:
        manifest = registry.get(use.name)
        if manifest and manifest.data_policy:
            pattern_labels |= _policy_labels(manifest.data_policy)
    report.labels = sorted(app_labels | pattern_labels)
    report.checked = True

    known = app_labels
    for effect, rule in app.policy.sinks.items():
        for key in ("accepts", "rejects"):
            for label in rule.get(key, []):
                if label not in known:
                    problem(
                        "E-POLICY",
                        f"app policy {effect} {key} label {label!r} is not declared on any field",
                    )
    for rule in app.policy.declassify:
        if rule.get("label") not in known:
            problem(
                "E-POLICY",
                f"app declassify label {rule.get('label')!r} is not declared on any field",
            )

    # Start from the app policy so sinks with no pattern use are still visible.
    for effect, rule in app.policy.sinks.items():
        report.sinks[effect] = {
            "accepts": sorted(set(rule.get("accepts", []))),
            "rejects": sorted(set(rule.get("rejects", []))),
        }

    for use in app.patterns:
        manifest = registry.get(use.name)
        if not manifest:
            continue
        entity = entity_for(app, use)
        policy = manifest.data_policy
        if policy is None:
            report.unresolved.append(
                f"pattern {use.name}: no data_policy; treated as public data only"
            )
            if app_labels:
                problem(
                    "E-POLICY",
                    f"pattern {use.name} has no data_policy but the app declares labels; "
                    "add data_policy to the manifest or remove the labels",
                    use.line,
                )
            continue

        flow = {
            "name": f"{use.name}:{use.target}" if use.target else use.name,
            "sources": [],
            "sinks": [],
            "declassify": [],
            "labels": [],
        }
        for source, declared in (policy.get("sources", {}) or {}).items():
            resolved, field = _resolve_source(source, use, entity)
            if not resolved:
                problem("E-POLICY", f"pattern {use.name} source {source!r} cannot be resolved", use.line)
                continue
            base = resolved.split(".", 1)[0]
            if base not in {e.name for e in app.entities}:
                problem(
                    "E-POLICY",
                    f"pattern {use.name} source {source!r} resolves to unknown entity {base!r}",
                    use.line,
                )
                continue
            actual = _source_labels(app, base, field)
            labels = set(declared or []) | set(actual)
            flow["sources"].append(f"read({resolved})")
            flow["labels"] = sorted(set(flow["labels"]) | labels)

        pattern_rejects: dict[str, set[str]] = {}
        for effect, labels in (policy.get("rejects", {}) or {}).items():
            resolved = _resolve_effect(effect, use)
            pattern_rejects.setdefault(resolved, set()).update(labels)

        for effect, rule in (policy.get("sinks", {}) or {}).items():
            resolved = _resolve_effect(effect, use)
            needed = set(rule.get("accepts", []) or [])
            app_accepts, app_rejects = _app_sink_rule(app, resolved)
            missing = needed - app_accepts
            blocked = needed & app_rejects
            if missing:
                problem(
                    "E-POLICY",
                    f"pattern {use.name} requires {sorted(missing)} at {resolved}, "
                    "but the app policy does not accept it",
                    use.line,
                )
            if blocked:
                problem(
                    "E-POLICY",
                    f"pattern {use.name} sends {sorted(blocked)} to {resolved}, "
                    "but the app policy rejects it",
                    use.line,
                )
            merged = report.sinks.setdefault(resolved, {"accepts": [], "rejects": []})
            merged["accepts"] = sorted(set(merged["accepts"]) | (needed & app_accepts))
            merged["rejects"] = sorted(set(merged["rejects"]) | app_rejects)
            flow["sinks"].append(resolved)

        for resolved, labels in pattern_rejects.items():
            merged = report.sinks.setdefault(resolved, {"accepts": [], "rejects": []})
            merged["rejects"] = sorted(set(merged["rejects"]) | labels)

        for rule in policy.get("declassify", []) or []:
            label = rule.get("label", "")
            scope = rule.get("in") or rule.get("scope", "")
            roles = rule.get("roles") or ([rule["role"]] if rule.get("role") else [])
            if not _match_declassify(app, label, scope, roles):
                problem(
                    "E-POLICY",
                    f"pattern {use.name} requires declassify {label} in {scope} "
                    f"for {roles}, but the app policy does not allow it",
                    use.line,
                )
                continue
            entry = {"label": label, "in": scope, "roles": sorted(roles)}
            if entry not in report.declassify:
                report.declassify.append(entry)
            flow["declassify"].append(entry)

        if flow["sources"] or flow["sinks"]:
            report.flows.append(flow)

    for rule in app.policy.declassify:
        entry = {
            "label": rule.get("label", ""),
            "in": rule.get("in", ""),
            "roles": sorted(rule.get("roles", [])),
        }
        if entry not in report.declassify:
            report.declassify.append(entry)

    # `net(*) rejects secret` applies to every concrete net sink. Resolve the
    # wildcard into the concrete rules so a narrower `net(email) accepts
    # secret` cannot silently override it.
    for effect in list(report.sinks):
        if effect.endswith("(*)"):
            continue
        kind = effect.split("(", 1)[0]
        wildcard = report.sinks.get(f"{kind}(*)")
        if not wildcard:
            continue
        rule = report.sinks[effect]
        rule["accepts"] = sorted(set(rule.get("accepts", [])) | set(wildcard.get("accepts", [])))
        rule["rejects"] = sorted(set(rule.get("rejects", [])) | set(wildcard.get("rejects", [])))

    for custom in app.customs:
        report.unresolved.append(
            f"custom {custom.name} ({custom.lines} lines): its implementation is trusted"
        )
        if app_labels:
            problem(
                "W-POLICY",
                f"custom {custom.name} is not covered by the pattern data_policy; "
                "labeled data may flow through it",
                custom.line,
                "warning",
            )

    # A sink that accepts a rejected label is a contradiction, not a guarantee.
    for effect, rule in report.sinks.items():
        conflict = set(rule.get("accepts", [])) & set(rule.get("rejects", []))
        if conflict:
            problem(
                "E-POLICY",
                f"sink {effect} accepts and rejects the same labels: {sorted(conflict)}",
            )

    errors = [item for item in report.problems if item.severity == "error"]
    if not errors:
        if not report.labels:
            report.guarantees.append("no labeled data is declared")
        elif not report.sinks:
            report.guarantees.append("no labeled data leaves the app through a declared sink")
        else:
            for effect in sorted(report.sinks):
                rule = report.sinks[effect]
                for label in report.labels:
                    if label in rule.get("rejects", []) or label not in rule.get("accepts", []):
                        report.guarantees.append(f"no {label} to {effect}")
        report.guarantees = sorted(set(report.guarantees))
    return report


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

@dataclass
class Artifact:
    pattern: str
    path: str
    kind: str
    lines: int
    desc: str


def plural(name: str) -> str:
    if name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    return name + "s"


def entity_for(app: App, p: PatternUse) -> Optional[Entity]:
    target = p.slots.get("entity", p.target)
    return next((e for e in app.entities if e.name == target), None)


def template_context(entity: Optional[Entity], target: str = "") -> dict:
    name = entity.name if entity else ""
    label = target or name
    return {
        "entity": name,
        "Entity": name,
        "entity_lower": name.lower(),
        "plural": plural(name).lower() if name else "",
        "Plural": plural(name) if name else "",
        "fields": len(entity.fields) if entity else 0,
        "target": label,
        "target_lower": label.lower(),
    }


def substitute(template: str, ctx: dict) -> str:
    out = template
    for key, value in ctx.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def expand(app: App, registry: dict[str, PatternManifest]) -> list[Artifact]:
    arts: list[Artifact] = []

    schema_lines = 10 + sum(4 + len(e.fields) * 2 for e in app.entities)
    type_lines = 15 + sum(len(e.fields) * 2 for e in app.entities)
    arts.append(Artifact("shared", "db/schema.sql", "SQL", schema_lines, "エンティティから生成したテーブル定義"))
    arts.append(Artifact("shared", "lib/db.ts", "TS", 25, "Prismaクライアントと接続管理"))
    arts.append(Artifact("shared", "lib/types.ts", "TS", type_lines, "共有型とブランド型"))
    arts.append(Artifact("shared", "README.md", "MD", 30, "セットアップと環境変数の手順"))

    if not app.entities and app.patterns and all(p.name == "scene" for p in app.patterns):
        arts.clear()

    for p in app.patterns:
        m = registry.get(p.name)
        if not m:
            continue
        entity = entity_for(app, p)
        ctx = template_context(entity, p.target)
        if p.name == "scene":
            ctx["Entity"] = p.target
        for t in m.artifacts:
            path = substitute(t.get("path", ""), ctx)
            desc = substitute(t.get("desc", ""), ctx)
            lines = int(t.get("lines", 0)) + int(t.get("per_field", 0)) * ctx["fields"]
            arts.append(Artifact(p.name, path, t.get("kind", "TS"), lines, desc))

    for c in app.customs:
        arts.append(Artifact("custom", f"custom/{c.name}.ts", "TS", c.lines, c.why or "custom logic"))

    for pattern, path, kind, lines, desc in staffing_rules.runtime_artifacts(app):
        arts.append(Artifact(pattern, path, kind, lines, desc))

    return arts


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def spec_lines(source: str) -> int:
    return sum(1 for line in source.splitlines() if line.strip() and not line.strip().startswith("#"))


def render_report(
    app: App,
    diags: list[Diagnostic],
    arts: list[Artifact],
    security: Optional[SecurityReport] = None,
) -> str:
    total_lines = sum(a.lines for a in arts)
    spec = spec_lines(app.source)
    ratio = round(total_lines / spec, 1) if spec else 0
    errors = [d for d in diags if d.severity == "error"]
    patterns = len(app.patterns)

    groups: dict[str, list[Artifact]] = {}
    for a in arts:
        groups.setdefault(a.pattern, []).append(a)

    pattern_order = ["shared"] + [p.name for p in app.patterns] + ["custom"]
    pattern_labels = {
        "shared": "共通基盤",
        "auth": "認証",
        "crud": "CRUD",
        "checkout": "決済",
        "refund": "返金",
        "dashboard": "ダッシュボード",
        "search": "検索",
        "notify": "通知",
        "upload": "アップロード",
        "approval": "承認ワークフロー",
        "timesheet": "勤怠承認",
        "dispatch_contract": "派遣契約",
        "staffing_billing": "請求と給与",
        "matching": "マッチング",
        "ai_task": "AIタスク",
        "approval_gate": "承認ゲート",
        "custom": "カスタム",
    }
    pattern_labels["overtime_agreement"] = "36協定"

    group_html = []
    for key in pattern_order:
        items = groups.get(key)
        if not items:
            continue
        lines = sum(a.lines for a in items)
        rows = "".join(
            f"""
            <div class="artifact">
              <div class="artifact-main">
                <code>{esc(a.path)}</code>
                <div class="artifact-desc">{esc(a.desc)}</div>
              </div>
              <div class="artifact-meta"><span class="kind {esc(a.kind.lower())}">{esc(a.kind)}</span><span class="lines">{a.lines}行</span></div>
            </div>
            """
            for a in items
        )
        group_html.append(
            f"""
            <div class="group">
              <div class="group-head">
                <div><span class="group-name">{esc(pattern_labels.get(key, key))}</span><span class="group-key">{esc(key)}</span></div>
                <span class="group-lines">{lines}行</span>
              </div>
              {rows}
            </div>
            """
        )

    spec_rows = []
    for no, line in enumerate(app.source.splitlines(), 1):
        if not line.strip():
            continue
        spec_rows.append(f'<div class="src-line"><span class="ln">{no}</span><span>{esc(line)}</span></div>')
    spec_html = "".join(spec_rows)

    preview_entity = app.entities[0] if app.entities else None
    preview_fields = [
        name
        for name in (preview_entity.fields if preview_entity else {})
        if name not in ("id", "status")
    ][:4]
    preview_rows = ""
    if preview_entity:
        for index, person in enumerate(("山田 太郎", "佐藤 花子", "鈴木 一郎"), 1):
            cells = "".join(
                f"<td>{esc(person if name in ('applicant', 'name') else sample_value(name, preview_entity.fields[name]))}</td>"
                for name in preview_fields
            )
            preview_rows += (
                f'<tr><td class="mono">#{1043 - index}</td>{cells}'
                f'<td><span class="pill approved">active</span></td></tr>'
            )

    approval = next((p for p in app.patterns if p.name == "approval"), None)
    if approval:
        human_panel = (
            '<p style="font-size:12.5px;color:var(--muted);margin-bottom:12px;">'
            '承認前と承認後を、ボタンで確かめられるデモを生成した。'
            '申請・承認・差し戻しのたびに、状態と承認後の処理が変わる。</p>'
            '<p><a class="demo-link" href="demo.html">承認デモを開く →</a></p>'
        )
    elif preview_entity:
        headers = "".join(f"<th>{esc(field)}</th>" for field in preview_fields)
        human_panel = (
            f'<table><thead><tr><th>ID</th>{headers}<th>状態</th></tr></thead>'
            f'<tbody>{preview_rows}</tbody></table>'
        )
    else:
        human_panel = '<p style="font-size:12.5px;color:var(--muted);">エンティティが定義されていない。</p>'

    if any(p.name == "scene" for p in app.patterns) and not errors:
        link = '<p><a class="demo-link" href="story.html">作品を開く →</a></p>'
        human_panel = human_panel + link if approval or preview_entity else link

    freedom_text = {
        "strict": "パターンのみ。最速・最も検証しやすい。",
        "guided": "パターン + custom。customにはwhyが必須。",
        "open": "パターン + 任意コード。自由度最大、検証はEtsahへ。",
    }.get(app.freedom, "")

    security_html = ""
    if security is not None:
        label_badges = "".join(
            f'<span class="sec-badge">{esc(label)}</span>' for label in security.labels
        ) or '<span class="sec-muted">ラベルなし</span>'
        sink_rows = "".join(
            f'<div class="sec-row"><code>{esc(effect)}</code>'
            f'<span class="sec-ok">accepts: {esc(", ".join(rule.get("accepts", [])) or "public only")}</span>'
            f'<span class="sec-no">rejects: {esc(", ".join(rule.get("rejects", [])) or "-")}</span></div>'
            for effect, rule in sorted(security.sinks.items())
        ) or '<div class="sec-muted">sink は宣言されていない。</div>'
        declassify_rows = "".join(
            f'<div class="sec-row"><code>{esc(rule["label"])}</code>'
            f'<span>scope: {esc(rule["in"])}</span>'
            f'<span>roles: {esc(", ".join(rule["roles"]))}</span></div>'
            for rule in security.declassify
        ) or '<div class="sec-muted">declassify は宣言されていない。</div>'
        if not security.checked:
            unresolved_html = '<div class="sec-muted">ラベルが宣言されていないため、情報フロー検査は対象外。</div>'
        elif security.unresolved:
            unresolved_html = (
                f'<div class="sec-warn">{len(security.unresolved)} 件の未解決事項</div>'
                + "".join(f'<div class="sec-row sec-muted">{esc(item)}</div>' for item in security.unresolved)
            )
        else:
            unresolved_html = '<div class="sec-ok">すべてのフローを静的検査済み</div>'
        security_html = f"""
  <div class="panel">
    <h2>情報フロー</h2>
    <div class="sec-row"><span class="sec-label">LABELS</span>{label_badges}</div>
    <div class="sec-row"><span class="sec-label">SINKS</span></div>
    {sink_rows}
    <div class="sec-row"><span class="sec-label">DECLASSIFY</span></div>
    {declassify_rows}
    {unresolved_html}
  </div>
"""

    css = """
    :root {
      --bg: #f4f1e9; --card: #fffdf8; --ink: #1e2328; --muted: #6d7280;
      --line: #e3ddd0; --green: #2f6f4e; --green-soft: #e7f2ea;
      --amber: #a85a08; --amber-soft: #fbf1e0; --red: #b3261e; --red-soft: #fbe9e7;
      --blue: #2454b8; --blue-soft: #e8eefb; --purple: #6335b8; --purple-soft: #f0eafb;
      --shadow: 0 1px 2px rgba(30,35,40,.04), 0 8px 24px rgba(30,35,40,.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--ink); line-height: 1.7;
      background: radial-gradient(1200px 500px at 50% -200px, #fbf8f1 0%, rgba(251,248,241,0) 70%), var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .wrap { max-width: 820px; margin: 0 auto; padding: 30px 20px 70px; }
    .kicker { font-family: ui-monospace, Menlo, monospace; font-size: 11px; letter-spacing: .16em; color: var(--green); margin-bottom: 10px; }
    h1 { font-size: 30px; line-height: 1.3; margin: 0 0 8px; letter-spacing: -.02em; }
    h2 { font-size: 15px; margin: 0 0 14px; }
    p { margin: 0 0 12px; }
    code { font-family: ui-monospace, Menlo, monospace; font-size: .88em; background: #f2eee4; padding: 2px 6px; border-radius: 5px; }
    .sub { color: var(--muted); font-size: 14px; margin-bottom: 16px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 22px; }
    .chip { font-size: 11px; padding: 4px 9px; border-radius: 999px; background: #fffefa; border: 1px solid var(--line); color: #4b5563; }
    .sec-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 7px 0; border-bottom: 1px dashed #ece6d9; font-size: 12px; }
    .sec-row:last-child { border-bottom: 0; }
    .sec-label { font-family: ui-monospace, Menlo, monospace; font-size: 9px; letter-spacing: .12em; color: var(--muted); min-width: 88px; }
    .sec-badge { font-family: ui-monospace, Menlo, monospace; font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 999px; background: var(--purple-soft); color: var(--purple); }
    .sec-ok { color: var(--green); }
    .sec-no { color: var(--red); }
    .sec-warn { color: var(--amber); font-weight: 650; margin-top: 6px; }
    .sec-muted { color: var(--muted); }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
    .metric { background: var(--card); border: 1px solid var(--line); border-radius: 13px; padding: 14px 15px; box-shadow: var(--shadow); }
    .metric .value { font-size: 26px; font-weight: 750; letter-spacing: -.03em; line-height: 1.2; }
    .metric .label { font-size: 10.5px; color: var(--muted); letter-spacing: .04em; }
    .metric .accent { color: var(--green); }
    .panel { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 22px; margin-top: 18px; box-shadow: var(--shadow); }
    .panel h2 { display: flex; align-items: center; gap: 8px; }
    .panel h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--green); display: inline-block; }
    .src { background: #20262b; border-radius: 10px; padding: 14px 0; overflow-x: auto; }
    .src-line { display: flex; gap: 14px; font-family: ui-monospace, Menlo, monospace; font-size: 11px; line-height: 1.75; color: #e8e4d9; padding: 0 16px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .src-line .ln { color: #6b7280; text-align: right; min-width: 26px; user-select: none; }
    .group { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; margin-bottom: 10px; background: #fffefa; }
    .group-head { display: flex; align-items: center; justify-content: space-between; padding: 11px 14px; background: #f8f5ee; border-bottom: 1px solid var(--line); }
    .group-name { font-weight: 700; font-size: 13.5px; }
    .group-key { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: var(--muted); margin-left: 8px; }
    .group-lines { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: var(--green); }
    .artifact { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 14px; border-bottom: 1px dashed #ece6d9; }
    .artifact:last-child { border-bottom: 0; }
    .artifact code { font-size: 11px; background: transparent; padding: 0; color: #334155; overflow-wrap: anywhere; }
    .artifact-desc { font-size: 11px; color: var(--muted); margin-top: 1px; }
    .artifact-meta { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
    .kind { font-family: ui-monospace, Menlo, monospace; font-size: 9px; padding: 2px 6px; border-radius: 4px; background: #efede7; color: #6d7280; }
    .kind.api { background: var(--blue-soft); color: var(--blue); }
    .kind.ui { background: var(--purple-soft); color: var(--purple); }
    .kind.sql { background: var(--amber-soft); color: var(--amber); }
    .kind.test { background: var(--green-soft); color: var(--green); }
    .lines { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: var(--muted); }
    .codeblock { background: #20262b; border-radius: 10px; padding: 16px; overflow-x: auto; margin-top: 10px; }
    .codeblock pre { margin: 0; color: #e8e4d9; font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
    table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    th { text-align: left; font-size: 10px; letter-spacing: .06em; color: var(--muted); font-weight: 600; padding: 8px 8px; border-bottom: 1px solid var(--line); }
    td { padding: 10px 8px; border-bottom: 1px solid #f0ebe0; }
    tr:last-child td { border-bottom: 0; }
    .mono { font-family: ui-monospace, Menlo, monospace; font-size: 11px; color: #4b5563; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .action { text-align: right; color: var(--green); font-size: 11px; font-weight: 650; }
    .demo-link { display: inline-block; background: var(--green); color: #fff; text-decoration: none; padding: 9px 14px; border-radius: 8px; font-size: 12.5px; font-weight: 650; }
    .pill { font-size: 10px; font-weight: 650; padding: 3px 8px; border-radius: 999px; }
    .pill.paid { background: var(--green-soft); color: var(--green); }
    .pill.pending { background: var(--amber-soft); color: var(--amber); }
    .pill.refunded { background: #efede7; color: var(--muted); }
    .dial { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .mode { border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: #fffefa; }
    .mode.active { border-color: #bcd9c6; background: var(--green-soft); }
    .mode .name { font-family: ui-monospace, Menlo, monospace; font-size: 10px; letter-spacing: .08em; color: var(--muted); margin-bottom: 3px; }
    .mode .title { font-weight: 700; font-size: 13px; margin-bottom: 2px; }
    .mode .desc { font-size: 10.5px; color: var(--muted); line-height: 1.5; }
    .footer { margin-top: 24px; font-size: 11px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 16px; }
    .hash { font-family: ui-monospace, Menlo, monospace; background: #eae5d9; padding: 3px 7px; border-radius: 5px; }
    @media (max-width: 640px) {
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .dial { grid-template-columns: 1fr; }
      h1 { font-size: 25px; }
      .wrap { padding: 24px 15px 56px; }
      .panel { padding: 18px 16px; }
    }
    """

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Etsah Patterns — {esc(app.name)}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">Etsah PATTERNS / BUILD REPORT</div>
  <h1>{esc(app.name)}</h1>
  <div class="sub">パターンから展開されたアプリケーション</div>
  <div class="chips">{''.join(f'<span class="chip">{esc(s)}</span>' for s in app.stack)}<span class="chip">freedom: {esc(app.freedom)}</span></div>

  <div class="metrics">
    <div class="metric"><div class="value">{spec}</div><div class="label">SPEC LINES</div></div>
    <div class="metric"><div class="value accent">{total_lines:,}</div><div class="label">GENERATED LINES (EST.)</div></div>
    <div class="metric"><div class="value">{ratio}×</div><div class="label">EXPANSION</div></div>
    <div class="metric"><div class="value">{patterns}</div><div class="label">PATTERNS / {len(errors)} ERRORS</div></div>
  </div>

  <div class="panel">
    <h2>AIが書いたもの</h2>
    <div class="src">{spec_html}</div>
  </div>

  <div class="panel">
    <h2>展開されたもの</h2>
    {''.join(group_html)}
  </div>

  {security_html}

  <div class="panel">
    <h2>人間が見るもの</h2>
    {human_panel}
  </div>

  <div class="panel">
    <h2>自由度ダイヤル</h2>
    <p style="font-size:13px;color:var(--muted);">「速いが縛られる」と「自由だが遅い」を、プロジェクトごとに選ぶ。現在は <strong>{esc(app.freedom)}</strong>。{esc(freedom_text)}</p>
    <div class="dial">
      <div class="mode {'active' if app.freedom == 'strict' else ''}"><div class="name">STRICT</div><div class="title">パターンのみ</div><div class="desc">最速。全生成物がパターン由来で、検証が最も簡単。</div></div>
      <div class="mode {'active' if app.freedom == 'guided' else ''}"><div class="name">GUIDED</div><div class="title">パターン + custom</div><div class="desc">customにはwhyが必須。既存業務ロジックとの接続向け。</div></div>
      <div class="mode {'active' if app.freedom == 'open' else ''}"><div class="name">OPEN</div><div class="title">任意コード</div><div class="desc">自由度最大。検証はEtsahの契約層に委ねる。</div></div>
    </div>
  </div>

  <div class="footer">
    <span>Spec → patterns → artifacts. {spec} lines in, {total_lines:,} lines out (estimated).</span>
    <span>AST <span class="hash">{esc(app.name)}-{spec}-{total_lines}</span></span>
  </div>
</div>
</body>
</html>
"""


def sample_value(name: str, typ: str) -> str:
    n = name.lower()
    t = typ.lower()
    if "email" in n or t == "email":
        return "taro@example.com"
    if "end" in n and ("date" in n or t == "date"):
        return "2026-12-09"
    if "date" in n or t == "date":
        return "2026-12-08"
    if "amount" in n or "price" in n or "total" in n or t == "money":
        return "48000"
    if "quantity" in n or "count" in n:
        return "3"
    if "department" in n:
        return "営業部"
    if "item" in n:
        return "ノートPC"
    if "applicant" in n:
        return "山田 太郎"
    if "destination" in n:
        return "大阪"
    if "purpose" in n or "reason" in n:
        return "顧客訪問"
    if "note" in n or "memo" in n:
        return "新規取引先との打ち合わせ"
    if "model" in n:
        return "ThinkPad X1"
    if "name" in n:
        return "山田 太郎"
    return "サンプル"


def render_story(scenes: list[PatternUse]) -> str:
    start = scenes[0]
    sections = []
    # Keep the default scene last so a targeted sibling can hide it without JS.
    for scene in [*scenes[1:], start]:
        choices = scene_choices(scene) or [("最初から", start.target)]
        links = "".join(
            f'<a href="#scene-{esc(target)}">{esc(label)}</a>'
            for label, target in choices
        )
        sections.append(
            f'<section id="scene-{esc(scene.target)}" tabindex="-1">'
            f'<h1>{esc(scene.slots["title"])}</h1><p>{esc(scene.slots["body"])}</p>'
            f'<nav aria-label="選択肢">{links}</nav></section>'
        )
    css = """
    body{margin:0;background:#f6f2e9;color:#243b38;font:1.15rem/1.8 system-ui,sans-serif}
    main{max-width:36rem;margin:10vh auto;padding:1.5rem}
    h1{line-height:1.3}section{display:none}section:last-child,section:target{display:block}
    section:target~section:last-child{display:none}
    nav{display:grid;gap:.7rem;margin-top:2rem}
    a{display:block;padding:.6rem 1rem;background:#234e48;color:white;border-radius:.4rem}
    """
    return (
        '<!doctype html><html lang="ja"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(start.slots["title"])}</title><style>{css}</style>'
        f'<main>{"".join(sections)}</main></html>'
    )


def render_demo(app: App, arts: list[Artifact]) -> str:
    """Generate a dependency-free, interactive approval demo for humans."""
    approval = next((p for p in app.patterns if p.name == "approval"), None)
    if approval is None:
        return ""

    entity_name = approval.slots.get("entity") or approval.target
    entity = next((e for e in app.entities if e.name == entity_name), None)
    approver = approval.slots.get("approver", "approver")
    on_approved = slot_value(approval.slots.get("on_approved", ""))
    custom = next((c for c in app.customs if c.name == on_approved), None)
    hook_use = next((p for p in app.patterns if p.name == on_approved), None)
    if custom and custom.why:
        action = custom.why
    elif hook_use and on_approved == "notify":
        channel = slot_value(hook_use.slots.get("channel", "email"))
        recipient = slot_value(hook_use.slots.get("recipient", "approver"))
        template = slot_value(hook_use.slots.get("template", ""))
        action = f"{channel} で {recipient} ロールに承認結果を通知する"
        if template:
            action += f"（テンプレート: {template}）"
    elif on_approved:
        action = f"{on_approved} を実行する"
    else:
        action = "承認後の処理は未定義"

    fields = []
    if entity:
        for name, typ in entity.fields.items():
            if name in ("id", "status"):
                continue
            fields.append({"name": name, "type": typ, "value": sample_value(name, typ)})

    fields_html = "".join(
        f'<label class="field"><span>{esc(f["name"])} <em>{esc(f["type"])}</em></span>'
        f'<input value="{esc(f["value"])}" data-field="{esc(f["name"])}"></label>'
        for f in fields
    )

    data = {
        "app": app.name,
        "entity": entity_name,
        "approver": approver,
        "onApproved": on_approved,
        "action": action,
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    demo_css = """
    :root {
      --bg: #f4f1e9; --card: #fffdf8; --ink: #1e2328; --muted: #6d7280;
      --line: #e3ddd0; --green: #2f6f4e; --green-soft: #e7f2ea;
      --amber: #a85a08; --amber-soft: #fbf1e0; --red: #b3261e; --red-soft: #fbe9e7;
      --blue: #2454b8; --blue-soft: #e8eefb;
      --shadow: 0 1px 2px rgba(30,35,40,.04), 0 8px 24px rgba(30,35,40,.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--ink); line-height: 1.7;
      background: radial-gradient(1200px 500px at 50% -200px, #fbf8f1 0%, rgba(251,248,241,0) 70%), var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .wrap { max-width: 980px; margin: 0 auto; padding: 30px 20px 64px; }
    .kicker { font-family: ui-monospace, Menlo, monospace; font-size: 11px; letter-spacing: .16em; color: var(--green); margin-bottom: 10px; }
    h1 { font-size: 32px; line-height: 1.25; margin: 0 0 6px; letter-spacing: -.02em; }
    .sub { color: var(--muted); font-size: 14px; margin: 0 0 22px; }
    .sub code { background: #eae5d9; padding: 2px 6px; border-radius: 5px; font-size: .88em; }
    .grid { display: grid; grid-template-columns: 1.12fr .88fr; gap: 16px; align-items: start; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px; box-shadow: var(--shadow); margin-bottom: 16px; }
    .card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 14px; }
    .card-head strong { font-size: 15px; }
    .card-head code { font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; background: #f2eee4; padding: 3px 7px; border-radius: 5px; color: #4b5563; }
    .field { display: block; margin-bottom: 10px; }
    .field span { display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
    .field em { font-style: normal; font-family: ui-monospace, Menlo, monospace; font-size: 9px; color: #8a8f98; }
    .field input { width: 100%; border: 1px solid var(--line); background: #fffefa; border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 13px; color: var(--ink); }
    .field input:focus { outline: 2px solid #bcd9c6; outline-offset: 1px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
    button { border: 0; border-radius: 8px; padding: 9px 14px; font: inherit; font-size: 12.5px; font-weight: 650; cursor: pointer; background: var(--green); color: #fff; transition: transform .08s ease, opacity .15s ease; }
    button:active { transform: translateY(1px); }
    button:disabled { opacity: .32; cursor: not-allowed; }
    button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--line); }
    .pill { font-family: ui-monospace, Menlo, monospace; font-size: 10px; font-weight: 700; padding: 4px 9px; border-radius: 999px; letter-spacing: .04em; }
    .pill.draft { background: #efede7; color: #6d7280; }
    .pill.pending { background: var(--amber-soft); color: var(--amber); }
    .pill.approved { background: var(--green-soft); color: var(--green); }
    .pill.returned { background: var(--red-soft); color: var(--red); }
    .pill.rejected { background: var(--red-soft); color: var(--red); }
    .flow { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 11.5px; color: #4b5563; margin-bottom: 14px; }
    .flow span { background: #f8f5ee; border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; }
    .flow i { color: #9aa0a8; font-style: normal; }
    .action-box { background: var(--green-soft); border: 1px solid #cfe3d6; border-radius: 12px; padding: 14px; }
    .action-box .label { font-size: 10px; letter-spacing: .1em; color: var(--green); font-weight: 700; margin-bottom: 4px; }
    .action-text { font-size: 13px; font-weight: 650; }
    .state { display: inline-block; margin-top: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 10px; padding: 3px 8px; border-radius: 999px; background: #fffefa; color: var(--muted); border: 1px solid var(--line); }
    .state.done { background: var(--green); color: #fff; border-color: var(--green); }
    .log { list-style: none; margin: 0; padding: 0; }
    .log li { display: flex; align-items: baseline; gap: 10px; padding: 8px 0; border-bottom: 1px dashed #ece6d9; font-size: 12px; }
    .log li:last-child { border-bottom: 0; }
    .log .time { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: #8a8f98; min-width: 42px; }
    .log .n { margin-left: auto; font-family: ui-monospace, Menlo, monospace; font-size: 9px; color: #b0b4ba; }
    .log li.ok span:nth-child(2) { color: var(--green); font-weight: 650; }
    .log li.warn span:nth-child(2) { color: var(--amber); }
    .log li.empty { color: var(--muted); font-size: 11.5px; }
    .compare { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .compare-col { border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: #fffefa; opacity: .55; transition: opacity .2s ease, border-color .2s ease, background .2s ease; }
    .compare-col.active { opacity: 1; border-color: #bcd9c6; background: var(--green-soft); }
    .compare-label { font-size: 10px; letter-spacing: .1em; color: var(--muted); font-weight: 700; margin-bottom: 8px; }
    .compare-col ul { margin: 0; padding-left: 16px; font-size: 11.5px; color: #4b5563; }
    .compare-col li { margin: 4px 0; }
    .stats { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 18px; }
    .stat { background: #fffefa; border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font-size: 10.5px; color: var(--muted); }
    .stat b { color: var(--ink); font-weight: 700; }
    .footer { margin-top: 8px; font-size: 11px; color: var(--muted); }
    @media (max-width: 760px) {
      .grid { grid-template-columns: 1fr; }
      h1 { font-size: 26px; }
      .wrap { padding: 24px 15px 56px; }
      .card { padding: 16px; }
      .compare { grid-template-columns: 1fr; }
    }
    """

    demo_js = """
    const statusEl = document.getElementById("status");
    const submitEl = document.getElementById("submit");
    const approveEl = document.getElementById("approve");
    const returnEl = document.getElementById("return");
    const rejectEl = document.getElementById("reject");
    const resetEl = document.getElementById("reset");
    const actionStateEl = document.getElementById("action-state");
    const logEl = document.getElementById("log");
    const beforeCol = document.getElementById("before-col");
    const afterCol = document.getElementById("after-col");
    const auditCountEl = document.getElementById("audit-count");

    let state = { status: "draft", fired: false, log: [] };

    function addLog(text, tone) {
      state.log.push({
        text: text,
        tone: tone || "",
        time: new Date().toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })
      });
      render();
    }

    function render() {
      statusEl.textContent = state.status;
      statusEl.className = "pill " + state.status;
      submitEl.disabled = state.status !== "draft";
      approveEl.disabled = state.status !== "pending";
      returnEl.disabled = state.status !== "pending";
      rejectEl.disabled = state.status !== "pending";
      actionStateEl.textContent = state.fired ? "実行済み" : "未実行";
      actionStateEl.className = "state" + (state.fired ? " done" : "");
      beforeCol.classList.toggle("active", state.status !== "approved");
      afterCol.classList.toggle("active", state.status === "approved");
      auditCountEl.textContent = String(state.log.length);
      document.querySelectorAll("input[data-field]").forEach(function (el) {
        el.disabled = state.status !== "draft";
      });
      if (state.log.length === 0) {
        logEl.innerHTML = '<li class="empty">まだ操作はありません。「申請する」を押してください。</li>';
        return;
      }
      logEl.innerHTML = state.log.map(function (entry, index) {
        return '<li class="' + entry.tone + '"><span class="time">' + entry.time +
          '</span><span>' + entry.text + '</span><span class="n">' +
          String(index + 1).padStart(2, "0") + '</span></li>';
      }).join("");
    }

    submitEl.addEventListener("click", function () {
      state.status = "pending";
      addLog("申請者が申請を送信しました", "info");
      addLog("承認者（" + DATA.approver + "）に承認依頼を送りました", "info");
    });

    approveEl.addEventListener("click", function () {
      state.status = "approved";
      addLog("承認者が申請を承認しました", "ok");
      state.fired = true;
      addLog("承認後の処理を実行: " + DATA.action, "ok");
    });

    returnEl.addEventListener("click", function () {
      state.status = "returned";
      addLog("承認者が申請を差し戻しました", "warn");
    });

    rejectEl.addEventListener("click", function () {
      state.status = "rejected";
      addLog("承認者が申請を却下しました", "warn");
    });

    resetEl.addEventListener("click", function () {
      state = { status: "draft", fired: false, log: [] };
      render();
    });

    render();
    """

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Etsah Patterns — {esc(app.name)} / approval demo</title>
<style>{demo_css}</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">Etsah PATTERNS / HUMAN VIEW</div>
  <h1>{esc(app.name)}</h1>
  <p class="sub">承認前と承認後を、ボタンで確かめる。仕様の <code>approval</code> と <code>{esc(on_approved) or "on_approved"}</code> が、この画面の動きに対応している。</p>

  <div class="stats">
    <span class="stat">対象 <b>{esc(entity_name)}</b></span>
    <span class="stat">承認者 <b>{esc(approver)}</b></span>
    <span class="stat">監査ログ <b id="audit-count">0</b> 件</span>
  </div>

  <div class="grid">
    <section class="card">
      <div class="card-head"><strong>申請</strong><span id="status" class="pill draft">draft</span></div>
      {fields_html}
      <div class="actions">
        <button id="submit">申請する</button>
        <button id="approve" disabled>承認する</button>
        <button id="return" class="ghost" disabled>差し戻す</button>
        <button id="reject" class="ghost" disabled>却下する</button>
        <button id="reset" class="ghost">リセット</button>
      </div>
    </section>

    <div>
      <section class="card">
        <div class="card-head"><strong>承認フロー</strong><code>{esc(approver)}</code></div>
        <div class="flow">
          <span>申請</span><i>→</i><span>{esc(approver)} が承認</span><i>→</i><span>{esc(action)}</span>
        </div>
        <div class="action-box">
          <div class="label">ON_APPROVED</div>
          <div class="action-text">{esc(action)}</div>
          <div id="action-state" class="state">未実行</div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><strong>履歴</strong><span style="font-size:11px;color:var(--muted)">操作するたびに増える</span></div>
        <ol id="log" class="log"><li class="empty">まだ操作はありません。「申請する」を押してください。</li></ol>
      </section>
    </div>
  </div>

  <section class="card">
    <div class="card-head"><strong>承認前後の差分</strong><span style="font-size:11px;color:var(--muted)">状態に応じて強調される</span></div>
    <div class="compare">
      <div class="compare-col active" id="before-col">
        <div class="compare-label">承認前 / BEFORE</div>
        <ul>
          <li>申請は下書き、または承認待ち</li>
          <li>承認後の処理はまだ動かない</li>
          <li>監査ログは申請と承認依頼だけ</li>
        </ul>
      </div>
      <div class="compare-col" id="after-col">
        <div class="compare-label">承認後 / AFTER</div>
        <ul>
          <li>状態は <code>approved</code></li>
          <li>{esc(action)}</li>
          <li>監査ログに承認と処理を記録</li>
        </ul>
      </div>
    </div>
  </section>

  <div class="footer">Etsah Patterns / generated from the spec. No server, no external dependencies.</div>
</div>
<script>
const DATA = {data_json};
{demo_js}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Discovery and benchmark
# ---------------------------------------------------------------------------

def find_patterns(query: str, registry: dict[str, PatternManifest]) -> list[tuple[int, PatternManifest]]:
    tokens = [t.lower() for t in re.split(r"\s+", query) if t]
    results: list[tuple[int, PatternManifest]] = []
    for m in registry.values():
        score = 0
        name = m.name.lower()
        intent = m.intent.lower()
        tags = [t.lower() for t in m.tags]
        slots = [s.lower() for s in m.slots]
        descriptions = [str(v.get("desc", "")).lower() for v in m.slots.values()]
        for token in tokens:
            # Match in both directions for short registry terms (names,
            # tags, slots): a natural request like 「申請を承認したい」
            # arrives as one token, and the tags 申請 / 承認 appear inside
            # it. Sentences (intent, descriptions) only match token-in-text,
            # so a long token cannot swallow a whole description.
            # Very short ASCII tokens ("a", "I", "to") are stopwords, not
            # search terms: they only count on an exact term match.
            short_ascii = len(token) <= 2 and token.isascii()

            def hit(term: str) -> bool:
                if short_ascii:
                    return token == term
                return token in term or (len(term) >= 2 and term in token)

            if hit(name):
                score += 5
            if not short_ascii and token in intent:
                score += 3
            for tag in tags:
                if hit(tag):
                    score += 3
            for slot in slots:
                if hit(slot):
                    score += 1
            if not short_ascii:
                for description in descriptions:
                    if token in description:
                        score += 1
        if score:
            results.append((score, m))
    return sorted(results, key=lambda x: (-x[0], x[1].name))


def bench_rows(registry: dict[str, PatternManifest]) -> list[dict]:
    cases = [
        ("expense_before", ROOT / "bench" / "expense_before.etsah"),
        ("expense_after", ROOT / "bench" / "expense_after.etsah"),
        ("travel_request", ROOT / "examples" / "travel_request.etsah"),
        ("equipment_purchase", ROOT / "examples" / "equipment_purchase.etsah"),
        ("pc_lending", ROOT / "bench" / "pc_lending.etsah"),
    ]
    rows = []
    for name, path in cases:
        app = parse_app(path.read_text(encoding="utf-8"))
        diags = validate(app, registry)
        arts = expand(app, registry)
        spec = spec_lines(app.source)
        custom = sum(c.lines for c in app.customs)
        ai_lines = spec + custom
        generated = sum(a.lines for a in arts)
        rows.append(
            {
                "case": name,
                "spec_lines": spec,
                "custom_lines": custom,
                "ai_authored_lines": ai_lines,
                "generated_lines": generated,
                "reuse_ratio": round(generated / ai_lines, 1) if ai_lines else 0,
                "patterns": [p.name for p in app.patterns],
                "errors": len([d for d in diags if d.severity == "error"]),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    text = Path(args.file).read_text(encoding="utf-8")
    try:
        app = parse_app(text)
    except ValueError as ex:
        print(f"[ERROR] {ex}", file=sys.stderr)
        return 2

    diags = validate(app, registry)
    security = analyze_security(app, registry)
    arts = expand(app, registry)
    total = sum(a.lines for a in arts)
    spec = spec_lines(text)

    print(f"Etsah Patterns: {app.name}")
    print(f"stack: {', '.join(app.stack)}  freedom: {app.freedom}")
    print(f"entities: {len(app.entities)}  patterns: {len(app.patterns)}  custom: {len(app.customs)}")
    print(f"spec lines: {spec}  generated lines: {total:,}  expansion: {round(total/spec, 1) if spec else 0}x")
    if security.checked:
        print(
            f"information flow: labels {', '.join(security.labels) or '-'}  "
            f"flows {len(security.flows)}  guarantees {len(security.guarantees)}  "
            f"unresolved {len(security.unresolved)}"
        )
    else:
        print("information flow: skipped (no labels declared)")
    if diags:
        print()
        for d in diags:
            print(d.render())
    else:
        print("OK — no issues")
    checks = staffing_rules.runtime_checks(app)
    rendered_checks = staffing_rules.format_checks(checks)
    if rendered_checks:
        print()
        print(rendered_checks)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = render_report(app, diags, arts, security)
    (out / "report.html").write_text(report, encoding="utf-8")
    has_errors = any(d.severity == "error" for d in diags)
    demo = "" if has_errors else render_demo(app, arts)
    if demo:
        (out / "demo.html").write_text(demo, encoding="utf-8")

    scenes = [p for p in app.patterns if p.name == "scene"]
    story = out / "story.html"
    if scenes and not has_errors:
        story.write_text(render_story(scenes), encoding="utf-8")
        print(f"wrote {story}")
    else:
        story.unlink(missing_ok=True)

    manifest = {
        "app": app.name,
        "stack": app.stack,
        "freedom": app.freedom,
        "spec_lines": spec,
        "generated_lines": total,
        "patterns": [{"name": p.name, "target": p.target, "slots": p.slots} for p in app.patterns],
        "artifacts": [
            {"pattern": a.pattern, "path": a.path, "kind": a.kind, "lines": a.lines, "desc": a.desc}
            for a in arts
        ],
        "diagnostics": [d.render() for d in diags],
        "runtime_checks": checks,
        "security": {
            "labels": security.labels,
            "flows": len(security.flows),
            "guarantees": len(security.guarantees),
            "unresolved": len(security.unresolved),
            "verified": not has_errors,
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    security_json = {
        "schema_version": 1,
        "ast_hash": app_hash(app),
        "labels": security.labels,
        "flows": security.flows,
        "sinks": security.sinks,
        "declassify": security.declassify,
        "guarantees": security.guarantees,
        "unresolved": security.unresolved,
        "verified": not has_errors,
    }
    (out / "security.json").write_text(
        json.dumps(security_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {out / 'report.html'}")
    if demo:
        print(f"wrote {out / 'demo.html'}")
    elif has_errors:
        print("demo.html skipped because the spec has errors")
    print(f"wrote {out / 'manifest.json'}")
    print(f"wrote {out / 'security.json'}")
    return 1 if any(d.severity == "error" for d in diags) else 0


def cmd_find(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    results = find_patterns(args.query, registry)
    if not results:
        print(f"no patterns matched {args.query!r}")
        return 1
    shown = results[:3]
    print(f"query: {args.query!r}  ({len(results)} match(es), showing {len(shown)})")
    for score, m in shown:
        required = [k for k, v in m.slots.items() if v.get("required")]
        optional = [k for k, v in m.slots.items() if not v.get("required")]
        print()
        print(f"  {m.name}  v{m.version}  score={score}")
        print(f"    intent: {m.intent}")
        print(f"    required: {', '.join(required) or '-'}")
        print(f"    optional: {', '.join(optional) or '-'}")
        print(f"    effects: {', '.join(m.effects) or '-'}")
        print(f"    tags: {', '.join(m.tags)}")
        if m.examples:
            print(f"    examples: {', '.join(m.examples)}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    registry_dir = Path(args.registry)
    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    m = manifest_from_dict(data)
    errors = validate_manifest(m)
    if errors:
        print("[ERROR] invalid manifest:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    dest = registry_dir / f"{m.name}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = write_index(registry_dir)
    print(f"added {m.name} v{m.version} -> {dest}")
    print(f"updated {index}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    out = write_index(Path(args.registry))
    registry = load_registry(Path(args.registry))
    print(f"indexed {len(registry)} pattern(s) -> {out}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry))
    rows = bench_rows(registry)
    print(f"{'case':<16} {'spec':>5} {'custom':>7} {'AI lines':>9} {'generated':>10} {'reuse':>7}  patterns")
    for r in rows:
        print(
            f"{r['case']:<16} {r['spec_lines']:>5} {r['custom_lines']:>7} "
            f"{r['ai_authored_lines']:>9} {r['generated_lines']:>10} "
            f"{r['reuse_ratio']:>6}x  {', '.join(r['patterns'])}"
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="etsahc")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="validate and expand an app spec")
    b.add_argument("file")
    b.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    b.add_argument("--out", default=str(ROOT / "build"))
    b.set_defaults(func=cmd_build)

    f = sub.add_parser("find", help="search the registry")
    f.add_argument("query")
    f.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    f.set_defaults(func=cmd_find)

    a = sub.add_parser("add", help="add a pattern manifest to the registry")
    a.add_argument("manifest")
    a.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    a.set_defaults(func=cmd_add)

    i = sub.add_parser("index", help="rebuild registry/index.json")
    i.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    i.set_defaults(func=cmd_index)

    be = sub.add_parser("bench", help="compare apps with and without reusable patterns")
    be.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    be.add_argument("--out", default=str(ROOT / "bench" / "report.json"))
    be.set_defaults(func=cmd_bench)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
