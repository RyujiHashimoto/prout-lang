"""Pattern-layer information flow: labels, data_policy, and security.json."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
import etsahc
from etsahc import (
    DEFAULT_REGISTRY,
    analyze_security,
    app_hash,
    load_registry,
    manifest_from_dict,
    parse_app,
    validate,
    validate_manifest,
)
from support import PATTERNS, snippet


def _quiet(func, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return func(*args)


def _codes(diags):
    return [d.code for d in diags]


def _manifest(name: str, **overrides):
    data = {
        "name": name,
        "version": "0.1.0",
        "intent": "do a thing",
        "slots": overrides.pop("slots", {"entity": {"type": "entity", "required": True}}),
        "effects": overrides.pop("effects", ["read(entity)"]),
        "artifacts": [{"path": "a.ts", "kind": "TS", "lines": 1, "desc": "a"}],
        "examples": [],
        "provenance": {},
    }
    data.update(overrides)
    return manifest_from_dict(data)


class TestPolicySyntax(unittest.TestCase):
    def test_entity_field_labels(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii @internal
                    name: String
                """
            )
        )
        user = app.entities[0]
        self.assertEqual(user.field_labels, {"email": ["pii", "internal"]})
        self.assertEqual(user.fields["email"], "Email")

    def test_policy_block(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  policy
                    labels pii, secret
                    sink net(email) accepts pii, internal rejects secret
                    sink net(*) rejects secret
                    declassify pii in masked_email require role support, admin
                """
            )
        )
        self.assertEqual(app.policy.labels, ["pii", "secret"])
        self.assertEqual(
            app.policy.sinks["net(email)"],
            {"accepts": ["pii", "internal"], "rejects": ["secret"]},
        )
        self.assertEqual(
            app.policy.declassify,
            [{"label": "pii", "in": "masked_email", "roles": ["support", "admin"]}],
        )

    def test_bad_sink_rule_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_app(
                snippet(
                    """
                    app demo
                      stack nextjs
                      policy
                        sink email accepts pii
                    """
                )
            )

    def test_sink_rule_needs_accepts_or_rejects(self) -> None:
        with self.assertRaises(ValueError):
            parse_app(
                snippet(
                    """
                    app demo
                      stack nextjs
                      policy
                        sink net(email)
                    """
                )
            )

    def test_bad_declassify_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_app(
                snippet(
                    """
                    app demo
                      stack nextjs
                      policy
                        declassify pii masked
                    """
                )
            )


class TestManifestPolicy(unittest.TestCase):
    def test_data_policy_round_trip(self) -> None:
        policy = {
            "labels": ["pii", "secret"],
            "sources": {"entity": ["pii"]},
            "sinks": {"net(email)": {"accepts": ["pii"]}},
            "rejects": {"net(*)": ["secret"]},
            "declassify": [{"label": "pii", "in": "masked", "roles": ["support"]}],
        }
        manifest = _manifest("demo", data_policy=policy)
        self.assertEqual(manifest.data_policy, policy)
        self.assertEqual(validate_manifest(manifest), [])

    def test_undeclared_label_in_sink_is_rejected(self) -> None:
        manifest = _manifest(
            "demo",
            data_policy={
                "labels": ["pii"],
                "sinks": {"net(email)": {"accepts": ["secret"]}},
            },
        )
        self.assertTrue(any("undeclared label" in error for error in validate_manifest(manifest)))

    def test_bad_sink_effect_is_rejected(self) -> None:
        manifest = _manifest(
            "demo",
            data_policy={"labels": ["pii"], "sinks": {"email": {"accepts": ["pii"]}}},
        )
        self.assertTrue(any("not an effect" in error for error in validate_manifest(manifest)))

    def test_index_summarizes_policy(self) -> None:
        manifest = _manifest(
            "demo",
            data_policy={"labels": ["pii"], "sinks": {"net(email)": {"accepts": ["pii"]}}},
        )
        index = manifest.to_index()
        self.assertEqual(index["data_policy"], {"labels": ["pii"], "sinks": ["net(email)"]})


class TestPolicyMerge(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_registry(DEFAULT_REGISTRY)

    def test_safe_example_has_flows_and_guarantees(self) -> None:
        app = parse_app((PATTERNS / "examples" / "data_policy.etsah").read_text(encoding="utf-8"))
        diags = validate(app, self.registry)
        self.assertEqual([d for d in diags if d.severity == "error"], [])
        security = analyze_security(app, self.registry)
        self.assertTrue(security.checked)
        self.assertEqual(security.labels, ["internal", "pii", "secret"])
        self.assertEqual(len(security.flows), 2)
        self.assertIn("no pii to emit(audit)", security.guarantees)
        self.assertIn("no secret to net(*)", security.guarantees)
        self.assertTrue(any("custom reimburse" in item for item in security.unresolved))

    def test_leak_example_fails_closed(self) -> None:
        app = parse_app(
            (PATTERNS / "examples" / "data_policy_leak.etsah").read_text(encoding="utf-8")
        )
        diags = validate(app, self.registry)
        errors = [d for d in diags if d.severity == "error"]
        self.assertEqual(_codes(errors), ["E-POLICY"])
        self.assertIn("notify(manager)", errors[0].message)

    def test_pattern_without_data_policy_fails_closed(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii
                  pattern csv_export Users
                    entity: User
                    columns: [email]
                """
            )
        )
        codes = _codes(validate(app, self.registry))
        self.assertIn("E-POLICY", codes)

    def test_unlabeled_app_skips_policy_checks(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email
                  pattern notify
                    channel: email
                """
            )
        )
        security = analyze_security(app, self.registry)
        self.assertFalse(security.checked)
        self.assertEqual(security.labels, [])
        self.assertEqual(security.unresolved, [])
        self.assertEqual(security.guarantees, ["no labeled data is declared"])
        self.assertEqual(_codes(validate(app, self.registry)), [])

    def test_labels_follow_entity_references(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity Employee
                    id: Id
                    email: Email @pii
                  entity Expense
                    id: Id
                    applicant: Employee
                    note: String @internal
                  pattern crud Expenses
                    entity: Expense
                """
            )
        )
        security = analyze_security(app, self.registry)
        self.assertEqual(security.flows[0]["labels"], ["internal", "pii"])

    def test_missing_app_sink_rule_is_an_error(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii
                  policy
                    labels pii, internal
                    sink net(*) rejects secret
                  pattern notify
                    channel: email
                """
            )
        )
        errors = [d for d in validate(app, self.registry) if d.severity == "error"]
        self.assertTrue(errors)
        self.assertTrue(any("does not accept" in error.message for error in errors))

    def test_wildcard_accepts_policy(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii
                  policy
                    labels pii, internal
                    sink net(*) accepts pii, internal
                  pattern notify
                    channel: email
                """
            )
        )
        self.assertEqual(
            [d for d in validate(app, self.registry) if d.severity == "error"], []
        )

    def test_reject_conflict_is_an_error(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @secret
                  policy
                    labels secret
                    sink net(*) accepts secret rejects secret
                  pattern notify
                    channel: email
                """
            )
        )
        errors = [d for d in validate(app, self.registry) if d.severity == "error"]
        self.assertTrue(any("accepts and rejects" in error.message for error in errors))

    def test_wildcard_reject_cannot_be_overridden(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    token: String @secret
                  policy
                    labels secret
                    sink net(email) accepts secret
                    sink net(*) rejects secret
                """
            )
        )
        errors = [d for d in validate(app, self.registry) if d.severity == "error"]
        self.assertTrue(any("accepts and rejects" in error.message for error in errors))
        self.assertIn("net(email)", " ".join(error.message for error in errors))

    def test_unknown_policy_label_is_an_error(self) -> None:
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii
                  policy
                    labels pii
                    sink net(email) accepts secret
                """
            )
        )
        errors = [d for d in validate(app, self.registry) if d.severity == "error"]
        self.assertTrue(any("not declared" in error.message for error in errors))

    def test_declassify_requires_app_permission(self) -> None:
        policy = {
            "labels": ["pii"],
            "sources": {"entity": ["pii"]},
            "sinks": {"net(email)": {"accepts": ["pii"]}},
            "declassify": [{"label": "pii", "in": "masked", "roles": ["support"]}],
        }
        registry = {"mask": _manifest("mask", data_policy=policy)}
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii
                  policy
                    labels pii
                    sink net(email) accepts pii
                  pattern mask Users
                    entity: User
                """
            )
        )
        codes = _codes(validate(app, registry))
        self.assertIn("E-POLICY", codes)

    def test_declassify_matches_label_scope_and_role(self) -> None:
        policy = {
            "labels": ["pii"],
            "sources": {"entity": ["pii"]},
            "sinks": {"net(email)": {"accepts": ["pii"]}},
            "declassify": [{"label": "pii", "in": "masked", "roles": ["support"]}],
        }
        registry = {"mask": _manifest("mask", data_policy=policy)}
        app = parse_app(
            snippet(
                """
                app demo
                  stack nextjs
                  entity User
                    id: Id
                    email: Email @pii
                  policy
                    labels pii
                    sink net(email) accepts pii
                    declassify pii in masked require role support
                  pattern mask Users
                    entity: User
                """
            )
        )
        self.assertEqual([d for d in validate(app, registry) if d.severity == "error"], [])
        security = analyze_security(app, registry)
        self.assertEqual(
            security.declassify,
            [{"label": "pii", "in": "masked", "roles": ["support"]}],
        )

    def test_hash_is_stable_and_sensitive(self) -> None:
        one = parse_app("app demo\n  stack nextjs\n  entity User\n    id: Id\n")
        two = parse_app("app demo\n  stack nextjs\n  entity User\n    id: Id\n")
        three = parse_app("app demo\n  stack nextjs\n  entity User\n    id: Id\n    email: Email @pii\n")
        self.assertEqual(app_hash(one), app_hash(two))
        self.assertNotEqual(app_hash(one), app_hash(three))
        self.assertRegex(app_hash(one), r"^[0-9a-f]{12}$")


