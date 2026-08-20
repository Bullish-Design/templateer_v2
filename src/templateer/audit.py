"""Adversarial audit of a template's escaping, and the authoring-rule lint.

Two independent controls live here.

`audit_template` confirms after the fact what `escaping.py` enforces up front:
no string a schema permits can alter the structure of the rendered artifact.
It renders every example fixture, pokes each string field with a set of
language-shaped payloads, and compares the parsed artifacts.

`lint_template_source` reads the template source instead of the artifact. It
enforces the authoring rule the 04 refactor guide promised: every `{{ }}` site
in a structured-language template sits inside a double-quoted span, unless the
schema proves the value is a non-string scalar.

Both return findings. `audit_template` wraps them in an `AuditReport`, so a
caller can tell "audited and clean" from "audited nothing".
"""

from __future__ import annotations

import ast
import copy
import json
import re
import tomllib
from typing import Any

import yaml
from pydantic import BaseModel, Field

from templateer.template import Template

# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


class AuditFieldSkip(BaseModel):
    """One schema string field that the audit could not probe."""

    fixture: str
    field: str
    reason: str


class AuditReport(BaseModel):
    """What the audit did, not only what it found.

    An empty `findings` list is ambiguous on its own: it means "clean" and
    "nothing ran" alike. `skipped_reason` separates the two, and `ok` is only
    true when the audit actually ran.
    """

    template: str
    language: str
    fixtures_seen: int
    fields_probed: int
    fields_skipped: list[AuditFieldSkip] = Field(default_factory=list)
    sites_linted: int
    findings: list[str]
    skipped_reason: str | None = None

    @property
    def audited(self) -> bool:
        """True when the injection probe ran against at least one fixture."""
        return self.skipped_reason is None

    @property
    def ok(self) -> bool:
        """True when the audit ran and found nothing."""
        return self.audited and not self.findings


# ---------------------------------------------------------------------------
# Parsing the artifact
# ---------------------------------------------------------------------------

# Languages with a structure an injection can subvert. markdown and text have
# no string literal syntax, so neither control applies to them.
_DATA_PARSERS = {
    "toml": tomllib.loads,
    "json": json.loads,
    "yaml": yaml.safe_load,
}
STRUCTURED_LANGUAGES = frozenset(_DATA_PARSERS) | {"python"}

# Per-language payloads. Each one is a legal `str` that the target language
# re-lexes as something else when it lands outside a quoted span: a different
# type, an extra key, an extra item, or a syntax error.
#
# The old single payload was `'"\nINJECTED = "yes'`. It is TOML- and
# Python-shaped. Measured at f3b9193: it flags a vulnerable TOML, JSON and
# Python template, because it breaks those three grammars as well. It misses
# YAML entirely, where a plain scalar carries no quotes to break out of.
PAYLOADS: dict[str, tuple[str, ...]] = {
    "toml": (
        '"\nINJECTED = "yes',  # break out of a quoted string, add a key
        "true",  # bool where the schema says str
        "123",  # int where the schema says str
        "1979-05-27T07:32:00Z",  # datetime
        "[1, 2]",  # array
        "{ injected = 1 }",  # inline table
        "bare",  # bare word: a syntax error unquoted
    ),
    "json": (
        '", "INJECTED": "yes',  # break out, add a member
        "true",
        "null",
        "123",
        "[1, 2]",
        '{"injected": 1}',
        "bare",
    ),
    "yaml": (
        "true",  # plain scalar: bool
        "null",
        "~",
        "123",
        "#injected",  # a comment, so the value becomes null
        "[1, 2]",  # flow sequence
        "{injected: 1}",  # flow mapping
        "\nINJECTED: yes",  # a second key
        "- injected",  # a block sequence item
        "*injected",  # an undefined alias
    ),
    "python": (
        '"\nINJECTED = "yes',
        "True",
        "None",
        "123",
        "[1, 2]",
        "{'injected': 1}",
        "bare",
    ),
}


