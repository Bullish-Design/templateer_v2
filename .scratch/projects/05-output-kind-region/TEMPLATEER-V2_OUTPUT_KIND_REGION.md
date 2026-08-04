# Templateer v2 — Output `kind: "region"` (the write-back seam)

> **Kickoff + design doc for the region update.** The numbered project dir for
> the upstream side of argentic.space concept §5.4: *"One blocking mismatch to
> fix upstream: `OutputSpec.kind` is `Literal["full_file"]` — whole files only,
> which is the operation P5 bans. Add `kind: "region"`."*
>
> Cross-reference: argentic.space — `CONCEPT.md` §5 (the event-driven LLM
> pipeline), §5.3 (owned regions are bidirectional), §5.4 (this seam); the
> Phase 6 kickoff (`argentic/.scratch/projects/004-argentic-space/
> PHASE-6-PROMPT.md`), which consumes this. The argentic side is a parallel
> track; this doc is the templateer side.

## 1. The problem, restated against the actual 0.2.0 code

The concept says `OutputSpec.kind` is `Literal["full_file"]`. **The 0.2.0 code
has no `kind` on `OutputSpec` at all** — the whole-file assumption is implicit,
not declared:

- `src/templateer/models.py:57` — `OutputSpec` is `{path, language}`. No kind,
  no region metadata.
- `src/templateer/pipeline.py:61-111` — resolve → generate (validated Pydantic
  model) → render (deterministic Jinja) → validate (`validate_output`) →
  `GenerationResult` with the rendered `artifact` and an `output_path` that
  "belongs" to it.
- `src/templateer/result.py` — `GenerationResult.artifact` is the rendered
  text; **templateer does not write the file** — the caller does. The "whole
  file" contract is in the *caller's* expectation: an artifact at a path.
- `src/templateer/renderer.py` — the invariant: the template receives only
  `model.model_dump(mode="json")`, never raw LLM output.

The pipeline consumer (argentic.space §5) bans whole-file writes for its own
zone (P5: don't write what you don't own, applied to regions within a page).
It needs a template whose artifact is a **bounded payload** that gets spliced
into a page region by `replace_range` — the fence body of a `$ref`'d data
block. Today nothing in templateer *says* a template is region-bounded, so a
region template is indistinguishable from a full-file one, and the
safety property (region writes are bounded) cannot be declared or enforced.

## 2. What this update builds

1. **`OutputSpec.kind`** — a discriminated kind on the output spec:
   `Literal["full_file"]` (default, back-compatible) and `Literal["region"]`.
   A region template declares the *boundary* it may be spliced into:
   - `region.page` — the hosting page (a page-name pattern or a single name),
   - `region.ref` — the `$ref` of the data block whose payload it replaces
     (the block's `CodeText` span — the fence body — is what the consumer
     swaps; the fence and the human zone stay untouched),
   - `region.anchor` — the ref of the annotation (e.g. `$fix-tuesday`) this
     region's `addressed:` list records handling.
2. **The markdown output validator** — `kind: "command"` already exists
   (concept §5.4: "the `kind: 'command'` seam already exists; `LocalIndex` or
   `RuntimeIndex` is the natural validator"). A region template's artifact is
   **markdown payload** (a YAML block that must parse + pass the consumer's
   `replace_range` round-trip), so the validator runs the artifact through
   the argentic index (LocalIndex offline / RuntimeIndex live) and fails the
   generation when the payload is not clean.
3. **Keep the invariant**: the renderer still receives only validated Pydantic
   model data; region changes nothing there. The **write** stays with the
   consumer (argentic's `replace_range`) — templateer renders and validates,
   it does not write regions (or files).

## 3. The pinned shapes (design target — confirm against the code, then pin)

```python
# src/templateer/models.py
class RegionBoundary(BaseModel):
    model_config = {"extra": "forbid"}
    page: str                    # hosting page name (or pattern)
    ref: str                     # the data block's $ref — the payload the region owns
    anchor: str | None = None    # annotation ref recorded in addressed: (optional)

class OutputSpec(BaseModel):
    path: str                    # full_file: the target path; region: informational
    language: str
    kind: Literal["full_file", "region"] = "full_file"
    region: RegionBoundary | None = None   # required iff kind == "region" (model_validator)
```

- `kind == "region"` **requires** `region` (fail at template load, not at
  render time — the same discipline as the validator discriminated union).
- `kind == "full_file"` with a `region` is a config error.
- The pipeline (`pipeline.py`) resolves `output.language` and the validators
  unchanged; a region template's `language` is `yaml` (the payload is YAML
  for the `$ref`'d block) and its validator is the markdown/YAML check.
- `GenerationResult.output_path` for a region stays the *page* path (where
  the region lives), so failure reporting is still grounded in a real path.

## 4. The gate (falsifiable, offline-first)

1. A template with `output.kind: region` + `region:` loads and renders, and
   its artifact round-trips: `---`-fenced YAML that the consumer's
   `replace_range` can splice into a `$ref`'d block with the fence intact.
2. A `kind: region` template **without** `region:` fails template load
   (model validator), as does `kind: full_file` with a `region:`.
3. The renderer invariant holds: the region template's context is still
   `model.model_dump(mode="json")` only — no raw LLM output path.
4. The markdown output validator rejects a payload that breaks the block
   (unclosed fence, non-YAML body) and accepts a clean one.
5. Back-compat: every existing template and `GenerationResult` shape is
   untouched (`kind` defaults to `full_file`; no field removed).

## 5. Explicit non-goals

- **Writing regions** — templateer renders and validates; the bounded write
  is argentic's `replace_range` (the consumer owns the bytes).
- **The consumer loop** (digest, termination, `addressed:` bookkeeping) —
  that is argentic.space Phase 6; this repo only declares the boundary and
  validates the artifact.
- **A second renderer engine** — minijinja stays the only engine.
- Changing the Pydantic-model-only invariant in `renderer.py`.

## 6. Report back

End the session with: files touched; the final `OutputSpec` shape; the gate
results (the five checks above); the validator decision (LocalIndex vs
RuntimeIndex as the default — and why); any drift between this doc and the
code (e.g. `region.page` semantics vs patterns); the state of trunk (pushed
or not). If you stopped early, say exactly what is unfinished and why.
