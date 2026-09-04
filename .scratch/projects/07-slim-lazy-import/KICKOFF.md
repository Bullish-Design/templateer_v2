# 07 — a slim, lazy `pydantic_ai` import

Kickoff for project 07. Written 2026-09-04 from measurements taken by a
consumer project, `~/Documents/Projects/devman-spike`, against this repository
at commit `736f573`. **This repository was not modified to produce them** — the
work ran against temporary copies of `src/templateer`.

## The task

`import templateer` costs **1,872 ms**. A consumer that only renders and
validates — never calls a model — pays all of it, and **1,451 ms of that is
`pydantic_ai`**, of which 1,059 ms is a Model Context Protocol client stack.

Investigate and fix this. Two changes, and they belong together:

1. **Depend on `pydantic-ai-slim`, not the full `pydantic-ai` metapackage.**
2. **Import `pydantic_ai` on first generation, not on `import templateer`.**

Target: `import templateer` at about **143 ms**, with Templateer's own suite
unchanged at **444 passed, 9 skipped**, and no change to generation throughput.

## Where the evidence comes from

Every number here was measured against `templateer_v2` at commit `736f573`, on
NixOS, Python 3.13.14, by a consumer project. **`templateer_v2` itself was never
modified** — the work ran against temporary copies of `src/templateer`. Two
documents in `~/Documents/Projects/devman-spike` hold the detail:

- `TEMPLATEER_PROPOSALS.md` — sections 1, 1a, 2, 3, 7.2 are this task. Sections
  5 and 6 are separate proposals; see "Out of scope".
- `COMPILED_PYTHON_NOTE.md` — section 4 is the packaging consequence.

**Do not trust those numbers. Re-measure on your machine before you change
anything, and again after.** They are a description of what was found, not a
specification.

## Phase 1 — investigate before you edit

Reproduce the problem and write down what you get.

```bash
python -c "import time; t=time.perf_counter(); import templateer; print((time.perf_counter()-t)*1000)"
python -X importtime -c "import templateer" 2>&1 | sort -t'|' -k2 -rn | head -20
```

Confirm each of these, and say so explicitly in your report if any is wrong:

- `pyproject.toml` declares `pydantic-ai>=2,<3` — the full metapackage. It pulls
  `fastmcp`, `mcp`, `mcp_types`, `pydantic_evals` and `logfire` into the
  environment. Check what is actually installed.
- `src/templateer/generator.py:17` — `from pydantic_ai import Agent`. `Agent` is
  used **once**, at line 67, inside `generate_model_async`.
- `src/templateer/generator.py:21` — `DEFAULT_MODEL = "openai:gpt-4.1-mini"`. It
  is a plain string, and `api.py:54`, `result.py:13` and `cli.py:42` each import
  `generator` to read it. **Three modules pay 1.45 s for one string literal.**
- `src/templateer/pipeline.py:21` — `from pydantic_ai.exceptions import
  UnexpectedModelBehavior, UserError`. Both are used only inside one `try` block,
  at lines 159 and 162.
- `src/templateer/api.py:52` imports `templateer.audit` (about 90 ms), reached
  only by `TemplateRegistry.audit()`.
- `src/templateer/__init__.py:3` imports `importlib.metadata` for `__version__`
  (about 35 ms at every import).
- `asyncio` costs about 46 ms and is on the path only because `api.py` and
  `result.py` reach `generator`.

Then test the slim hypothesis **without installing anything**, by hiding the MCP
packages from the import system:

```python
import sys, time
class Blocker:
    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root in ("fastmcp", "mcp", "mcp_types"):
            raise ModuleNotFoundError(f"No module named {root!r}", name=root)
        return None
sys.meta_path.insert(0, Blocker())
t = time.perf_counter(); import templateer; print((time.perf_counter()-t)*1000)
```

Expected: it **succeeds**, at roughly 700 ms instead of 1,872 ms.
`pydantic_ai.capabilities._deferred_capability_loader` degrades on its own.
**If it raises instead, stop and report that** — the slim change is then not
safe and only the lazy change should proceed.

## Phase 2 — the changes