def _blank_strings(node: Any) -> Any:
    """Replace every string leaf with `""`, keeping everything else."""
    if isinstance(node, str):
        return ""
    if isinstance(node, dict):
        return {k: _blank_strings(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_blank_strings(v) for v in node]
    return node


def _skeleton(artifact: str, language: str) -> Any:
    """Parse *artifact* and strip the content of every string.

    Escaping may change what a string holds. It must never change the
    artifact's shape, its keys, its item counts, or the type of any value.
    The skeleton is exactly that invariant, so comparing two skeletons catches
    a new key, a new item and a changed value type alike.

    Raises:
        Exception: the artifact does not parse. The caller reports that.
    """
    if language == "python":
        tree = ast.parse(artifact)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
                node.value = ""
        return ast.dump(tree)
    return _blank_strings(_DATA_PARSERS[language](artifact))


FieldPath = tuple[str | int, ...]
FieldTarget = tuple[FieldPath, dict[str, Any]]

# The audit probes at most this many schema fields per fixture. The report
# records every field above the limit in ``fields_skipped``. This keeps a
# machine-generated schema from multiplying fields by every language payload
# without hiding the lost coverage.
MAX_FIELDS_PER_FIXTURE = 100

_MISSING = object()


def _path_label(path: FieldPath) -> str:
    """Return a stable dotted path with collection indexes in brackets."""
    out = ""
    for part in path:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + part
    return out


def _schema_is_string(schema: dict[str, Any]) -> bool:
    """Whether one concrete JSON Schema branch carries a string."""
    if schema.get("type") == "string" or isinstance(schema.get("const"), str):
        return True
    enum = schema.get("enum")
    return isinstance(enum, list) and any(isinstance(item, str) for item in enum)


def _discover_string_targets(
    schema: Any,
    data: Any,
    root: dict[str, Any],
    path: FieldPath = (),
    depth: int = 0,
) -> list[FieldTarget]:
    """Discover concrete string-bearing paths from the Pydantic JSON Schema.

    Existing collection elements keep their indexes. An empty or omitted
    collection gets index zero, which the synthesizer can construct. Optional
    and nullable object branches are traversed even when the example omits
    them.
    """
    if depth > _MAX_SCHEMA_DEPTH:
        return []

    targets: dict[str, FieldTarget] = {}
    for candidate in _candidates(schema, root, depth):
        if _schema_is_string(candidate):
            targets[_path_label(path)] = (path, candidate)
            continue

        properties = candidate.get("properties")
        if isinstance(properties, dict):
            current = data if isinstance(data, dict) else {}
            for name in sorted(properties):
                child = current.get(name, _MISSING)
                for target in _discover_string_targets(
                    properties[name], child, root, path + (name,), depth + 1
                ):
                    targets[_path_label(target[0])] = target
            continue

        items = candidate.get("items")
        if isinstance(items, dict):
            values = data if isinstance(data, list) and data else [_MISSING]
            for index, value in enumerate(values):
                for target in _discover_string_targets(
                    items, value, root, path + (index,), depth + 1
                ):
                    targets[_path_label(target[0])] = target

    return [targets[name] for name in sorted(targets)]


def _schema_values(schema: dict[str, Any]) -> list[Any]:
    """Return deterministic candidate values for one concrete schema."""
    values: list[Any] = []
    default = schema.get("default", _MISSING)
    if default is not _MISSING and default is not None:
        values.append(copy.deepcopy(default))
    const = schema.get("const", _MISSING)
    if const is not _MISSING and const is not None:
        values.append(copy.deepcopy(const))
    enum = schema.get("enum")
    if isinstance(enum, list):
        values.extend(copy.deepcopy(item) for item in enum if item is not None)
    return values


def _synthesise_value(
    schema: Any, root: dict[str, Any], depth: int = 0
) -> Any:
    """Construct one conservative value for a JSON Schema branch.

    The caller still validates the full object with Pydantic. This function
    never bypasses model validators or field constraints.
    """
    if depth > _MAX_SCHEMA_DEPTH:
        return _MISSING
    for candidate in _candidates(schema, root, depth):
        schema_values = _schema_values(candidate)
        if schema_values:
            return schema_values[0]

        kind = candidate.get("type")
        if kind == "string":
            return "templateer-audit"
        if kind == "boolean":
            return False
        if kind == "integer":
            minimum = candidate.get("minimum", 0)
            return int(minimum) if isinstance(minimum, int | float) else 0
        if kind == "number":
            minimum = candidate.get("minimum", 0.0)
            return float(minimum) if isinstance(minimum, int | float) else 0.0
        if kind == "array":
            items = candidate.get("items")
            if not isinstance(items, dict):
                continue
            count = candidate.get("minItems", 0)
            count = count if isinstance(count, int) and count > 0 else 0
            item = _synthesise_value(items, root, depth + 1)
            if item is _MISSING and count:
                continue
            return [copy.deepcopy(item) for _ in range(count)]
        if kind == "object" or isinstance(candidate.get("properties"), dict):
            properties = candidate.get("properties")
            required = candidate.get("required", [])
            if not isinstance(properties, dict) or not isinstance(required, list):
                continue
            out: dict[str, Any] = {}
            possible = True
            for name in sorted(str(item) for item in required):
                if name not in properties:
                    possible = False
                    break
                value = _synthesise_value(properties[name], root, depth + 1)
                if value is _MISSING:
                    possible = False
                    break
                out[name] = value
            if possible:
                return out
    return _MISSING


def _materialise_target(
    schema: Any,
    data: Any,
    path: FieldPath,
    value: str,
    root: dict[str, Any],
    depth: int = 0,
) -> Any:
    """Return data with *path* present and set to *value*.

    Missing parents and collection elements are built from the schema. The
    result is only a candidate. Pydantic validation decides whether it is a
    possible model.
    """
    if depth > _MAX_SCHEMA_DEPTH:
        return _MISSING
    if not path:
        return value

    head, *rest = path
    for candidate in _candidates(schema, root, depth):
        if isinstance(head, str):
            properties = candidate.get("properties")
            if not isinstance(properties, dict) or head not in properties:
                continue
            out = copy.deepcopy(data) if isinstance(data, dict) else {}
            required = candidate.get("required", [])
            if isinstance(required, list):
                possible = True
                for name in sorted(str(item) for item in required):
                    if name in out:
                        continue
                    if name not in properties:
                        possible = False
                        break
                    sibling = _synthesise_value(
                        properties[name], root, depth + 1
                    )
                    if sibling is _MISSING:
                        possible = False
                        break
                    out[name] = sibling
                if not possible:
                    continue
            current = out.get(head, _MISSING)
            child = _materialise_target(
                properties[head], current, tuple(rest), value, root, depth + 1
            )
            if child is _MISSING:
                continue
            out[head] = child
            return out

        items = candidate.get("items")
        if not isinstance(head, int) or not isinstance(items, dict):
            continue
        out = copy.deepcopy(data) if isinstance(data, list) else []
        while len(out) <= head:
            item = _synthesise_value(items, root, depth + 1)
            if item is _MISSING:
                break
            out.append(item)
        if len(out) <= head:
            continue
        child = _materialise_target(
            items, out[head], tuple(rest), value, root, depth + 1
        )
        if child is _MISSING:
            continue
        out[head] = child
        return out
    return _MISSING


def _safe_seeds(schema: dict[str, Any], current: Any) -> list[str]:
    """Return deterministic benign strings for a target baseline."""
    seeds: list[str] = []
    if isinstance(current, str):
        seeds.append(current)
    seeds.extend(item for item in _schema_values(schema) if isinstance(item, str))
    seeds.extend(("templateer-audit", "safe", "a", "0"))
    return list(dict.fromkeys(seeds))


def _value_at_path(data: Any, path: FieldPath) -> Any:
    """Return the current value at *path*, or the missing sentinel."""
    node = data
    for part in path:
        if isinstance(part, str) and isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(part, int) and isinstance(node, list) and part < len(node):
            node = node[part]
        else:
            return _MISSING
    return node


# ---------------------------------------------------------------------------
# The source lint (§A1, the rule the 04 guide promised)
# ---------------------------------------------------------------------------

# `{{ … }}`, `{% … %}` and `{# … #}` are opaque units. They do not change the
# double-quote state of the line around them.
_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}")

