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
from collections.abc import Iterator
from typing import Any

import yaml
from pydantic import BaseModel

from templateer.template import Template

# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


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


def _string_paths(node: Any, prefix: tuple = ()) -> Iterator[tuple]:
    if isinstance(node, str):
        yield prefix
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _string_paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _string_paths(v, prefix + (i,))


def _poke(data: Any, path: tuple, value: Any) -> Any:
    out = copy.deepcopy(data)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


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

    def report(
        fixtures: int, probed: int, reason: str | None = None
    ) -> AuditReport:
        return AuditReport(
            template=template.name,
            language=language,
            fixtures_seen=fixtures,
            fields_probed=probed,
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
    except Exception as e:
        return report(0, 0, f"schema class unloadable: {e}")

    payloads = PAYLOADS[language]
    probed = 0

    for fixture in fixtures:
        try:
            benign_data = json.loads(fixture.read_text(encoding="utf-8"))
        except Exception as e:
            findings.append(f"{fixture.name}: fixture is not readable JSON: {e}")
            continue
        try:
            baseline = _skeleton(
                template.render(schema_class.model_validate(benign_data)), language
            )
        except Exception as e:
            findings.append(
                f"{fixture.name}: benign fixture does not render/parse: {e}"
            )
            continue

        for path in _string_paths(benign_data):
            where = ".".join(str(p) for p in path)
            counted = False
            for payload in payloads:
                try:
                    model = schema_class.model_validate(_poke(benign_data, path, payload))
                except Exception:
                    continue  # constrained field — not injectable by construction
                if not counted:
                    probed += 1
                    counted = True
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

    return report(len(fixtures), probed)