Make them in this order. Run the suite after each.

### 2.1 `DEFAULT_MODEL` gets a home that costs nothing to import

Create `src/templateer/constants.py` holding `DEFAULT_MODEL`. Re-export it from
`generator` so any existing import keeps working. Point `api.py`, `result.py`
and `cli.py` at `constants`.

*Why:* it is the reason `api.py` and `result.py` reach `generator` at all, and
therefore the reason `asyncio` and `pydantic_ai` are on the import path.

### 2.2 `Agent` loads on first generation, off the event loop

```python
# generator.py
Agent: Any = None
"""``pydantic_ai.Agent``, loaded on first use by ``_load_agent``."""


def _load_agent() -> None:
    global Agent
    if Agent is None:
        from pydantic_ai import Agent as _Agent

        Agent = _Agent


async def generate_model_async(...):
    ...
    await asyncio.to_thread(_load_agent)
    agent = Agent(model_name, ...)
```

**Two details are load-bearing. Do not simplify either away.**

**`Agent` stays a module attribute.** Five tests patch
`templateer.generator.Agent` — `tests/test_generator.py` lines 130, 150, 178,
195 and 210. A plain function-local import removes the attribute and they fail
with `AttributeError: module 'templateer.generator' does not have the attribute
'Agent'`. Measured: `444 passed` became `5 failed, 439 passed`. Assigning only
when the attribute is still `None` lets a patched mock survive.

**The load runs through `asyncio.to_thread`.** `api.py` says it itself: *"Agent
frameworks run an event loop. `generate_async` is therefore the primary entry
point."* A 2 s synchronous import inside that coroutine starves every other task.
Measured with a 10 ms heartbeat beside a first generation:

| the import happens… | worst heartbeat gap |
|---|---:|
| inline, in the coroutine | **1,546–1,704 ms** |
| through `asyncio.to_thread` | 90–99 ms |
| eagerly, at import (today) | 67–75 ms |

### 2.3 The pipeline's two exception classes load the same way

Load `UnexpectedModelBehavior` and `UserError` through `asyncio.to_thread`
before the `try` block that catches them. After 2.2 has run once this is a dict
lookup, but `_attempt_async` can be reached first, so it must not assume.

**Do 2.2 and 2.3 together or neither.** `Agent` alone took `import templateer`
from 1,872 ms only to 1,654 ms, because `pipeline.py` re-imports the whole
package for two exception classes.

### 2.4 The dependency

```toml
- "pydantic-ai>=2,<3",
+ "pydantic-ai-slim[openai]>=2,<3",
```

`[openai]` is required because `DEFAULT_MODEL` is `openai:gpt-4.1-mini`.
**Decide the provider extras deliberately** and say in the commit which
providers Templateer intends to support. Inheriting an MCP client stack by
accident is the thing being fixed.

Consider going further: with 2.2 and 2.3 done, `pydantic_ai` is not imported at
all on a render-only path, so it could become an **optional extra**
(`templateer[generate]`). That was verified — with `pydantic_ai` hidden
entirely, the patched package still imported and a consumer's 182 tests still
passed. It is a bigger API decision than a version bump, so **propose it, do
not just do it.**

### 2.5 Two smaller imports, if 2.1–2.4 land cleanly

- `templateer.audit` (~90 ms): import it inside `TemplateRegistry.audit()`;
  quote the return annotation or use `TYPE_CHECKING`. **Watch
  `tests/test_api.py::TestIntegration::test_registry_audit_exposes_schema_field_coverage`**
  — a first attempt at this broke it by leaving `audit_template` unbound.
- `__version__` (~35 ms): a module-level `__getattr__` in `__init__.py`.
- `templateer.pipeline` in `api.py:55`: import inside the method that calls it.

## What good looks like

| | before | after |
|---|---:|---:|
| `import templateer` | 1,872 ms | ~354 ms after 2.1–2.3; ~143 ms with 2.5 |
| the same, `PYDANTIC_DISABLE_PLUGINS=1` | 1,547 ms | ~160 ms |
| Templateer's suite | 444 passed, 9 skipped | **unchanged** |
| second and later generations | 8.4–17.4 ms | **unchanged** |

