# 08 — Release state, consumers, and tooling after the `nix` language

Date: 2026-10-11. Base: `main` = `ac02b48c015d`.

This investigation changed no code, no tag, and no bookmark. It pushed nothing.
Five subagents collected the raw logs under
`~/.local/state/templateer/investigation-08/<topic>/`. The orchestrator checked
each claim against those logs, or ran it again. Small copies are in `evidence/`.

Each section keeps **Observed** (a command and its result) apart from
**Inferred** (a conclusion from the observations).

## Summary

- `main` has the `nix` language. `v6-flake` (tag `v0.4.2`) has the flake. Neither
  line contains the other. They conflict in one file, `tests/test_cli.py`.
- The flake builds `main`'s code after the orchestrator takes `main`'s side of that file. The build runs
  the test suite: 613 passed, 72 skipped.
- The next version should be `0.5.0`. The version lives only in `pyproject.toml`.
- Only `devenv-py` needs the `nix` language. Three consumers read the
  `templateer_v2` working copy through a path source. That working copy is on
  `70f13f57`, not on `main`.
- The push hook does not gate Templateer today. A minimal Testee 0.6.1 adoption
  went GREEN in a scratch repository.
- All three known `nix` gaps reproduce. The float tag has a worse effect than
  the known limit: comparisons and truth tests give wrong results with no error.

## Environment

Observed:

- `testee --version` → `testee 0.6.1`. `which -a testee` →
  `~/.nix-profile/bin/testee` only. The same holds inside `devenv shell`. No
  Testee 0.5.1 was on `PATH`.
- `which -a gitman` → `/etc/profiles/per-user/andrew/bin/gitman`. `gitman work`
  needs `--path` because `GITMAN_WORKSPACE_ROOT` is not set.
- `jj 0.46.0`. `jj git push --tag <name>` exists.
- The session shell exports `UV_PROJECT_ENVIRONMENT` and `VIRTUAL_ENV` as
  `templateer_v2/.devenv/state/venv`. That venv is an editable install of the
  default working copy, so it reports `0.4.1` and has no `nix` language.
- In a new workspace, a bare `pytest` inside `devenv shell` resolves to the
  main checkout's venv until `uv sync --extra dev` runs. It then imports the
  wrong `src/` and fails: `ImportError: cannot import name 'escape_nix_string'`.
- `devenv shell` prints `MYPI_AGENT_ROOT must stay within project root` and a
  missing `check-codex-assets-installed.sh` in a workspace. Neither affects the
  commands.

Inferred: the safe gate invocation is
`env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV SECRETSPEC_REASON=<text> devenv shell -- uv run --extra dev python -m pytest -q`
(and the same form for `ruff` and `ty`). Bare tool names are safe only after
`uv sync --extra dev` in that checkout.

## Q1 — History: `main` and `v6-flake`

Observed:

- Graph: `main` `ac02b48c` → `d3304db4` (nix language) → `03adf25a` (version
  test) → `70f13f57`. `v6-flake` = tag `v0.4.2` = `a342d1e4`, parent `70f13f57`.
- Files that `v6-flake` changes (8 files, +210/−3): new `flake.nix`, `flake.lock`,
  `nix/minijinja.nix`, `nix/package.nix`; changed `README.md`, `pyproject.toml`
  (0.4.1 → 0.4.2), `uv.lock`, `tests/test_cli.py`.
  See `evidence/diff-v6-flake-since-70f13f57.txt`.
- Files that `main` changes (12 files, +1444/−14): new `nix_render.py`,
  `escaping.py` changes, `tests/test_nix_language.py`; changed `audit.py`,
  `models.py`, `renderer.py`, `validators.py`, `conftest.py`, `test_audit.py`,
  `test_cli.py`, `README.md`, `CONTRIBUTING.md`.
  See `evidence/diff-main-since-70f13f57.txt`.
- `jj new main v6-flake` → `tests/test_cli.py 2-sided conflict`. `README.md`
  merges cleanly. `jj duplicate a342d1e4 -d main` conflicts in the same file.
  See `evidence/merge-v6-flake-onto-main.txt`.