# A plain field path: `x`, `ruff.line_length`, `dependency.name`.
_FIELD_PATH = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")

_FOR_TAG = re.compile(r"\Afor\s+([A-Za-z_]\w*)\s+in\s+(.+?)\s*\Z")

# Types that need no quotes: they lex as themselves in all four languages.
_SCALAR_TYPES = frozenset({"integer", "number", "boolean"})

_MAX_SCHEMA_DEPTH = 12


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any] | None:
    """Follow a local JSON pointer such as `#/$defs/RuffConfig`."""
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _candidates(
    schema: Any, root: dict[str, Any], depth: int = 0
) -> list[dict[str, Any]]:
    """Flatten `$ref`, `anyOf` and `oneOf` into the concrete schemas below.

    A `null` branch is dropped: `int | None` resolves to the integer branch,
    because a `None` value never reaches the renderer — the finalizer raises.
    """
    if depth > _MAX_SCHEMA_DEPTH or not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str):
        target = _resolve_ref(ref, root)
        return [] if target is None else _candidates(target, root, depth + 1)
    branches = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(branches, list):
        out: list[dict[str, Any]] = []
        for branch in branches:
            if isinstance(branch, dict) and branch.get("type") == "null":
                continue
            out.extend(_candidates(branch, root, depth + 1))
        return out
    return [schema]