**Generation gets ~400 ms slower once per process, and not at all thereafter.**
Measured through the real `generate_async` on pydantic-ai's offline `test`
model: total to the first artifact goes from about 1,950 ms to about 2,350 ms;
the second generation is identical. Against a real model call of seconds, that
is inside the noise of one request — but **state it in the commit, do not hide
it.**

Give it back with a public `preload()`:

```python
def preload() -> None:
    """Import the generation stack now, so the first generation does not."""
    from templateer.generator import _load_agent

    _load_agent()
```

`cli.py`'s generate path should call it at startup, so Templateer's own
user-visible timing does not change at all.

## Verify

```bash
pytest -q                                  # must be 444 passed, 9 skipped
ruff check .
python -c "import time; t=time.perf_counter(); import templateer; print((time.perf_counter()-t)*1000)"
python -X importtime -c "import templateer" 2>&1 | sort -t'|' -k2 -rn | head -15
```

Then prove the consumer still works. `~/Documents/Projects/devman-spike` renders
eight artifact kinds through `render_from_model` and `validate_artifact`:

```bash
cd ~/Documents/Projects/devman-spike
PYTHONPATH=<your templateer src>:src PYDANTIC_DISABLE_PLUGINS=1 python -m pytest -q -m "not slow"
```

That suite was 182 passing against the patched copy. **Do not modify
devman-spike.** If a test there fails, that is your result, not its bug.

Add a test to Templateer's own suite that asserts `pydantic_ai` is **not** in
`sys.modules` after `import templateer`. That test, not a comment, is what keeps
the import from creeping back.

## Traps that were hit already

1. **A function-local `Agent` import breaks five tests.** §2.2.
2. **An inline lazy import stalls the event loop for 1.7 s.** §2.2.
3. **`Agent` alone is nearly worthless without the pipeline change.** §2.3.
4. **`PYDANTIC_DISABLE_PLUGINS=1` only pays once `pydantic_ai` is off the import
   path.** It saves ~325 ms against the shipped tree, ~20 ms against a slim
   install, and ~195 ms against the lazy patch. `logfire` arrives by two routes —
   the MCP chain, and Pydantic's plugin entry-point scan. This is a *consumer*
   environment variable; document it for embedding callers, **do not set it
   inside Templateer.** A consumer who wants Logfire is entitled to it.
5. **A first `audit` lazification left `audit_template` unbound** and broke one
   `test_api.py` test. §2.5.

## The argument that is not about speed

A consumer tried to package a render-only tool with Nuitka and
`--nofollow-import-to=pydantic_ai`. It failed at run time:

```text
File ".../templateer/api.py", line 54, in <module templateer.api>
File ".../templateer/generator.py", line 17, in <module templateer.generator>
ModuleNotFoundError: No module named 'pydantic_ai'
```

**Templateer as shipped cannot be packaged without the agent stack**, by any
build flag, for a consumer that never generates. Rebuilt against the lazy
loaders, the same command produced a working binary with no `pydantic_ai` in it,
rendering byte-identical output.

So this is not a start-up optimisation. It is what makes Templateer shippable as
a library rather than as an application.

## Out of scope

Do not do these here. They are real, and they are separate changes:

- **A `kind: python` validator.** `validate_output` accepts `parse`, `command`
  and `markdown` only. `command` costs 49.5 ms per artifact against 0.83 ms for
  the same check called as a function. `TEMPLATEER_PROPOSALS.md` §5.
- **`validate_artifact` returns `([], [])` for an unstructured language with no
  declared validator** — the same answer for a good artifact, for `"total
  garbage"`, and for the empty string. Every caller's `if errors:` guard is dead
  code and cannot tell. `TEMPLATEER_PROPOSALS.md` §6.
- Any behaviour change to rendering, validation or the CLI's interface.

## Report

- The before and after numbers **from your machine**, not from this prompt.
- Anything in Phase 1 that did not match, and what you did about it.
- The suite result, exactly.
- Whether `pydantic-ai` should become an optional extra, with your reasoning.
- What you did **not** do, and why.