- The conflict: both sides fixed the stale `"0.4.0"` assertion in
  `test_version_flag`. `main` reads `pyproject.toml` (`PROJECT_VERSION`,
  `tests/test_cli.py:19-22`, `:651-654`). `v6-flake` uses
  `importlib.metadata.version` (`tests/test_cli.py:10`, `:649`).
- After `jj restore --from main tests/test_cli.py` in the merge:

  | Command | Result |
  |---|---|
  | `nix flake show` | pass |
  | `nix build .#minijinja -L` | pass |
  | `nix build .#templateer -L` | pass; sandbox pytest 613 passed, 72 skipped |
  | `nix build .#templateer-lib -L` | pass |
  | `nix flake check -L` | "all checks passed"; warning: `apps.x86_64-linux.default` lacks `meta` |
  | `./result/bin/templateer --version` | `templateer, version 0.4.2` |
  | `ParsedLanguage` in the built package | `('nix',)` |

- `v6-flake` alone: `nix build .#templateer` → 445 passed, 9 skipped.
- Flake outputs per system (`x86_64-linux`, `aarch64-linux`):
  `packages.{default,templateer,templateer-lib,minijinja}`, `overlays.default`
  (adds `minijinja` and `templateer` to `pythonPackagesExtensions`),
  `apps.default`, `checks.templateer`. No `devShells`. nixpkgs pin `e7439b6b`.
  Python 3.13.
- `nix/package.nix:16,20` reads the version from `pyproject.toml`.
  `nix/package.nix:48-55` runs `pytestCheckHook` and `templateer --help`.
- In the sandbox, the 72 skips are 9 API-key tests and 63 `needs_nix` tests.
  The sandbox has no `nix-instantiate` (`tests/conftest.py:43-58`).

Inferred:

- Land the flake as a copy of `a342d1e4` on `main`, and keep `main`'s
  `tests/test_cli.py`. The orchestrator tested this result. The new commit does not match tag
  `v0.4.2`. That is correct: `v0.4.2` stays where consumers locked it.
- Add `pkgs.nix` to the package's `nativeCheckInputs` so the 63 `needs_nix`
  tests run in the build. This is untested.

## Q2 — Versioning

Observed:

- `pyproject.toml:3` = `0.4.1` on `main`, `0.4.2` on `v6-flake`. `uv.lock:806`
  matches each side.
- `src/templateer/__init__.py:15-27`: lazy `__version__` from
  `importlib.metadata.version("templateer")`, fallback `0.0.0+unknown`.
- The CLI `--version` comes from Click and package metadata. No literal.
- Tests: `tests/test_cli.py:651-654` compares with `pyproject.toml`.
  `tests/test_surface.py:327` compares `__version__` with package metadata.
- No CHANGELOG. `CONTRIBUTING.md:241-245` says: change the version in
  `pyproject.toml` only.
- `nix/minijinja.nix:12` hard-codes the MiniJinja binding at `2.22.0`, with two
  hashes (`:16`, `:27`). `uv.lock:385` resolves `minijinja` `2.22.0`.

  | Tag | Commit | pyproject | Ancestor of `main` | Kind |
  |---|---|---|---|---|
  | v0.2.0 | dc0dd9f2 | 0.2.0 | yes | annotated |
  | v0.3.0 | 4554450c | 0.3.0 | yes | annotated |
  | v0.4.0 | 08d5b0b3 | 0.4.0 | yes | annotated |
  | v0.4.1 | 73afed43 | 0.4.1 | yes | annotated |
  | v0.4.2 | a342d1e4 | 0.4.2 | **no** | lightweight |

  Source: `git ls-remote --tags origin` (the `^{}` lines mark annotated tags).

Inferred:

- Choose `0.5.0`. The `nix` language is a new feature and widens the closed
  language set (`models.py:14`, `ParsedLanguage = Literal["nix"]`). Code that
  matches the set exhaustively can fail type checks. Under SemVer 0.x that is a
  minor bump. `0.4.3` hides a feature in a patch.
