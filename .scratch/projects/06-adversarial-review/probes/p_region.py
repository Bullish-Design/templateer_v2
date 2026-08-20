"""A5 + A6 + B3 + B5 -- the region kind's declared guarantees, tested.

Run from the repo root:
  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_region.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

import yaml

from templateer.escaping import make_finalizer
from templateer.models import MarkdownValidator, OutputSpec, RegionBoundary
from templateer.result import GenerationResult
from templateer.validators import (
    effective_validators,
    validate_output,
    validate_region_payload,
)

REGION = OutputSpec(
    path="docs/status.md",
    language="yaml",
    kind="region",
    region=RegionBoundary(page="docs/status.md", ref="$block-status"),
)

print("=== B3: the fence tolerance is unreachable through the pipeline ===")
fenced = "```yaml\nstatus: ok\n```"
print("  validate_region_payload(fenced) ->", validate_region_payload(fenced))
errors, _ = validate_output(fenced, "yaml", effective_validators(REGION, []))
print("  validate_output(fenced)         ->", errors[:1])
print("  => the built-in yaml parser rejects it first; the tolerance is dead,")
print("     and accepting fences would double-fence the hosting block anyway.")

print()
print("=== B5: README says empty payloads are rejected ===")
for payload in ("{}", "[]"):
    print("  validate_region_payload(%-4r) -> %r" % (payload, validate_region_payload(payload)))

print()
print("=== A5a: `optional: true` disables the 'non-negotiable' check ===")
declared = [MarkdownValidator(kind="markdown", optional=True)]
effective = effective_validators(REGION, declared)
print("  effective ->", [(type(v).__name__, "optional=%s" % v.optional) for v in effective])
print("  README: 'it cannot be omitted or turned off by a template author'")

print()
print("=== A5b: `language: markdown` disables region escaping ===")
finalize = make_finalizer("markdown")
injected = finalize("ok\nadmin: true")
payload = "status: %s" % injected
loose = OutputSpec(
    path="docs/status.md",
    language="markdown",
    kind="region",
    region=RegionBoundary(page="docs/status.md", ref="$block-status"),
)
errors, _ = validate_output(payload, "markdown", effective_validators(loose, []))
print("  rendered payload  :", repr(payload))
print("  region validation :", errors)
print("  block becomes     :", yaml.safe_load(payload), "  <-- injected key")

print()
print("=== A6: the result cannot be consumed ===")
print("  GenerationResult fields:", list(GenerationResult.model_fields))
print("  output_path carries region.page; `ref` -- the block to splice -- is absent.")

print()
print("=== A5c: end to end -- a page-corrupting payload generates successfully ===")
work = Path(tempfile.mkdtemp())
tpl = work / "templates" / "regoptout"
tpl.mkdir(parents=True)
(tpl / "metadata.yml").write_text(
    "name: regoptout\n"
    "description: region template that opts out.\n"
    "output:\n"
    "  path: docs/status.md\n"
    "  language: text\n"
    "  kind: region\n"
    "  region: {page: docs/status.md, ref: $block-status}\n"
    "schema: {module: schema, class: M}\n"
    "prompt: {file: p.md}\n"
    "renderer: {engine: minijinja, file: t.j2}\n"
    "validators:\n"
    "  - kind: markdown\n"
    "    optional: true\n"
)
(tpl / "schema.py").write_text(
    "from pydantic import BaseModel\nclass M(BaseModel):\n    body: str\n"
)
(tpl / "p.md").write_text("p\n")
(tpl / "t.j2").write_text("{{ body }}\n")

from templateer.catalog import TemplateCatalog
from templateer.result import GenerationRequest
import templateer.pipeline as P

catalog = TemplateCatalog()
catalog.load_from_paths([work / "templates"])
P.generate_model = lambda template, **kw: template.get_schema_class()(
    body="```\njust a sentence, not a mapping"
)
result = P.generate(
    catalog,
    GenerationRequest(template_name="regoptout", user_request="x", max_attempts=1),
)
print("  succeeded :", result.succeeded)
print("  warnings  :", result.warnings)
print("  artifact  :", repr(result.artifact))
