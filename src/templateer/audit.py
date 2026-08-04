"""Adversarial audit of a template's escaping.

Confirms after the fact what escaping.py enforces up front: that no string a
schema permits can alter the structure of the rendered artifact.
"""

import ast
import copy
import json
import tomllib
from collections.abc import Iterator
from typing import Any

import yaml

from templateer.template import Template

PAYLOAD = '"\nINJECTED = "yes'

_PARSERS = {
    "toml": tomllib.loads,
    "json": json.loads,
    "yaml": yaml.safe_load,
    "python": ast.parse,
}


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


def _key_paths(node: Any, prefix: tuple = ()) -> set[tuple]:
    out: set[tuple] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            out |= {prefix + (k,)} | _key_paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out |= _key_paths(v, prefix + (i,))
    return out


def audit_template(template: Template) -> list[str]:
    """Probe every string field of every example fixture for injection.

    Returns a list of findings; empty means the template is sound.
    """
    language = template.metadata.output.language
    parse = _PARSERS.get(language)
    if parse is None:
        return []  # unstructured target: no structure to subvert

    schema_class = template.get_schema_class()
    findings: list[str] = []

    for fixture in sorted((template.root / "examples").glob("*.input.json")):
        benign_data = json.loads(fixture.read_text(encoding="utf-8"))
        try:
            baseline = _key_paths(parse(template.render(
                schema_class.model_validate(benign_data))))
        except Exception as e:
            findings.append(f"{fixture.name}: benign fixture does not render/parse: {e}")
            continue

        for path in _string_paths(benign_data):
            where = ".".join(str(p) for p in path)
            try:
                model = schema_class.model_validate(_poke(benign_data, path, PAYLOAD))
            except Exception:
                continue  # constrained field — not injectable by construction
            try:
                rendered = template.render(model)
            except Exception as e:
                findings.append(f"{fixture.name}:{where}: render failed: {e}")
                continue
            try:
                got = _key_paths(parse(rendered))
            except Exception as e:
                findings.append(f"{fixture.name}:{where}: artifact unparseable: {e}")
                continue
            if injected := got - baseline:
                findings.append(
                    f"{fixture.name}:{where}: injected "
                    + ", ".join(".".join(map(str, p)) for p in sorted(injected))
                )

    return findings