- Do not move or reuse `v0.4.2`. copyroom `v0.8.1` locks it.
- A tag needs: `pyproject.toml` version, a regenerated `uv.lock`, and the README
  flake pin. The flake, `__version__`, and the tests follow `pyproject.toml`.
- A MiniJinja bump in `uv.lock` breaks the flake until `nix/minijinja.nix` gets
  the new version and both hashes.

## Q3 — Consumers

**Update 2026-10-12:** copyroom `main` changed after this table was written. See the addendum at the end.

Observed (logs in `consumers/`; pins in `evidence/consumer-pins.txt`):

| Consumer | Pin | Mechanism | Needs | Effect of `0.5.0` |
|---|---|---|---|---|
| devenv-py | rev `ac02b48c` (`pyproject.toml:34`); `uv.lock` 937-940 records 0.4.1 | uv git source | `nix` language, `nix` and `nix_attr`, `$pkg`/`$output`/`$path`/`$nix`/`$priority`, `TemplateRegistry.from_paths` | None until it moves the pin. Move it to the tag and re-lock. |
| copyroom `main` | `templateer==0.4.1` (`:17`) and path `../templateer_v2`, editable (`:58`) | path source | Python API; writes `importlib.metadata.version("templateer")` into plan records | Reads whatever the default working copy holds. Its `uv.lock` goes stale. |
| copyroom `v0.8.1` | flake input `?ref=refs/tags/v0.4.2` (`flake.nix:24`), uses `overlays.default` (`:48`) | flake input | overlay attributes `templateer`, `minijinja` | None. A move needs a new `flake.lock`. |
| copyroom `v0.8.1` devenv module | `packages/templateer.nix:16-22`: version 0.4.2, rev `a342d1e4`, inline hash | `fetchFromGitHub` copy | the package | A second, independent copy of the pin. A move needs a manual bump. |
| vendomat `main` | none in pins; `templates/workspace/init:12-31` runs `templateer render` from `PATH` or `$VENDOMAT_TEMPLATEER` | CLI | `render` | None. |
| vendomat V6 docs | `16-…/evidence/step-8a.md:25,166`: `?ref=refs/tags/v0.4.2` | docs only | flake output names | Docs need `v0.5.0`. `17-…/evidence/step-9.md:82-86` asks for a tag on `main`. |
| vendomat `v0.4.5` toolchain, nix-meta | `?ref=refs/tags/v0.4.1`, locked rev `73afed43` | non-flake input | CLI | None. |
| argentic | extra `templateer>=0.2` and absolute path source, `editable = false` (`:102`); `uv.lock` records 0.4.1 | path source | API, `kind: region` | Accepts 0.5.0. Re-lock. |
| devman-spike | unpinned, path `../templateer_v2` (`:17`); `uv.lock` records 0.3.0 | path source | `TemplateRegistry` (lazy import) | Already stale. Re-lock. |

- Host: `which -a templateer` finds only the project venv (`0.4.1`).
  `nix profile list` has no templateer. nix-meta's `developer.nix:12` lists it
  through vendomat (v0.4.1). That profile is not installed.
- devenv-py workarounds: `null` → `{"$nix": "null"}`
  (`src/devenv_py/writer_nix.py:178-185`); it reads `$nix` back from the
  parenthesised node (`read_syntax.py:29-30,784`); it treats a missing
  `$output` as `"out"` (`read_eval.py:406`).
- Experiment (`evidence/uv-exact-pin-vs-path-source.txt`): with uv 0.12.1, a
  project that pins `templateer==0.4.1` and uses an editable path source at
  version `0.5.0` locks without error and records `0.5.0`.

Inferred:

- Only devenv-py needs the `nix` language. Only copyroom `v0.8.1` and the
  vendomat V6 docs need the flake. The flake output names are a contract:
  keep `packages.templateer`, `.templateer-lib`, `.minijinja`, `overlays.default`.
- The subagent said copyroom's `==0.4.1` pin "breaks" at 0.5.0. The
  experiment above shows it does not break under uv 0.12.1. The real effect is
  a stale `uv.lock`, so `uv sync --locked` fails until copyroom re-locks. The
  exact pin also becomes false documentation.
