"""SS A1 -- unquoted interpolation re-lexes a `str` as another type.

Run from the repo root:  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_escaping_holes.py
"""

import sys
import tomllib

sys.path.insert(0, "src")

import yaml
from minijinja import Environment

from templateer.escaping import make_finalizer


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
print("=== B4: container interpolation bypasses the finalizer entirely ===")
out = render("deps = {{ deps }}", "toml", deps=['a"\nINJECTED = "yes'])
print("  list ->", repr(out))
print("  note: single-quoted = a TOML *literal* string; escapes do not apply")
out = render("x = {{ d }}", "toml", d={"k": "v"})
print("  dict ->", repr(out), "(valid Python, invalid TOML)")

print()
print("=== B4b: single-quoted TOML literal string breaks out ===")
out = render("x = ['{{ v }}']", "toml", v="a', 'b")
print("  ->", repr(out), "parsed:", tomllib.loads(out), "  <-- 1 value became 2")
