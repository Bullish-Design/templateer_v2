"""The core guarantee: a validated model cannot alter artifact structure."""
import ast
import json
import tomllib
from pathlib import Path

import pytest
import yaml

from templateer.template import Template

PAYLOADS = [
    'benign"\nlicense = "PROPRIETARY',   # the A1 attack
    'He said "hi"', 'back\\slash', 'tab\there', 'null\x00byte',
    'unié中\U0001F600',                   # astral: breaks ensure_ascii=True
    'del\x7fhere',                        # DEL: must be escaped for TOML
    "', evil: true, x: '", 'a\r\nb', '"""', "'''", '\x1b[31m',
    '{{ injected }}', '{% raw %}', '$(whoami)', '#comment',
]

@pytest.mark.parametrize("payload", PAYLOADS)
def test_escaped_string_round_trips_in_every_language(payload):
    from templateer.escaping import escape_string
    e = escape_string(payload)
    assert tomllib.loads(f'k = "{e}"')["k"] == payload
    assert json.loads(f'{{"k": "{e}"}}')["k"] == payload
    assert yaml.safe_load(f'k: "{e}"')["k"] == payload
    assert ast.literal_eval(f'"{e}"') == payload

def test_injection_payload_cannot_add_a_toml_key():
    t = Template(Path("templates/pyproject-uv"))
    cls = t.get_schema_class()
    m = cls(project_name="ok", python_version="3.12",
            project_description='benign"\nlicense = "PROPRIETARY')
    parsed = tomllib.loads(t.render(m))
    assert "license" not in parsed["project"]
    assert parsed["project"]["description"] == 'benign"\nlicense = "PROPRIETARY'

def test_bools_render_as_target_language_literals(tmp_path):
    """MiniJinja renders Python True as 'True', which is invalid TOML."""
    from pydantic import BaseModel

    from templateer.renderer import render_template
    class M(BaseModel):
        flag: bool = True
    f = tmp_path / "t.j2"
    f.write_text("flag = {{ flag }}\n")
    assert tomllib.loads(render_template(f, M(), "toml"))["flag"] is True

def test_interpolating_null_is_a_template_authoring_error(tmp_path):
    from pydantic import BaseModel

    from templateer.renderer import RenderError, render_template
    class M(BaseModel):
        x: str | None = None
    f = tmp_path / "t.j2"
    f.write_text('x = "{{ x }}"\n')
    with pytest.raises(RenderError, match="null"):
        render_template(f, M(), "toml")