- The path consumers (copyroom, argentic, devman-spike) change when the default
  working copy changes, not when a tag lands. Today they get `70f13f57` code
  with no `nix` language.

## Q4 — Tooling

**Update 2026-10-12:** the gitman verify gate in this section is dead. See the addendum at the end.

Observed:

- The hook is registered in `~/.claude/settings.json:58` (PreToolUse, Bash,
  timeout 45). It matches `jj git push`, `git push`, `gh pr create`,
  `gh pr merge`, including inside `bash -c` and `devenv shell --`.
- A repository "declares Testee" when `devenv.yaml`, `devenv.nix`, or the
  `.local` variants match
  `testee/nix/testee\.nix|Bullish-Design/testee|\btestee\.(?:checks|prepare|package|keep)\b`
  (`testee_integrate_gate.py:306-309`). It reads the working tree.
- For a declaring repository, each pushed commit needs
  `testee --root R status -r <commit>` to exit 0. Otherwise the hook blocks.
  `TESTEE_GATE=off|warn` relaxes it. A missing `testee` blocks.
- Templateer does not declare Testee. A synthetic `jj git push -b main` through
  the hook → exit 0, no output (`evidence/hook-templateer-today.txt`).
- `testee status` here → `lock_missing … no devenv.lock` (exit 2).
  `testee doctor` → `module_missing`.
- `.gitignore:11` ignores `devenv.lock`.
- `gitman.toml:4` verify = `uv run --extra dev ruff check . && uv run --extra dev pytest`.
  It omits `ty`. `AGENTS.md:28-33` lists three commands.
- `AGENTS.md:25-26` says the shell has no `testee` or `gitman`. That is false.
- References: devenv-py `559964de` adds a `testee` input
  (`?ref=refs/tags/v0.6.1`, `flake: false`), imports `testee/nix/testee.nix`,
  sets `testee.prepare` and `testee.checks` with absolute venv paths and the
  `ruff-json` and `junit-xml` parsers, and commits `devenv.lock`.
  vendomat `e1434bb2` does the same and sets `verify = ["testee","verify","--full"]`.
- Scratch adoption (files in `evidence/tooling-proposed/`): with `devenv.lock`
  generated by `devenv update testee` and committed, `testee verify` → pytest
  676 passed, 9 skipped; ruff pass; ty pass; `GREEN … 3/3 checks`. The hook on
  `git push origin HEAD` → exit 0. In a declaring repository with no lock, the
  hook blocks (`evidence/hook-declared-simulation.txt`).
- Not tested: a real `jj git push` through the hook after adoption, and gitman
  with the new verify command.

Inferred:

- Adopt Testee 0.6.1 before the release. Then the hook gates every later push,
  and `gitman` verify, the hook, and `AGENTS.md` share one gate.
- Minimal adoption, modeled on devenv-py `559964de`:
  1. `devenv.yaml`: add the `testee` input and the import.
  2. `devenv.nix`: `testee.prepare = [ ["uv" "sync" "--locked" "--extra" "dev"] ]`
     (`--extra dev`, because the tools live in the `dev` extra), and checks
     `ruff` (quick, full), `ty` (full), `pytest` (full, timeout 900).
  3. `.gitignore`: drop line 11. Commit `devenv.lock`.
  4. `gitman.toml`: `verify = ["testee", "verify"]`.
  5. `AGENTS.md`: replace lines 25-33 with the Testee gate and a quick loop.
- One change from the scratch proposal: the scratch `pytest` check appends
  `/run/current-system/sw/bin` to `PATH` for `nix-instantiate`. That ties the
  check to NixOS. Put `${pkgs.nix}/bin` on `PATH` instead. This is untested.

## Q5 — `nix` language follow-ups

Observed at `main`:

- Full suite: 676 passed, 9 skipped (API-key tests). `-m needs_nix`: 63 passed.
  `nix-instantiate` 2.34.7 is on `PATH` inside and outside devenv. With
  `PATH=/usr/bin:/bin`: 167 passed, 63 skipped. `ruff` and `ty`: pass.
  See `evidence/test-counts.txt`.
