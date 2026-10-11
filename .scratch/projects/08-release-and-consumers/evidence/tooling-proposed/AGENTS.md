# AGENTS.md — Templateer project instructions

This file defines the Templateer-specific rules.

## What this project is

Templateer is a Python library and command-line interface for constrained
artifact generation. It asks a language model for a Pydantic model. It then
renders that model with MiniJinja and validates the artifact.

Agent-framework authors use the Python API. Template authors use the metadata,
schema, prompt, renderer, audit, and test workflow.

The Python package does not ship a template catalog. Callers provide `./templates`
or pass template paths with `-p` or `TemplateRegistry.from_paths()`.

## Working here

Verify through Testee. Run it from the repository root. Do not wrap it in
`devenv shell`:

```bash
testee verify
testee status -r REV
```

`testee verify` runs `ruff check src/ tests/ templates/`, `ty check src/`, and
`pytest -q` in a clean workspace. Run `testee status -r REV` on the exact
revision before you integrate. A push through the devman hook needs exit 0.
The checks live in `devenv.nix` as `testee.checks`. The host profile provides
`testee` (0.6.1 or newer) and `gitman`. Check `testee --version` first: a
devenv shell can put an older `testee` ahead of the host one on `PATH`.

For a quick edit loop, run the tools through `uv run` in the pinned environment.
`uv run` uses the venv of the current checkout, even when another checkout's
`bin` directory is on `PATH`:

```bash
devenv shell -- uv run --extra dev pytest -q
devenv shell -- uv run --extra dev ruff check src/ tests/ templates/
devenv shell -- uv run --extra dev ty check src/
```

All three must pass before a commit. Run focused tests before the full gate
when you change one behavior.

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

The `.agents/` and `.claude/` links are machine-local and maintained by the
central Devman link plane.
