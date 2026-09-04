# Project 07 — slim and lazy `pydantic_ai`

Date: 2026-09-04

## Scope and acceptance criteria

This investigation targets the render-only import path. It must not import
`pydantic_ai` until generation starts. The dependency must use
`pydantic-ai-slim[openai]`, because the default model is OpenAI-based.

The acceptance criteria are:

- `import templateer` stays near the measured 143 ms target.
- `pydantic_ai` is absent from `sys.modules` after a clean package import.
- The first generation can load the agent stack without blocking the event loop.
- Later generations reuse the loaded agent class.
- Existing Templateer behavior remains green.
- The consumer suite remains green without changes to the consumer repository.

## Baseline evidence

The baseline used commit `736f573a7d9dae0c289b2f1e230e1def37548880` in the
pinned `devenv` shell. The shell used Python 3.13.14. The original dependency
set contained `pydantic-ai` 2.23.0, `mcp` 1.29.0, `pydantic-evals` 2.23.0, and
`logfire` 4.39.0. The import-time trace also showed the `fastmcp` module chain.

The direct baseline command measured `import templateer` at 1719 ms. The
import-time trace identified these dominant paths:

- `pydantic_ai`: 818 ms cumulative.
- `pydantic_ai.agent`: 809 ms cumulative.
- `pydantic_ai.capabilities._deferred_capability_loader`: 704 ms cumulative.
- `pydantic_ai.mcp`: 514 ms cumulative.
- `pydantic_ai.mcp` through `mcp` and `fastmcp`: about 514 ms cumulative in
  this run.
- `templateer.audit`: 336 ms cumulative.

The kickoff safety test inserted a finder that blocked `fastmcp`, `mcp`, and
`mcp_types`. Import still succeeded at 681 ms. This confirms that the slim
dependency shape is safe for this code path.

The local source inspection confirmed:

- `generator.py` imported `Agent` only for `generate_model_async`.
- `generator.py` stored the default model string that `api.py`, `result.py`,
  and `cli.py` imported.
- `pipeline.py` used `UnexpectedModelBehavior` and `UserError` only in the
  model-generation exception block.
- `api.py` imported `audit` and `pipeline` before any caller requested them.
- `__init__.py` queried package metadata during every package import.

## Implemented change

The default model now lives in `templateer.constants`. `generator` re-exports
the imported name, so existing imports remain valid. The API, result model, and
CLI read the constant without importing the generator module.

`generator.Agent` remains a module attribute initialized to `None`. The
`_load_agent` helper assigns the real class only when the attribute is still
`None`. `generate_model_async` calls it through `asyncio.to_thread`, which
keeps the event loop available while Python imports the generation stack.
The public `templateer.preload()` function provides an explicit eager option.

The pipeline loads its two exception classes through `asyncio.to_thread` just
before the exception block. The API loads `pipeline` and `audit` inside the
methods that use them. The CLI generation command calls `preload()` at startup;
the audit command imports its audit function locally. Package version metadata
loads through module `__getattr__` only when a caller requests `__version__`.

The dependency now declares `pydantic-ai-slim[openai]>=2,<3`. The OpenAI extra
is intentional because the default model is `openai:gpt-4.1-mini`. MCP, evals,
and Logfire extras are not part of Templateer's declared provider support.

The render-only path may support making the generation dependency an optional
`templateer[generate]` extra in a later API decision. This report does not make
that change.

## Post-change evidence

After `uv sync --locked --extra dev`, the environment reported
`pydantic-ai-slim` 2.23.0 and `openai` 2.53.0. It no longer installed the full
`pydantic-ai` distribution, MCP, evals, or Logfire packages.

The measured import time is:

| Measurement | Result |
| --- | ---: |
| baseline `import templateer` | 1719 ms |
| post-change `import templateer` | 186 ms |
| post-change with `PYDANTIC_DISABLE_PLUGINS=1` | 156 ms |
| clean import, `pydantic_ai` present | `False` |
| after `templateer.preload()`, `pydantic_ai` present | `True` |
| `import templateer.pipeline`, `pydantic_ai` present | `False` |

The full Templateer suite passed with 445 tests and skipped 9 tests. The
baseline suite had 444 passing tests; the additional passing test is the new
import regression guard. Ruff and ty both passed.

The consumer command used the Templateer source through `PYTHONPATH` and set
`PYDANTIC_DISABLE_PLUGINS=1` as an embedding-environment option. It passed 196
tests and deselected 2 slow tests. The consumer repository was not modified.

A temporary offline probe used Pydantic AI's `TestModel` with Templateer's
generator. It measured 130.4 ms for the first generation and 8.1 ms for the
second generation in one run. The first run included the one-time loader work;
the second run reused the module-level `Agent` attribute.

## Relevant primary references

- [Python `asyncio.to_thread` documentation](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread): documents running blocking import work in a separate thread.
- [Pydantic AI documentation](https://ai.pydantic.dev/): documents the agent framework used by the generation path.
- [Python packaging dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/): defines extras such as `openai`.

## Remaining proof and removal conditions

The tests prove the import boundary and preserve the patchable `Agent`
attribute. They do not prove provider throughput because the suite does not
make provider calls. A real generation benchmark should compare first and
second offline test-model runs before release.

The lazy loaders can be removed only if Templateer drops the render-only API
promise or if the generation framework becomes dependency-free at import time.
The slim dependency can be widened only when Templateer deliberately supports
the added provider or feature extra. Do not set
`PYDANTIC_DISABLE_PLUGINS` inside Templateer; embedding callers must choose
that policy.
