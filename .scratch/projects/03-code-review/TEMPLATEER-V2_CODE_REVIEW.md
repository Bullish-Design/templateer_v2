# Templateer V2: Deep Adversarial Code Review

**Version:** 1.0
**Date:** 2026-08-04
**Scope:** Full source review of `src/templateer/` (all 12 modules), the bundled `templates/pyproject-uv` template, the test suite (13 files), packaging (`pyproject.toml`, hatch), and Allium spec alignment.
**Method:** Static reading of every source and test file, plus empirical verification of each significant claim by executing the code (missing-API-key path, context serialization, path traversal, schema/validator code execution, empty-outputs templates, wheel contents, dependency-constraint floor).
**Baseline:** 237 tests pass, 11 skipped (LLM-gated). `ruff check`, `ruff format --check`, and `ty check src/` all pass. Findings below are therefore *behavioral* and *contractual* issues, not style issues.

---

## Executive Summary

Templateer's architecture is sound and the codebase is clean, well-documented, and unusually well-tested. The central invariant — *a renderer receives only `model_dump(mode="json")` of a validated Pydantic model* — is upheld, MiniJinja strict mode genuinely sandboxes the render step, and the `Generation` state machine is a clean design.

The problems are concentrated in three areas:

1. **Failure-handling boundaries are too narrow.** The pipeline promises "on any failure, return a FAILED `Generation`" but crashes with raw tracebacks on the four most likely failure modes, including simply not having an API key.
2. **The dependency constraint is broken.** `pydantic-ai>=0.0.20` (unbounded) declares a minimum version with which the code cannot even construct an `Agent`.
3. **Template loading is arbitrary code execution, and the documentation says the wrong thing about security.** `schema.py` is executed, command validators run subprocesses, and `resolve_path` permits traversal outside the template root — none of which the README's Security section discloses.

Plus a tier of medium issues: dead code in the failure taxonomy and validation-messages feature, a compounding retry budget, CLI/API validator inconsistency, an `outputs: []` crash vector, and packaging that ships no templates.

---

## Severity Index

| ID | Severity | Finding |
|----|----------|---------|
| C1 | Critical | `run_pipeline` crashes with unhandled exceptions instead of returning FAILED `Generation` (4 sub-vectors) |
| C2 | Critical | Dependency constraint `pydantic-ai>=0.0.20` is incompatible with the code at its declared floor |
| C3 | Critical | Templates are an undocumented arbitrary-code-execution surface (schema exec, command validators, path traversal) |
| H1 | High | `MODEL_VALIDATION_FAILED` failure reason is dead code; all failures report `LLM_FAILED` |
| H2 | High | `validation_messages` is a dead feature — always empty |
| H3 | High | Retry budget compounds across retries; `can_retry` hardcodes the limit; retries lose the custom model |
| H4 | High | CLI `validate` ignores template-declared custom validators |
| M1 | Medium | `outputs: []` passes metadata validation → `IndexError` crashes downstream |
| M2 | Medium | `Template.output_kind` returns the *language*, not the kind |
| M3 | Medium | CLI `generate` context-file parsing is inconsistent |
| M4 | Medium | Wheel ships no templates; `DEFAULT_TEMPLATE_PATHS` is dead code |
| M5 | Medium | `api.generate_model` leaks raw exceptions despite documented contract |
| M6 | Medium | `render` CLI writes unvalidated output; README security claim overstates |
| M7 | Medium | `Generation`'s documented invariants are unenforced |
| L1–L10 | Low | Nits: duplicated model default, encoding, module caching, weak YAML check, etc. |

---

## C1 — `run_pipeline` crashes instead of returning a FAILED `Generation`

**Files:** `src/templateer/pipeline.py` (try/except at lines ~102-110), `src/templateer/generator.py` (lines 84-94).

**Contract violated:** `pipeline.py:33-35` documents "On any failure the Generation is returned with status FAILED and an appropriate FailureReason", mirroring the Allium `FailGeneration` rule. `run_pipeline` only catches `ModelGenerationError` around `generate_model`, and `generate_model` only wraps the `agent.run_sync()` call.

Four verified crash vectors:

