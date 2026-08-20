# AGENTS.md — Templateer project instructions

Read [`.agents/skills/my-ai/SKILL.md`](.agents/skills/my-ai/SKILL.md) first. It
defines the standing environment, verification, version-control, and writing
rules. This file defines the Templateer-specific rules.

## What this project is

Templateer is a Python library and command-line interface for constrained
artifact generation. It asks a language model for a Pydantic model. It then
renders that model with MiniJinja and validates the artifact.

Agent-framework authors use the Python API. Template authors use the metadata,
schema, prompt, renderer, audit, and test workflow.

The Python package does not ship a template catalog. Callers provide `./templates`
or pass template paths with `-p` or `TemplateRegistry.from_paths()`.

## Working here

Enter the pinned environment before you run a project command:

```bash
devenv shell
```

The current shell does not provide `testee` or `gitman`. Use direct verification
and Git commands when those managers are unavailable. Report the fallback.

Run the pull-request gate:

```bash
pytest -q
ruff check src/ tests/ templates/
ty check src/
```

All three commands must pass before a commit. Run focused tests before the full
gate when you change one behavior.

Use Pydantic AI test models in automated tests. Do not require a provider key
for the default suite.

## Project rules

- Keep the synchronous and asynchronous APIs aligned.
- Return generation failures as `GenerationResult` values at the pipeline boundary.
- Keep template paths inside the template root.
- Treat output languages as a closed set in `models.py`.
- Quote string interpolation sites in structured-language renderers.
- Run the authoring audit after a renderer or schema change.
- Add a negative test for each security or containment control.
- Preserve command exit codes: `0` success, `1` finding, `2` configuration, and `3` usage.
- Keep `--json` output machine-readable and free of prose.

## Where things live

- `src/templateer/` contains the library, pipeline, Python API, and command-line interface.
- `tests/` contains library and command-line tests.
- `templates/pyproject-uv/` is a development example. The wheel excludes it.
- `.scratch/specs/allium/` contains the behavioral specifications.
- `.scratch/projects/` contains bounded investigations and their evidence.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the module map and template-authoring
workflow. See [`README.md`](README.md) for the public API and command examples.

## Template checks

For a template under `./templates`, run:

```bash
templateer check <name> -p ./templates
templateer validate <name> -p ./templates --input <input.json>
pytest templates/<name>/tests/ -q
```

`templateer check` must report real fixture coverage. Treat a "nothing audited"
result as a failed verification.

## Managed agent files

`AGENTS.md` is the repository source of truth. `CLAUDE.md` is a symlink to this
file. Do not replace the symlink with a second copy.

The personal layer owns `.agents/skills/my-ai/SKILL.md`. Change that skill in
its source repository, then materialize the update here.
