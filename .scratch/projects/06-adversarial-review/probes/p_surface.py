"""A8 + A9 + B6 + B8 + C2 -- verify retry, async, and API safeguards.

The original probe showed identical retries, lost warnings, blocked async
callers, inconsistent validation errors, an uncapped retry count, and no
top-level exports. Round 2 reverses each result.

Run from the repo root:
  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_surface.py
"""

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "src")

import templateer.pipeline as pipeline
from templateer.api import TemplateRegistry
from templateer.catalog import TemplateCatalog
from templateer.result import GenerationRequest

print("=== A9 + B6: the retry loop re-rolls identical input and drops warnings ===")
seen = []


async def fake_generate_model(
    template, user_request, context, model_name, prior_failure=None
):
    seen.append((user_request, dict(context), prior_failure))
    return template.get_schema_class()(
        project_name="x", python_version="3.12"
    ), {"input_tokens": 1, "output_tokens": 1}


pipeline.generate_model_async = fake_generate_model
pipeline.validate_output = lambda artifact, language, validators: (
    ["toml parse failed: forced"],
    ["an optional validator note"],
)

catalog = TemplateCatalog()
catalog.load_from_paths([Path("templates")])
result = pipeline.generate(
    catalog,
    GenerationRequest(template_name="pyproject-uv", user_request="build it", max_attempts=3),
)
print("  attempts made          :", len(seen))
print("  all inputs identical   :", len({str(s) for s in seen}) == 1)
print("  failure detail fed back:", bool(seen[1][2]) if len(seen) > 1 else False)
print("  warnings on failure    :", result.warnings)

print()
print("=== A8: TemplateRegistry.generate_async inside a running event loop ===")


async def main():
    registry = TemplateRegistry.from_paths(["templates"])
    result = await registry.generate_async(
        "pyproject-uv", "build it", max_attempts=1
    )
    print("  returned GenerationResult:", type(result).__name__)


asyncio.run(main())

print()
print("=== B8: api.render_from_model vs the CLI, same mistake, two error types ===")
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
print("=== A9: max_attempts is capped ===")
try:
    GenerationRequest(template_name="x", user_request="y", max_attempts=100000)
except Exception as error:
    print("  max_attempts=100000 ->", type(error).__name__)

print()
print("=== C2: top-level package exports (fresh process -- no submodules imported) ===")
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