- Implementation: `nix_render.py` (new in `d3304db4`; `tag_integral_floats`
  at `:221` from `ac02b48c`), `escaping.py:114` (`escape_nix_string`),
  `renderer.py:39-41,50-54`, `models.py:14`, `validators.py:185,467`,
  `audit.py:557,619,652,747`. `_audit_nix` parses fixtures; it runs no
  injection probe.

Reproductions (`evidence/nix-gaps.txt`, `evidence/nix-edge-cases.txt`; the
orchestrator re-ran the main cases with `evidence/nix-orchestrator-repro.py.txt`;
output in `evidence/nix-orchestrator-repro.txt`):

| Gap | Input | Expected | Actual |
|---|---|---|---|
| a. null | `nix_value(None)` | `null` | `EscapeError: null value passed to the nix filter…` |
| b. `.out` | `{"$pkg":"curl","$output":"out"}` | `pkgs.curl.out` | `pkgs.curl` (`nix_render.py:128`) |
| c. `$nix` parens | `{"$nix":"a"}` | `a` | `(a)` |
| d. float tag | `x=0.0`, `{{ (x > 1) \| nix }}` | `false` | `true` |
| d. float tag | `x=7.0`, `{{ (x == 7) \| nix }}` | `true` | `false` |
| d. float tag | `x=0.0`, `{% if x %}` | false branch | true branch |
| d. float tag | `x=7.0`, `{{ (x + 1) \| nix }}` | `8.0` | `RenderError: + operator on unsupported types map and number` |
| e. denormal | `nix_value(5e-324)` | error | `5.0e-324`; `nix-instantiate` rejects it |

- `x=7.5` behaves correctly. Only integral floats carry the tag.
- `pkgs.curl.outputName` = `"bin"`. `pkgs.curl.outPath` and
  `pkgs.curl.out.outPath` are different store paths (`evidence/nix-pkg-outputs.txt`).
- `(X)` parses in every value position tested. Only `{ (foo) = 1; }` fails, and
  `$nix` cannot reach a name position.
- `{"$nix": "1); evil = (2"}` renders valid Nix. A model-controlled
  `dict[str, Any]` field can inject raw Nix through `$nix`.
- All string escapes (`${`, `''`, backslash, control characters, U+2028,
  U+FEFF) round-trip through `nix-instantiate`.

Assessment, fix, and test plan (inferred):

| Gap | Risk | Fix | Tests |
|---|---|---|---|
| d. float tag | **High** for any template that compares, branches, or computes on a float field. Wrong Nix, no error. devenv-py's one template does not compare floats, so it is not hit today. | Stop tagging the context. Before render, rewrite each top-level `\| nix` in a `{{ }}` site to `\| tojson \| nix_json`; `nix_json` = `json.loads` then `nix_value`. A prototype passed 691 tests (`nix-lang/proto-all.diff`), with 3 ruff and 1 ty errors left. Cheaper option: keep the tag and make comparison and arithmetic fail loudly. | Positive: `x>1`, `x==7`, truth of `0.0`, `x+1` for 0.0, 7.0, 7.5. Negative: the rewrite ignores string literals, comments, `{% raw %}`; data that looks like template text stays data; `nix_json` rejects non-JSON. |
| b. `.out` dropped | **Medium.** Silent wrong output for multi-output packages (curl, openssl). | `nix_render.py:128`: append `$output` whenever it is set. Update README.md:348 and `tests/test_nix_language.py:204`. Changes rendered text for callers that send `"out"`. | Positive: `out` kept; absent `$output` → `pkgs.x`. Negative: empty or non-string `$output` raises. |
| a. null refused | **Medium**, usability. Each caller with an optional field needs a guard or `$nix`. | Add a `nix_nullable` filter (or an explicit `allow_null` option). Keep `nix` strict. Register it in `renderer.py:54` and `_NIX_FILTERS` (`audit.py:557`). | Positive: `None`, nested `None`, `mkDefault null` → `null`. Negative: `nix` still refuses `None`; the lint accepts the new filter and still rejects a bare site. |
| c. `$nix` parens | **Low**, cosmetic. The security point is raw injection, not the parentheses. | No code change. Document that `$nix` is raw Nix and must not carry model-controlled text. | Negative test that pins the current behaviour and the documented risk. |
| e. denormal float | **Low.** The validator catches it. | `_float_literal` (`nix_render.py:94`): raise `EscapeError` for non-zero `abs(v) < 2.2250738585072014e-308`. | Negative: `5e-324` and `1e-310` raise. Positive: the smallest normal float renders. |