### C1a — Missing API key (the most common failure mode)
`generator.py:86-92` constructs `Agent(...)` *outside* the try block. pydantic-ai 2.x raises `pydantic_ai.exceptions.UserError` at construction when `OPENAI_API_KEY` is unset.

```bash
$ templateer generate pyproject-uv --request "Make a project" -p templates
Traceback (most recent call last):
  ...
pydantic_ai.exceptions.UserError: Set the `OPENAI_API_KEY` environment variable or pass it via
`OpenAIProvider(api_key=...)` to use the OpenAI provider. ...
```

A CLI tool must fail gracefully; its most common failure mode is a raw traceback with a nonzero-but-unhandled exit.

### C1b — Non-JSON-serializable context
`_build_context` (`generator.py:128-143`) calls `json.dumps(context, indent=2)` outside any try. Verified:

```python
run_pipeline(catalog, "pyproject-uv", "x", context={"path": Path("/tmp")})
# -> UNHANDLED CRASH: TypeError Object of type PosixPath is not JSON serializable
```

`Path` objects are a perfectly plausible "project fact" for an agent to pass.

### C1c — Template load errors
`get_schema_class()` / `load_prompt()` can raise `TemplateLoadError` inside `generate_model`; `run_pipeline` does not catch it.

### C1d — `agent.retries` AttributeError
`generator.py:103` accesses `agent.retries` in the `output is None` branch. Verified: pydantic-ai 2.x `Agent` has **no** `retries` attribute (it stores `_max_tool_retries` / `_max_output_retries`; there is no property). The branch intended to produce a clean `ModelGenerationError` would instead raise `AttributeError`. The mock-based test misses this because `unittest.mock.MagicMock` auto-creates attributes.

**Why tests don't catch this:** `api.generate` wraps generic exceptions into `ModelGenerationError`, and several pipeline tests intentionally swallow `Exception` (`test_retry_no_template_succeeds_if_template_added`, `test_retry_increments_retry_count`), so the unhandled crash passes silently.

**Fix:** wrap the entire `generate_model` body (context building, `Agent` construction, `run_sync`, post-processing) in the try, or add a broad `except Exception` fallback in `run_pipeline` mapping to `FailureReason.LLM_FAILED`. Add a regression test asserting `run_pipeline` returns a FAILED `Generation` (not an exception) when no API key is set.

---

## C2 — Dependency constraint is incompatible with its declared minimum

**File:** `pyproject.toml` (`"pydantic-ai>=0.0.20"`).

The code calls `Agent(model_name, output_type=schema_class, instructions=prompt, retries=max_retries)` (`generator.py:86-89`). Verified against the declared floor:

```
pydantic-ai 0.0.20: Agent.__init__() got an unexpected keyword argument 'output_type'
```

0.0.20's signature is `Agent(model, result_type=..., system_prompt=..., ...)` — no `output_type`, no `instructions`. The currently resolved 2.23.0 happens to work, but any resolver picking a version in the range breaks, and a future 3.x could break again silently.

**Fix:** bound the constraint to the range that works (e.g. `pydantic-ai>=1,<3`), and ideally add a CI job that tests against the pinned floor. The same unbounded looseness applies to `pydantic>=2.0` and `minijinja>=2.0`, though those APIs are more stable.

---

## C3 — Templates are arbitrary code execution; security docs mislead

**Files:** `src/templateer/template.py` (`load_schema_module`, `resolve_path`), `src/templateer/validators.py` (`validate_output` command kind), `README.md` (Security Considerations).

The README states templates "have no filesystem access and no shell execution" and are "static, reviewed files." That describes only the MiniJinja render step. The **loading path** executes arbitrary code on three independent, verified vectors:

1. **`schema.py` is executed.** `load_schema_module()` (`template.py:105-124`) `exec_module`s the schema file. Verified: a schema module containing `os.system('touch /tmp/pwned_by_schema')` executes on `get_schema_class()`.

2. **Command validators run arbitrary subprocesses.** `validate_output` (`validators.py:99-124`) runs `subprocess.run(cmd, input=artifact, ...)` where `cmd` comes verbatim from `metadata.yml` `validators[].command`. Verified: `["bash", "-c", "echo PWNED > /tmp/..."]` executes. This fires on every `generate`, `api.generate`, `api.validate_artifact`, and `run_pipeline`.