def _path_schemas(
    path: list[str], root: dict[str, Any], bindings: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """Resolve a dotted field path to the schemas it can hold.

    Returns None when the path does not resolve. The first segment may be a
    `{% for %}` loop variable.
    """
    head, *rest = path
    if head in bindings:
        current: Any = bindings[head]
    else:
        properties = root.get("properties")
        if not isinstance(properties, dict) or head not in properties:
            return None
        current = properties[head]

    schemas = _candidates(current, root)
    for segment in rest:
        found: list[dict[str, Any]] = []
        for schema in schemas:
            properties = schema.get("properties")
            if isinstance(properties, dict) and segment in properties:
                found.extend(_candidates(properties[segment], root))
        if not found:
            return None
        schemas = found
    return schemas or None


def _item_schema(
    expression: str, root: dict[str, Any], bindings: dict[str, Any]
) -> Any:
    """Return the item schema of the array *expression* names, or None."""
    if not _FIELD_PATH.match(expression):
        return None
    schemas = _path_schemas(expression.split("."), root, bindings)
    for schema in schemas or []:
        items = schema.get("items")
        if isinstance(items, dict):
            return items
    return None


def _site_verdict(
    expression: str, root: dict[str, Any], bindings: dict[str, Any]
) -> str | None:
    """Return why an unquoted site is a finding, or None when it is safe."""
    expression = expression.strip()
    if not expression:
        return "the expression is empty"
    if not _FIELD_PATH.match(expression):
        return (
            f"the expression '{expression}' is not a plain field path, so its "
            f"type cannot be proved"
        )
    schemas = _path_schemas(expression.split("."), root, bindings)
    if schemas is None:
        return f"'{expression}' does not resolve against the template schema"
    types = {schema.get("type") for schema in schemas}
    if types <= _SCALAR_TYPES and types:
        return None
    named = ", ".join(sorted(str(t) for t in types))
    return f"'{expression}' has type {named}, which needs double quotes"


def _lint_source(template: Template) -> tuple[list[str], int]:
    """Run the authoring-rule lint. Returns (findings, sites examined)."""
    language = template.metadata.output.language
    if language not in STRUCTURED_LANGUAGES:
        return [], 0

    try:
        source_path = template.resolve_path(template.metadata.renderer.file)
        source = source_path.read_text(encoding="utf-8")
    except Exception as e:
        return [f"renderer source unreadable: {e}"], 0

    try:
        root = template.get_schema_json()
    except Exception as e:
        return [f"schema unreadable, so no interpolation site can be checked: {e}"], 0

    name = template.metadata.renderer.file
    findings: list[str] = []
    sites = 0
    stack: list[tuple[str, Any]] = []

    for lineno, line in enumerate(source.splitlines(), 1):
        inside = False
        cursor = 0
        for match in _TAG.finditer(line):
            if line.count('"', cursor, match.start()) % 2:
                inside = not inside
            cursor = match.end()
            tag = match.group()
            if tag.startswith("{{"):
                sites += 1
                if inside:
                    continue
                bindings = dict(stack)
                reason = _site_verdict(tag[2:-2], root, bindings)
                if reason is not None:
                    findings.append(
                        f"{name}:{lineno}: unquoted interpolation "
                        f"{tag.strip()}: {reason}"
                    )
            elif tag.startswith("{%"):
                body = tag[2:-2].strip().lstrip("-").strip().rstrip("-").strip()
                loop = _FOR_TAG.match(body)
                if loop is not None:
                    stack.append(
                        (loop.group(1), _item_schema(loop.group(2), root, dict(stack)))
                    )
                elif body.startswith("endfor") and stack:
                    stack.pop()

    return findings, sites


def lint_template_source(template: Template) -> list[str]:
    """Flag every `{{ }}` site that breaks the authoring rule.

    The rule: in a structured-language template, every interpolation site sits
    inside a double-quoted span, unless the schema proves the value is an
    integer, a number or a boolean.

    Quote state is tracked per line, left to right. `{{ … }}` and `{% … %}`
    are opaque units, so `"{{ a }}{% if b %}x{% endif %}"` is one quoted span
    and both sites are safe.

    Returns:
        A list of findings. Empty means the template honours the rule.
    """
    return _lint_source(template)[0]


# ---------------------------------------------------------------------------
# The injection audit (§A3)
# ---------------------------------------------------------------------------


def audit_template(template: Template) -> AuditReport:
    """Probe every string field of every example fixture for injection.

    For each fixture the audit renders the benign model, then replaces one
    string field at a time with each payload for the target language. A sound
    template renders every payload into the same artifact skeleton: same keys,
    same item counts, same value types.

    Returns:
        An `AuditReport`. `report.ok` is true only when the probe ran and
        found nothing. `report.skipped_reason` says why nothing ran.
    """
    language = template.metadata.output.language
    findings, sites_linted = _lint_source(template)

    skipped: list[AuditFieldSkip] = []

    def report(
        fixtures: int, probed: int, reason: str | None = None
    ) -> AuditReport:
        return AuditReport(
            template=template.name,
            language=language,
            fixtures_seen=fixtures,
            fields_probed=probed,
            fields_skipped=sorted(
                skipped, key=lambda item: (item.fixture, item.field, item.reason)
            ),
            sites_linted=sites_linted,
            findings=findings,
            skipped_reason=reason,
        )

    if language not in STRUCTURED_LANGUAGES:
        return report(
            0, 0, f"target language '{language}' has no structure to subvert"
        )

    fixtures = sorted((template.root / "examples").glob("*.input.json"))
    if not fixtures:
        return report(0, 0, "no examples/ fixtures: no field was probed")

    try:
        schema_class = template.get_schema_class()
        schema = schema_class.model_json_schema()
    except Exception as e:
        return report(0, 0, f"schema class unloadable: {e}")

    payloads = PAYLOADS[language]
    probed = 0
    targets_seen = 0

    for fixture in fixtures:
        try:
            benign_data = json.loads(fixture.read_text(encoding="utf-8"))
        except Exception as e:
            findings.append(f"{fixture.name}: fixture is not readable JSON: {e}")
            continue
        try:
            benign_model = schema_class.model_validate(benign_data)
        except Exception as e:
            findings.append(
                f"{fixture.name}: fixture is not valid for the schema: {e}"
            )
            continue

        model_data = benign_model.model_dump(mode="json")
        targets = _discover_string_targets(schema, model_data, schema)
        targets_seen += len(targets)
        for index, (path, target_schema) in enumerate(targets):
            where = _path_label(path)
            if index >= MAX_FIELDS_PER_FIXTURE:
                skipped.append(
                    AuditFieldSkip(
                        fixture=fixture.name,
                        field=where,
                        reason=(
                            f"field limit {MAX_FIELDS_PER_FIXTURE} reached for "
                            "this fixture"
                        ),
                    )
                )
                continue

            baseline: Any = _MISSING
            baseline_errors: list[str] = []
            schema_valid_baseline = False
            current = _value_at_path(model_data, path)
            for seed in _safe_seeds(target_schema, current):
                candidate = _materialise_target(
                    schema, model_data, path, seed, schema
                )
                if candidate is _MISSING:
                    baseline_errors.append("schema branch could not be constructed")
                    continue
                try:
                    model = schema_class.model_validate(candidate)
                    schema_valid_baseline = True
                    baseline = _skeleton(template.render(model), language)
                except Exception as e:
                    baseline_errors.append(f"{type(e).__name__}: {e}")
                    continue
                break
            if baseline is _MISSING:
                detail = baseline_errors[-1] if baseline_errors else "no candidate value"
                if schema_valid_baseline:
                    findings.append(
                        f"{fixture.name}:{where}: synthesised field value does "
                        f"not render/parse: {detail}"
                    )
                skipped.append(
                    AuditFieldSkip(
                        fixture=fixture.name,
                        field=where,
                        reason=f"could not synthesise a valid field value: {detail}",
                    )
                )
                continue

            field_probed = False
            for payload in payloads:
                candidate = _materialise_target(
                    schema, model_data, path, payload, schema
                )
                if candidate is _MISSING:
                    continue
                try:
                    model = schema_class.model_validate(candidate)
                except Exception:
                    continue
                if not field_probed:
                    probed += 1
                    field_probed = True
                try:
                    rendered = template.render(model)
                except Exception as e:
                    findings.append(
                        f"{fixture.name}:{where}: render failed for {payload!r}: {e}"
                    )
                    break
                try:
                    got = _skeleton(rendered, language)
                except Exception as e:
                    findings.append(
                        f"{fixture.name}:{where}: payload {payload!r} made the "
                        f"artifact unparseable: {e}"
                    )
                    break
                if got != baseline:
                    findings.append(
                        f"{fixture.name}:{where}: payload {payload!r} changed the "
                        f"artifact structure: {baseline!r} became {got!r}"
                    )
                    break

            if not field_probed:
                skipped.append(
                    AuditFieldSkip(
                        fixture=fixture.name,
                        field=where,
                        reason="all language audit payloads violate schema constraints",
                    )
                )

    if targets_seen == 0:
        return report(len(fixtures), 0, "schema has no string-bearing fields")
    if probed == 0:
        return report(
            len(fixtures),
            0,
            "schema string fields could not be probed; inspect fields_skipped",
        )
    return report(len(fixtures), probed)