Fixes b, a, and d change rendered output or devenv-py's workarounds. devenv-py
pins by revision, so it sees them only when it moves its pin.

## Q6 — Other findings

Observed:

- Workspaces before this run: `default` (`648507a3`, empty, on `70f13f57`),
  `retire-agent-instructions-20261003` (`c97b941a`, empty, on `70f13f57`),
  `tpl-nix-float` (`e9d9c8b6`, empty, on `main`), `tpl-nix-language`
  (`afd8ade5`, empty, on `d3304db4`), `v6-templateer` (`92730a5c`, empty, on
  `a342d1e4`). None holds work that `main` lacks. The only pushed work outside
  `main` is `v6-flake`.
- This run added `inv08-docs` (this document). The scratch workspaces
  `inv08-merge`, `inv08-tooling`, and `inv08-nixlang` were forgotten and their
  directories deleted. Their scratch commits were abandoned.
- `038-devman-migration@origin` = `6cfdd6df`. It is **not** an ancestor of
  `main`. Its only change, `.devman/project.toml`, is byte-identical on `main`.
  (The subagent said "already merged". That is wrong. The content is present,
  but the commit is not.)
- `CONTRIBUTING.md:18-41` module map omits `src/templateer/constants.py`.
- `README.md:249-259,325-360` documents `nix`. It has no "known limits" for
  the gaps in Q5.
- `.scratch/projects/06-adversarial-review/OUTPUT_LANGUAGE_DECISION_2026-08-20.md:5`
  says "Do not add Nix". `main` added it. No note records the new decision.
- `06/FOLLOWUPS.md:71-74`: Allium specs "still open".
  `.scratch/specs/allium/templates.allium:43-46` types `language` as free text
  and does not mention `nix`.
- `07/RESEARCH_REPORT.md:114-119`: a real generation benchmark before release
  is still open.
- MiniJinja binding: nixpkgs has no `python3Packages.minijinja`
  (`nix eval` → missing attribute). `nix search` finds only the CLI 2.21.0.
  The flake builds the binding from the PyPI sdist with `maturinBuildHook`.

Inferred:

- The dead bookmark `038-devman-migration@origin` can go. Deleting it is a push.
- The four old empty workspaces can be forgotten.
- Record a short decision note that supersedes the 06 "no Nix" decision.

## Addendum 2026-10-12 — decisions, prep results, and corrections

The sections above record the observations of 2026-10-11. They stay as written.
This addendum adds dated facts and corrections.

### Decisions (owner, 2026-10-12)

- D1: version `0.5.0`.
- D2: copy the flake commit with `jj duplicate`. Do not merge.
- D3: ship `0.5.0` first with documented limits. Fix the `nix` gaps in `0.6.0`.
- D4: adopt Testee 0.6.1 before the tag.
- D5: move the default working copy to `main` after the tag.
- D6: delete `038-devman-migration@origin`. Keep `v6-flake` until copyroom moves
  off `v0.4.2`. Forget the four old empty workspaces.
- Open: explicit go for the pushes in `PLAN.md` steps 6, 7, and 8.

### Prep results (`PLAN.md` steps 1-5, local only)

- Step 2 used `jj duplicate a342d1e4 --onto main`. It restored `tests/test_cli.py`,
  `pyproject.toml`, and `uv.lock` from `main`, so the flake commit stays at `0.4.1`.
  It added `meta` to `apps.default` (the warning is gone). It added `nix` to
  `nativeCheckInputs`, so the build runs all 63 `needs_nix` tests: 676 passed,
  9 skipped (the 9 need `OPENAI_API_KEY`). This confirms the untested
  suggestion in Q1.