3. **Path traversal with no containment check.** `resolve_path` (`template.py:87-90`) is `(self.root / relative).resolve()` with no root containment verification. Verified: `prompt.file: ../secret.txt` reads a file outside the template root; `schema.module` and `renderer.file` have the same exposure, so a malicious template can also *load* `schema.py` from outside the template root (arbitrary code from an attacker-chosen path).

**Assessment:** this is acceptable if and only if templates are explicitly documented as trusted code with full process privileges — which is a fine position for a tool whose template authors are vetted. But the README currently gives a false sense of safety, and an "agent-friendly" tool will naturally be pointed at third-party template directories.

**Recommendations:**
- Document loudly: *"A Templateer template is trusted code. Loading a template executes its `schema.py`, runs its declared validator commands, and reads files it references. Only load templates from sources you trust."*
- Add a root-containment check in `resolve_path` for `prompt.file` / `renderer.file` reads.
- Consider a runtime gate for `kind: command` validators (opt-in flag or allowlist), since they are the most surprising execution surface.

---

## H1 — `MODEL_VALIDATION_FAILED` is dead code

**File:** `src/templateer/generation.py:35`; `src/templateer/pipeline.py:104-110`.

The enum value is defined, asserted to exist in `test_pipeline.py`, and **never produced by any code path** (grep confirms). Every model-generation failure — validation retries exhausted, `None` output, fallback-dict rejection, post-generation mismatch — maps to `FailureReason.LLM_FAILED` in `run_pipeline`. Callers cannot distinguish "LLM infrastructure failed" from "LLM produced a structurally invalid model", which is the entire point of having the separate reason. Either produce `MODEL_VALIDATION_FAILED` for the validation-failure branches or delete the value.

---

## H2 — `validation_messages` is a dead feature

**Files:** `src/templateer/generator.py` (`messages` list, always `[]`), `src/templateer/pipeline.py:102` (`model, _messages = ...` discards it), `src/templateer/models.py` (`TemplateGenerationResult.validation_messages`).

