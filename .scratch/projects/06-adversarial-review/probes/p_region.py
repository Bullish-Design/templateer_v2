"""A5 + A6 + B3 + B5 -- verify the region kind's enforced guarantees.

The original probe showed that authors could disable the region check, select
an identity-escaped language, and return a result without splice metadata.
Round 2 rejects each case or returns the required metadata.

Run from the repo root:
  .venv/bin/python .scratch/projects/06-adversarial-review/probes/p_region.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

from pydantic import ValidationError

import templateer.pipeline as pipeline
from templateer.catalog import TemplateCatalog
from templateer.models import MarkdownValidator, RegionBoundary, RegionOutput
from templateer.result import GenerationRequest, GenerationResult
from templateer.validators import (
    effective_validators,
    validate_output,
    validate_region_payload,
)

REGION = RegionOutput(
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
print("=== A5a: `optional: true` cannot disable the built-in check ===")
declared = [MarkdownValidator(kind="markdown", optional=True)]
effective = effective_validators(REGION, declared)
print("  effective ->", [(type(v).__name__, "optional=%s" % v.optional) for v in effective])
print("  first validator is required:", effective[0].optional is False)

print()
print("=== A5b: a region cannot select identity escaping ===")
try:
    RegionOutput(
        path="docs/status.md",
        language="markdown",
        kind="region",
        region=RegionBoundary(page="docs/status.md", ref="$block-status"),
    )
except ValidationError as error:
    print("  language: markdown ->", type(error).__name__)

print()
print("=== A6: the result carries splice metadata ===")
print("  GenerationResult fields:", list(GenerationResult.model_fields))
print("  kind and region present:", {"kind", "region"} <= set(GenerationResult.model_fields))

print()
print("=== A5c: end to end -- a page-corrupting payload fails ===")
work = Path(tempfile.mkdtemp())
tpl = work / "templates" / "regoptout"
tpl.mkdir(parents=True)
(tpl / "metadata.yml").write_text(
    "name: regoptout\n"
    "description: region template that opts out.\n"
    "output:\n"
    "  path: docs/status.md\n"
    "  language: yaml\n"
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

catalog = TemplateCatalog()
catalog.load_from_paths([work / "templates"])
async def fake_generate_model(template, **kw):
    return template.get_schema_class()(
        body="```\njust a sentence, not a mapping"
    ), None


pipeline.generate_model_async = fake_generate_model
result = pipeline.generate(
    catalog,
    GenerationRequest(template_name="regoptout", user_request="x", max_attempts=1),
)
print("  succeeded :", result.succeeded)
print("  warnings  :", result.warnings)
print("  failure   :", result.failure_reason)
print("  kind      :", result.kind)
print("  region    :", result.region)
