"""A1 + B4 -- guard unquoted interpolation and container interpolation.

The scalar examples preserve the original reproduction. The container examples
now show the round-2 ``EscapeError`` guard.

Run from the repo root:
  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_escaping_holes.py
"""

import sys
import tomllib

sys.path.insert(0, "src")

import yaml
from minijinja import Environment

from templateer.escaping import EscapeError, make_finalizer
from templateer.validators import check_round_trip


def render(src, lang, **ctx):
    env = Environment()
    env.trim_blocks = True
    env.lstrip_blocks = True
    env.undefined_behavior = "strict"
    env.finalizer = make_finalizer(lang)
    return env.render_str(src, **ctx)


print("=== A1a: TOML `name = {{ v }}` -- a str field lands as bool / int ===")
for v in ["true", "123", "foo"]:
    out = render("name = {{ v }}", "toml", v=v)
    try:
        parsed = tomllib.loads(out)
        print("  v=%-8r -> %-18r parsed=%r" % (v, out, parsed))
    except Exception as e:
        print("  v=%-8r -> %-18r PARSE ERROR: %s" % (v, out, e))

print()
print("=== A1b: YAML `key: {{ v }}` -- a str field lands as null / bool ===")
for v in ["#redacted", "true", "value"]:
    out = render("key: {{ v }}\nother: 1", "yaml", v=v)
    print("  v=%-12r -> %-30r parsed=%r" % (v, out, yaml.safe_load(out)))

print()
print("=== B4: container interpolation is rejected by the finalizer ===")
for name, value in (
    ("list", ['a"\nINJECTED = "yes']),
    ("dict", {"k": "v"}),
):
    try:
        out = render("value = {{ value }}", "toml", value=value)
        print(" ", name, "-> UNEXPECTED:", repr(out))
    except EscapeError as error:
        print(" ", name, "->", type(error).__name__ + ":", error)

print()
print("=== A1c: the runtime check catches a string that becomes a list ===")
out = render("value = ['{{ value }}']", "toml", value="a', 'b")
print("  artifact ->", repr(out), "parsed:", tomllib.loads(out))
print("  findings ->", check_round_trip(out, "toml", {"value": "a', 'b"}))