The API advertises "notes or warnings from the generation process" (`api.py` docstring) that never arrive. Either populate it (e.g., surface pydantic-ai's validation retry feedback) or remove it from the public API surface.

---

## H3 — Retry budget compounds; `can_retry` hardcodes the limit; retries lose the model

**File:** `src/templateer/pipeline.py` (`retry_generation`, lines 167-188), `src/templateer/generation.py` (`can_retry`, line 122).

- `retry_generation` passes `max_retries=next_attempt` — the *accumulated* retry count — into `run_pipeline`, which forwards it as pydantic-ai's `Agent(retries=...)`. First run: 3 internal retries; first retry: 4; second retry: 5. The comment "← carry forward the retry budget" misdescribes a budget that **grows** on every retry.
- `can_retry` hardcodes `retry_count < 3` while the spec's `config.max_retries = 3` lives in `generation.allium` and `DEFAULT_MAX_RETRIES = 3` in `generator.py` — three copies of the same constant that can drift.
- `Generation` does not record `model_name`, so `retry_generation` silently retries with the default `openai:gpt-4.1-mini` even if the original generation used a custom model.

**Fix:** pass a constant retry budget per attempt (or store the budget on the `Generation`), and store `model_name` on `Generation`.

---

## H4 — CLI `validate` ignores template custom validators

**File:** `src/templateer/cli.py:422-423`.

The `validate` command calls `validate_artifact(rendered, output_language)` — language only. The API's `validate_artifact` (`api.py:263-272`) and `generate` pass `template.metadata.validators`. The CLI's headline "three checks" therefore silently skips exactly the custom validation the template author declared. (It also crashes with `IndexError` on `outputs: []` — see M1.)

---

## M1 — `outputs: []` passes validation → `IndexError` crashes

**File:** `src/templateer/models.py` (`outputs: list[OutputSpec]`, no `min_length`).

Verified: `TemplateMetadata.model_validate({"outputs": [], ...})` succeeds. Every unguarded `metadata.outputs[0]` then crashes:
- `run_pipeline` (`pipeline.py:82`) — verified `IndexError: list index out of range`
- `api.generate`, `api.validate_artifact`, `cli.validate`

Only `Template.output_kind` guards against empty (`template.py:77-80`). **Fix:** `min_length=1` on `outputs`.

---

## M2 — `Template.output_kind` returns the language, not the kind

**File:** `src/templateer/template.py:77-80`.

```python
@property
def output_kind(self) -> str:
    return self.metadata.outputs[0].language if self.metadata.outputs else "unknown"
```

The property named `output_kind` returns `"toml"` while the actual kind is `"full_file"` (verified). `catalog.templates_by_output_kind("toml")` filters by language, and the CLI prints "Output: toml" for what it labels the output *kind*. Split into `output_kind` (→ `outputs[0].kind`) and `output_language`.

---

## M3 — CLI `generate` context-file parsing is inconsistent

**File:** `src/templateer/cli.py:315-336`.

- `user_request` inside the context file is only extracted inside the `if "facts" in context_data:` branch. A file of `{"user_request": "Build a CLI tool"}` (no `facts` key) silently treats the whole dict as *project facts* and ignores the request.
- `facts: <non-dict>` silently yields empty context.
- Non-dict top-level JSON (e.g., a list) silently produces empty context.

Either structure should be handled consistently, and malformed context should error rather than silently no-op.

---

## M4 — The wheel ships no templates; `DEFAULT_TEMPLATE_PATHS` is dead code

**Files:** `pyproject.toml` (`tool.hatch.build.targets.wheel.packages = ["src/templateer"]`, sdist `include` without `templates`), `src/templateer/__init__.py` (`DEFAULT_TEMPLATE_PATHS`), `src/templateer/cli.py` (`_get_default_paths`).

Verified by building the wheel: it contains `templateer/*.py` only — **no `templates/`**. Consequences:

- `DEFAULT_TEMPLATE_PATHS[0]` (`Path(__file__).parent / "templates"`) points to a nonexistent directory in any installed package, and the constant is used **nowhere** (grep confirms; the CLI duplicates the logic).
- A pip-installed `templateer` has zero templates out of the box, contradicting the "bundled templates" story in README and `_get_default_paths`' comment.
- The sdist includes `tests` but not `templates/`; the test suite references `templates/...` relative to cwd, so tests fail from an sdist.

**Fix:** either package the templates (`force-include` in the wheel, add `templates` to the sdist include) or drop the bundled-path claim and consolidate path logic into one place.

---

## M5 — `api.generate_model` leaks raw exceptions

**File:** `src/templateer/api.py:290-314`.

Docstring promises `ModelGenerationError`; verified that without an API key it raises raw `pydantic_ai.exceptions.UserError`. Same boundary problem as C1, one layer up. Callers catching `ModelGenerationError` will be surprised.

---

## M6 — `render` CLI writes unvalidated output to disk

**File:** `src/templateer/cli.py` (`render_from_model` command), `README.md` (Security Considerations).

`templateer render ... --output file` renders and writes with **no output validation**. README's security claim — "rendered artifacts are parsed/validated … to catch injection or corruption **before they hit disk**" — only holds for the `generate`/pipeline path. Either validate in `render` or correct the claim.

---

## M7 — `Generation`'s documented invariants are unenforced

**File:** `src/templateer/generation.py:66-70`.

The docstring asserts *"If status is READY, artifact must be non-null. If status is FAILED, failure_reason must be set."* Nothing enforces this — `Generation(status=READY)` without artifact validates (and the tests construct such states). The Allium spec has matching invariants (`ArtifactImpliesReady`, `FailureImpliesReason`, `ReadyImpliesArtifact`). Either implement as `@model_validator`s or remove the claim.

---

## Low-severity nits (L1–L10)

- **L1.** `"openai:gpt-4.1-mini"` hardcoded in four places (`api.py:156`, `cli.py:294`, `generator.py:38`, `pipeline.py:51`); `DEFAULT_MODEL` in `generator.py` is used by no one else. Single-source it.
- **L2.** `read_text()` / `write_text()` without `encoding="utf-8"` — locale-dependent; template content is UTF-8 by definition of the format.
- **L3.** `load_schema_module` (`template.py:105-124`): synthetic module name prevents relative imports inside `schema.py`; same-name `Template` instances collide in `sys.modules` (cross-instance `isinstance` failures possible); `_schema_class_cache` is never invalidated (stale schema during development).
- **L4.** YAML output validation is nearly vacuous — `yaml.safe_load("hello")` passes (tests document this). Validates parsability, not structure.
- **L5.** Command validators have no artifact-size cap before piping to `subprocess.run` (only a 30s timeout) — a huge LLM-produced artifact can exhaust memory.
- **L6.** `validate_model_instance` (`validation.py:19-31`) catches only `ValidationError`; a `TypeError` from bad data types escapes as a raw exception in the fallback-dict path of `generate_model`.
- **L7.** Test hygiene: `test_generator.py::TestGenerateModelNonLLM::test_unknown_template_errors` is literally `pass`, and two pipeline tests pass by catching broad `Exception` — they would not fail if `run_pipeline` regressed into crashing (which it has, per C1).
- **L8.** `catalog.load_from_paths` silently drops duplicate template names (documented "first wins") without logging — surprising when a stale shadow template wins.
- **L9.** `trigger_paths` reads only `triggers["filenames"]`; other trigger keys are accepted by the model but silently ignored.
- **L10.** `OutputValidator` doesn't cross-validate: `kind: command` without `command`, or `kind: parse` without `language`, validates fine and silently no-ops at runtime.

---

## What is genuinely good (verified)

- **Central invariant upheld:** the renderer receives only `model_dump(mode="json")` of a validated Pydantic model; MiniJinja strict mode genuinely blocks undefined/global access (verified: `__import__`, `range`, `self` are unreachable from templates).
- **Metadata hardening:** `extra="forbid"` + `protected_namespaces=()` on `TemplateMetadata` catches metadata typos; `yaml.safe_load` everywhere (no `yaml.load`); `subprocess.run` without `shell=True`; no `eval` anywhere.
- **Design:** the `Generation` state machine, exact-name catalog lookup, and the fixture roundtrip tests are clean and valuable.
- **Determinism** is real and enforced by tests.

---

## Recommended remediation order

1. **C1** — broaden the exception boundary in `generate_model`/`run_pipeline`; add a regression test that runs `generate` without an API key and asserts a FAILED `Generation`.
2. **C2** — bound `pydantic-ai` to a working range; add a floor-version CI test.
3. **C3** — document the trusted-code model; add `resolve_path` containment; gate command validators.
4. **H1/H2** — wire up `MODEL_VALIDATION_FAILED` and `validation_messages`, or delete them from the surface.
5. **H3/H4/M1–M5** — retry budget, CLI/API validator consistency, `outputs` `min_length`, and packaging.

---

## Appendix: Reproduction commands (verified on this checkout)

```bash
# C1a: unhandled traceback without API key
unset OPENAI_API_KEY
uv run templateer generate pyproject-uv --request "Make a project" -p templates

# C1b: unhandled TypeError from non-serializable context
python - <<'EOF'
from pathlib import Path
from templateer.catalog import TemplateCatalog
from templateer.pipeline import run_pipeline
c = TemplateCatalog(); c.load_from_paths([Path("templates")])
run_pipeline(c, "pyproject-uv", "x", context={"path": Path("/tmp")})
EOF

# C2: declared minimum dependency is incompatible
pip install pydantic-ai==0.0.20
python -c "from pydantic_ai import Agent; Agent('openai:gpt-4.1-mini', output_type=dict, instructions='hi')"
# -> TypeError: Agent.__init__() got an unexpected keyword argument 'output_type'

# C3a: schema.py executes arbitrary code
# (create a template whose schema.py calls os.system, then get_schema_class())

# C3b: command validators execute arbitrary commands
# (metadata.yml validators: [{kind: command, command: ["bash", "-c", "..."]}])

# C3c: path traversal via prompt.file / renderer.file
# (prompt: {file: ../secret.txt} reads a file outside the template root)

# M1: empty outputs crashes the pipeline
# (template with outputs: [] -> run_pipeline raises IndexError)

# M4: wheel ships no templates
pip wheel . --no-deps
python -c "import zipfile,glob; print([n for n in zipfile.ZipFile(glob.glob('*.whl')[0]).namelist() if 'templates' in n])"
# -> []
```
