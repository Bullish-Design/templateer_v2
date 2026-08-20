"""A8 + A9 + B6 + B8 -- retry semantics, async, and API surface.

Run from the repo root:
  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_surface.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "src")

from templateer.catalog import TemplateCatalog
from templateer.result import GenerationRequest
import templateer.pipeline as P

print("=== A9 + B6: the retry loop re-rolls identical input and drops warnings ===")
seen = []


def fake_generate_model(template, user_request, context, model_name):
    seen.append((user_request, dict(context)))
    return template.get_schema_class()(project_name="x", python_version="3.12")


P.generate_model = fake_generate_model
P.validate_output = lambda artifact, language, validators: (
    ["toml parse failed: forced"],
    ["an optional validator note"],
)

catalog = TemplateCatalog()
catalog.load_from_paths([Path("templates")])
result = P.generate(
    catalog,
    GenerationRequest(template_name="pyproject-uv", user_request="build it", max_attempts=3),
)
print("  attempts made          :", len(seen))
print("  all inputs identical   :", len({str(s) for s in seen}) == 1)
print("  failure detail fed back: no -- error_detail never reaches the next prompt")
print("  warnings on failure    :", result.warnings, "<-- dropped")

print()
print("=== A8: run_sync inside a running event loop ===")


async def main():
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class M(BaseModel):
        x: str

    try:
        Agent("test", output_type=M).run_sync("hi")
        print("  ok")
    except Exception as e:
        print("  ->", type(e).__name__ + ":", e)
        print("  TemplateRegistry.generate() is unreachable from async callers.")


asyncio.run(main())

print()
print("=== B8: api.render_from_model vs the CLI, same mistake, two error types ===")
from templateer.api import TemplateRegistry

registry = TemplateRegistry.from_paths(["templates"])
try:
    registry.render_from_model("pyproject-uv", ["not", "a", "dict"])
except Exception as e:
    print("  api ->", type(e).__name__ + ":", str(e)[:70])
try:
    registry.get_template("pyproject-uv").get_schema_class().model_validate(["not", "a", "dict"])
except Exception as e:
    print("  cli ->", type(e).__name__ + ":", str(e).splitlines()[0])

print()
print("=== A9: max_attempts has no ceiling ===")
print(
    "  GenerationRequest(max_attempts=100000) ->",
    GenerationRequest(template_name="x", user_request="y", max_attempts=100000).max_attempts,
)

print()
print("=== C2: top-level package exports (fresh process -- no submodules imported) ===")
import subprocess

print(
    "  dir(templateer) ->",
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0,'src'); import templateer; "
            "print([n for n in dir(templateer) if not n.startswith('_')])",
        ],
        capture_output=True,
        text=True,
    ).stdout.strip(),
)
