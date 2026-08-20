"""A4 + B2 -- two containment findings in one fixture.

  A4: pipeline.generate() promises "Nothing escapes as an exception".
      A TemplateLoadError from Template.render() -> resolve_path does.
  B2: `schema.module` bypasses resolve_path, so the *executed* file is the
      one place the template-root rule is not applied.

NOW GUARDS (round-2 remediation):
  B2 -> tests/test_surface.py::test_schema_module_outside_the_template_root_fails_to_load
  A4 -> tests/test_surface.py::test_pipeline_returns_a_result_for_a_renderer_outside_the_root

Each finding gets its own template.  The original probe shared one, and once
B2 was fixed the TemplateLoadError it now correctly raises killed the script
before the A4 section ran.

Run from the repo root:
  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_pipeline_escape.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

work = Path(tempfile.mkdtemp())

SCHEMA = (
    'MARKER = "schema module executed from OUTSIDE the template root"\n'
    "from pydantic import BaseModel\n"
    "class M(BaseModel):\n"
    "    x: str\n"
)

# --- B2 fixture: schema.module points outside the root ---------------------
b2 = work / "templates" / "escape"
b2.mkdir(parents=True)
(work / "outside_schema.py").write_text(SCHEMA)
(work / "outside.j2").write_text('x = "{{ x }}"\n')
(b2 / "prompt.md").write_text("p\n")
(b2 / "template.j2").write_text('x = "{{ x }}"\n')
(b2 / "metadata.yml").write_text(
    "name: escape\n"
    "description: probe path containment.\n"
    "output: {path: out.txt, language: text}\n"
    "schema: {module: ../../outside_schema, class: M}\n"
    "prompt: {file: prompt.md}\n"
    "renderer: {engine: minijinja, file: template.j2}\n"
)

# --- A4 fixture: schema is contained, the *renderer* points outside --------
a4 = work / "templates" / "escape-renderer"
a4.mkdir(parents=True)
(a4 / "schema.py").write_text(SCHEMA)
(a4 / "prompt.md").write_text("p\n")
(a4 / "metadata.yml").write_text(
    "name: escape-renderer\n"
    "description: probe renderer containment.\n"
    "output: {path: out.txt, language: text}\n"
    "schema: {module: schema, class: M}\n"
    "prompt: {file: prompt.md}\n"
    "renderer: {engine: minijinja, file: ../../outside.j2}\n"
)

import templateer.pipeline as pipeline  # noqa: E402
from templateer.catalog import TemplateCatalog  # noqa: E402
from templateer.result import GenerationRequest  # noqa: E402
from templateer.template import TemplateLoadError  # noqa: E402

catalog = TemplateCatalog()
catalog.load_from_paths([work / "templates"])

print("=== B2: schema.module escapes the template root and is executed ===")
try:
    print("  ->", catalog.get("escape").load_schema_module().MARKER)
except TemplateLoadError as e:
    print("  -> refused at load:", e)

print()
print("=== A4: does a TemplateLoadError escape pipeline.generate()? ===")


def _stub(template, **kw):
    return template.get_schema_class()(x="hi"), None


async def _stub_async(template, **kw):
    return template.get_schema_class()(x="hi"), None


# The generator entry point is sync before Wave 3a and async after it.
if hasattr(pipeline, "generate_model_async"):
    pipeline.generate_model_async = _stub_async
else:
    pipeline.generate_model = lambda template, **kw: template.get_schema_class()(x="hi")

try:
    result = pipeline.generate(
        catalog,
        GenerationRequest(
            template_name="escape-renderer", user_request="x", max_attempts=1
        ),
    )
    print("  returned a result:", result.failure_reason)
except Exception as e:
    print("  !!! EXCEPTION ESCAPED generate():", type(e).__name__, e)
    print("  pipeline.py:8 -- 'that promise is either total or worthless'")