class TestSecurityJson(unittest.TestCase):
    def test_build_writes_security_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = _quiet(
                etsahc.cmd_build,
                argparse.Namespace(
                    file=str(PATTERNS / "examples" / "data_policy.etsah"),
                    registry=str(DEFAULT_REGISTRY),
                    out=str(out),
                ),
            )
            self.assertEqual(code, 0)
            security = json.loads((out / "security.json").read_text(encoding="utf-8"))
            self.assertEqual(security["schema_version"], 1)
            self.assertRegex(security["ast_hash"], r"^[0-9a-f]{12}$")
            self.assertEqual(security["labels"], ["internal", "pii", "secret"])
            self.assertEqual([flow["name"] for flow in security["flows"]], ["crud:Expenses", "approval:Expense"])
            self.assertIn("no pii to emit(audit)", security["guarantees"])
            self.assertTrue(security["verified"])
            self.assertTrue(any("custom reimburse" in item for item in security["unresolved"]))
            report = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("情報フロー", report)
            self.assertIn("notify(manager)", report)
            self.assertIn("custom reimburse", report)

    def test_failed_build_marks_security_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code = _quiet(
                etsahc.cmd_build,
                argparse.Namespace(
                    file=str(PATTERNS / "examples" / "data_policy_leak.etsah"),
                    registry=str(DEFAULT_REGISTRY),
                    out=str(out),
                ),
            )
            self.assertEqual(code, 1)
            security = json.loads((out / "security.json").read_text(encoding="utf-8"))
            self.assertFalse(security["verified"])
            self.assertEqual(security["guarantees"], [])
            self.assertEqual(security["flows"][0]["sinks"], ["notify(manager)", "notify(applicant)", "emit(audit)"])

    def test_existing_manifest_policies_are_valid(self) -> None:
        registry = load_registry(DEFAULT_REGISTRY)
        for name in ("crud", "approval", "approval_gate", "notify", "ai_task", "auth"):
            self.assertIsNotNone(registry[name].data_policy, name)
            self.assertEqual(validate_manifest(registry[name]), [], name)


if __name__ == "__main__":
    unittest.main()