- Step 3 used the Testee files from the proposal, with `${pkgs.nix}/bin` on the
  `pytest` check `PATH`. `devenv.lock` started from the main checkout's ignored
  lock. `devenv update testee` added only the `testee` node (tag `v0.6.1`, rev
  `b5ec947d`). `testee status` reports GREEN 3/3.
- Step 4 fixed `CONTRIBUTING.md` (`constants.py`), the `README.md` known limits,
  the 06 superseded note, the `templates.allium` language union, and a note in
  `06/FOLLOWUPS.md`.
- Step 5 set `pyproject.toml` to `0.5.0`. `uv.lock` changed only the `templateer`
  version line. The README flake pin is `v0.5.0`. MiniJinja is `2.22.0` in both
  `uv.lock` and `nix/minijinja.nix`. `nix build` prints `templateer, version 0.5.0`.
  `nix flake check` and `nix flake check --all-systems --no-build` pass.
- Numbers and ids: `evidence/release-prep-results.txt` and `PLAN.md`.

### Corrections

- Gitman 0.12 dropped `gitman.toml [publish]`. The gitman `CHANGELOG.md` (0.12.0,
  line 35) says: "Gitman also dropped `gitman.toml`, the lane model, and the
  pyjutsu dependency." The `verify` line in Q4 therefore never ran, and the plan
  removes it. `gitman.toml` keeps only `trunk = "main"`. The Q4 inference that
  gitman verify, the hook, and `AGENTS.md` share one gate is wrong: the hook
  and `AGENTS.md` share the Testee gate.
- The push hook runs only `jj git push --dry-run`, read-only commands, and
  `testee status`. It checks only the pushed tip commit. Probe: the Testee
  commit gives exit 0. The flake commit gives exit 2 (`lock_missing`).
- `testee verify` has no `--full` flag. The vendomat `verify` line quoted in
  Q4 does not apply here.
- Copyroom moved. Copyroom `main` is `c64b2740` ("Pin a local source to a Git
  ref"), after tag `v0.8.2` (`32d7f74a`). It pins `templateer==0.4.2` and
  `{ git = ..., tag = "v0.4.2" }` (`pyproject.toml:17,58`). Check with
  `jj -R ~/Documents/Projects/copyroom file show -r main root:pyproject.toml`.
  Copyroom is no longer a path consumer. Update to the Q3 table:

  | Consumer | Pin | Mechanism | Effect of `0.5.0` |
  |---|---|---|---|
  | copyroom `main` (`c64b2740`) | `templateer==0.4.2` (`:17`); git source `tag = "v0.4.2"` (`:58`) | uv git source | None until it moves the pin. Set `templateer>=0.5,<0.6` and `tag = "v0.5.0"`. |

  The path consumers are argentic and devman-spike only.
- Workspaces: `inv08-docs` and `rel-050-docs` are forgotten and deleted. The
  release stack lives in `rel-050`. Step 8 forgets `rel-050` last.
- uv 0.12.1 accepts `tag = "v0.4.2"` for a git source.

### Consumer dry run

The candidate is the `main` code at `0.5.0`. The baseline is each consumer's
current pin. Logs: `~/.local/state/templateer/investigation-08/release-prep/consumers/`.

| Consumer | Baseline | Candidate |
|---|---|---|
| devenv-py | 731 passed, 2 failed | 731 passed, 2 failed |
| copyroom | 266 passed, 1 skipped | 266 passed, 1 skipped |
| devman-spike | 201 passed, 4 skipped | 201 passed, 4 skipped |
| argentic (non-live subset) | 173 passed | 173 passed |

- The two devenv-py failures are `tests/test_options.py::test_hash_is_stable_over_two_runs`
  and `::test_fresh_export_equals_the_committed_export`. A `secretspec.enable`
  option-export diff causes them. Templateer is not involved. They fail the same
  way on the baseline.
- Devman-spike needs a git repository in the copy. Without one, 2 tests fail on both sides.
- One parallel copyroom run failed a test on the candidate because of a shared
  `/tmp` path. The sequential run passed.
- The argentic live gates did not run.
- Pin drafts: `evidence/consumer-drafts/`.
