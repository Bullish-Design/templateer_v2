# 08 — Plan to a tagged `v0.5.0` on `main`

**Status (2026-10-12):** steps 1-8 are done. Step 9 (consumer updates) is in
progress; other agents run it. Step 10 is in progress.

Steps 6-10 are still a proposal. Evidence for each claim is in `REPORT.md`.
Results for steps 1-5 are in `evidence/release-prep-results.txt`.

Goal: one tag on `main` that every consumer can pin. The tag holds the `nix`
language, the flake, and a Testee gate.

## Decided

The owner decided D1-D6 on 2026-10-12. One approval is still open.

| Id | Question | Decision |
|---|---|---|
| D1 | Version: `0.5.0` or `0.4.3`? | `0.5.0`. The `nix` language widens a closed set. |
| D2 | Land the flake as a copy of `a342d1e4` on `main`, or as a merge commit? | Copy it (`jj duplicate`). Do not merge. |
| D3 | Fix the `nix` gaps in `0.5.0`, or ship `0.5.0` first? | Ship `0.5.0` first with documented limits. Fix the gaps in `0.6.0`. |
| D4 | Adopt Testee 0.6.1 before the tag? | Yes, before the tag. |
| D5 | Move the default working copy to `main` after the tag? | Yes, after the tag. Tell the path consumers' owners first. |
| D6 | Cleanup: dead remote bookmark, `v6-flake`, four old empty workspaces? | Delete `038-devman-migration@origin`. Keep `v6-flake` until copyroom moves off `v0.4.2`. Forget the four old empty workspaces. |
| Push | Explicit go for the pushes in steps 6, 7, and 8 | **Open.** No push runs until the owner says go. |

## Stack after steps 1-5

One linear stack sits on `main` (`ac02b48c`). Workspace `rel-050` holds it, with
an empty working copy on top. Commit ids change whenever the stack is rewritten.
Use the change ids, which stay stable. Run `jj log -r 'main::'` for the current
commit ids.

| Order | Change id | Description |
|---|---|---|
| 1 | `txtnyrvw` | docs: add investigation 08 on release state, consumers, and tooling |
| 2 | `qzkukvyx` | feat: add a flake that builds templateer and the MiniJinja binding |
| 3 | `ommuorol` | chore: verify through Testee 0.6.1 |
| 4 | `ryvsmxot` | docs: record the nix language limits and current module map |
| 5 | `lsrkxkxx` | chore: release 0.5.0 (the version commit; push this tip) |

The workspaces `inv08-docs` and `rel-050-docs` are forgotten and deleted.
Results and numbers are in `evidence/release-prep-results.txt`.

## Shared commands

```bash
R=~/Documents/Projects/templateer_v2
W=~/Documents/Projects/gitman-workspaces/rel-050
DEV='env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV SECRETSPEC_REASON=release devenv shell --'
GATE="$DEV bash -c 'uv run --extra dev python -m pytest -q && uv run --extra dev python -m ruff check src/ tests/ templates/ && uv run --extra dev python -m ty check src/'"
```

Expected gate result at each step: no failures. Today's baseline is 676 passed,
9 skipped.

## Step 1 — Open the release workspace — DONE

Done: workspace `rel-050` opened on `main` (`ac02b48c`). No workspace held work that `main` lacked.

```bash
cd $R && jj workspace list
gitman work rel-050 --from main --path $W
cd $W && jj log -r @-
```

Verify: `@-` is `ac02b48c` (or the current `main`). Report any workspace that
holds work `main` lacks.

Rollback: `jj workspace forget rel-050` from `$R`; delete `$W`.

## Step 2 — Land the flake on `main` (D2) — DONE

```bash
Done (change `qzkukvyx`): `jj duplicate a342d1e4 --onto main`. Then restore
`tests/test_cli.py`, `pyproject.toml`, and `uv.lock` from `main`. The flake
commit stays at `0.4.1`. It adds `meta` to `apps.default`, so the warning is
gone. It adds `nix` to `nativeCheckInputs`, so the build runs all 63
`needs_nix` tests. The commit body no longer says "Release 0.4.2". The
`.gitignore` gains `result` and `result-*`, so a bare `nix build` leaves no
tracked link.

```bash
cd $W
jj duplicate a342d1e4 --onto main    # prints the new change id; call it F
jj edit F
jj restore --from main tests/test_cli.py pyproject.toml uv.lock
jj resolve --list                    # expect: no conflicts
jj describe -m "feat: add a flake that builds templateer and the MiniJinja binding"
```

Keep `pyproject.toml` at `0.4.1` here; step 5 sets the release version.

Verify:

```bash
eval "$GATE"
nix build .#templateer -L     # result: 676 passed, 9 skipped (the 9 need OPENAI_API_KEY)
nix build .#minijinja -L
nix flake check -L            # expect: "all checks passed"
./result/bin/templateer --version
```

Rollback: `jj abandon F`.

## Step 3 — Adopt Testee 0.6.1 (D4) — DONE

Done (change `ommuorol`): the Testee files come from the proposal in
`evidence/tooling-proposed/`. The `pytest` check puts `${pkgs.nix}/bin` on
`PATH`, not `/run/current-system/sw/bin`. `devenv.lock` started from the main
checkout's ignored lock. `devenv update testee` added only the `testee` node
(tag `v0.6.1`, rev `b5ec947d`). `testee status` reports GREEN 3/3.

`gitman.toml` keeps only `trunk = "main"`. The proposal set `verify` there, but
gitman 0.12 dropped `[publish]` (see the REPORT addendum). `AGENTS.md` names
Testee as the gate and does not rely on gitman verify.

```bash
cd $W && jj new -m "chore: verify through Testee 0.6.1"
# edit devenv.yaml, devenv.nix, gitman.toml (trunk only), AGENTS.md; delete `devenv.lock` from .gitignore line 11
$DEV true                                 # creates devenv.lock
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV SECRETSPEC_REASON=release devenv update testee
jj new                                    # freeze the commit; call it T
testee --version                          # expect 0.6.1
testee verify -r T
testee status -r T                        # expect exit 0, GREEN 3/3
```

Verify: `testee status -r T` exits 0. `devenv.lock` is tracked (`jj file list -r T | grep devenv.lock`).

Rollback: `jj abandon T`. The hook stops gating once the tree no longer declares Testee.

## Step 4 — Fix the stale docs — DONE

Done (change `ryvsmxot`, "docs: record the nix language limits and current
module map"): all four items below, plus a note in `06/FOLLOWUPS.md`. The
`README.md` bullet for `$output: "out"` names `{"$nix": "pkgs.x.out"}` as the
only workaround and warns that `$nix` text is raw.

```bash
jj new -m "docs: record the nix language limits and current module map"
```

- `CONTRIBUTING.md` module map: add `constants.py`.
- `README.md` `nix` section: list the limits in REPORT.md Q5 (null refused,
  `$output: "out"` dropped, `$nix` is raw and must not carry model text,
  integral floats fail in comparison and arithmetic).
- `.scratch/projects/06-adversarial-review/`: add a note that supersedes the
  "no Nix" decision.
- `.scratch/specs/allium/templates.allium`: mark the language set as stale, or
  update it (the open 06 follow-up).

Verify: `testee verify -r @-` GREEN.

Rollback: `jj abandon` the commit.

## Step 5 — Set the version (D1) — DONE

Done (change `lsrkxkxx`): `pyproject.toml` is `0.5.0`. `uv.lock` changed only
the `templateer` version line. The README flake pin is `v0.5.0`. MiniJinja is
`2.22.0` in both `uv.lock` and `nix/minijinja.nix`. `nix build` prints
`templateer, version 0.5.0`. `nix flake check` and
`nix flake check --all-systems --no-build` pass.

```bash
jj new -m "chore: release 0.5.0"
# pyproject.toml:3 → version = "0.5.0"
$DEV uv lock
# README.md flake pin: v0.4.2 → v0.5.0
grep -n '^version' nix/minijinja.nix; grep -A1 'name = "minijinja"' uv.lock   # expect the same version
jj new                                    # freeze; call it V
testee verify -r V && testee status -r V
nix build .#templateer && ./result/bin/templateer --version   # expect 0.5.0
```

Rollback: `jj abandon V`.

## Step 6 — Land and push `main` — DONE

```bash
cd $W && jj bookmark set main -r lsrkxkxx   # V, the version commit, not the empty @
testee status -r main                      # expect exit 0
jj git push -b main                        # run alone, not chained
```

The push sends the whole stack (five commits). The hook checks only the pushed
tip commit. Probe: the Testee commit exits 0; the flake commit exits 2
(`lock_missing`). The hook runs only `jj git push --dry-run`, read-only
commands, and `testee status`. `testee verify` has no `--full` flag.

Verify: `jj log -r 'main@origin'` = `V`. The hook allowed the push.

Result (2026-10-12): `main` is pushed at `55af56f91ecf`.

Rollback before push: `jj bookmark set main -r ac02b48c --allow-backwards`.
After push: do not rewrite `main`. Revert with a new commit.

## Step 7 — Tag and push the tag — DONE

```bash
jj tag set v0.5.0 -r main
jj git push --tag v0.5.0
git ls-remote --tags origin | grep v0.5.0  # read-only check
```

Older tags `v0.2.0`–`v0.4.1` are annotated. `jj tag set` makes a lightweight
tag, like `v0.4.2`. Both work for `?ref=refs/tags/...` and uv `tag =`.

Result (2026-10-12): tag `v0.5.0` is pushed at `55af56f91ecf`. The tag builds
from GitHub and prints `0.5.0`. The `narHash` is
`sha256-JgJQ9zSwvuTJ7disfUQtDpvgmcRctgBNflvQ0NZ1oAY=`. The uv source
`tag = "v0.5.0"` resolves.

Verify from a clean place:

```bash
nix build --out-link /tmp/templateer-tag-check 'git+https://github.com/Bullish-Design/templateer_v2?ref=refs/tags/v0.5.0#templateer' && /tmp/templateer-tag-check/bin/templateer --version
```

Rollback: never move a pushed tag. Fix forward with `v0.5.1`.

## Step 8 — Main checkout and cleanup (D5, D6) — DONE

```bash
cd $R && jj new main                       # default working copy is empty today
jj workspace forget rel-050 retire-agent-instructions-20261003 tpl-nix-float tpl-nix-language v6-templateer
rm -rf $W ../gitman-workspaces/{tpl-nix-float,tpl-nix-language,v6-templateer} .worktrees/retire-agent-instructions-20261003
# D6, needs push approval. The bookmark exists only at origin, so track it, then delete it:
jj bookmark track 038-devman-migration@origin
jj bookmark delete 038-devman-migration
jj git push --deleted
```

The workspaces `inv08-docs` and `rel-050-docs` are already gone. This
investigation's change (`txtnyrvw`) is the first commit of the stack, so step 6
lands it.

Result (2026-10-12): the default checkout moved to `main`, and its venv reports
`0.5.0`. The five workspaces are forgotten. The Testee push hook blocked the
deletion of `038-devman-migration`, because the old commit (`6cfdd6df`) has no
`devenv.lock`. The owner approved a bypass of the gate for that one deletion.
The agent deleted the branch with `gh api -X DELETE` on 2026-10-12. The hook
reads `TESTEE_GATE` only from the Claude Code process.

Verify: `jj workspace list` shows only `default`. `templateer --version` in the
project venv prints `0.5.0` after `uv sync`.

Rollback: `jj new 70f13f57` restores the old view for path consumers.

## Step 9 — Consumer pin updates — IN PROGRESS

Each consumer owns its change. Run its own gate after each one.

| Consumer | Change | Verify |
|---|---|---|
| devenv-py | `pyproject.toml:34`: `rev = "ac02b48c…"` → `tag = "v0.5.0"`; `uv lock` | its suite; RT7 stays 554/554 and 515/515 |
| copyroom `main` (`c64b2740`, after tag `v0.8.2`) | `pyproject.toml:17`: `templateer==0.4.2` → `templateer>=0.5,<0.6`. `pyproject.toml:58`: `{ git = …, tag = "v0.4.2" }` → `tag = "v0.5.0"`. `flake.nix:24` ref → `v0.5.0`. `flake.lock`: `nix flake update templateer`. `packages/templateer.nix`: version, rev, hash → 0.5.0. Run `uv lock`. | `uv sync --locked`; `nix build`; its suite |
| vendomat V6 docs | `step-8a.md:25,166` ref → `v0.5.0` | doc review |
| argentic | `uv lock` | its suite |
| devman-spike | `uv lock` (stale since 0.3.0) | its suite |
| vendomat v0.4.5 / nix-meta | none (stays on `v0.4.1`) | — |

Copyroom is no longer a path consumer. Only argentic and devman-spike read the
`templateer_v2` working copy through a path source.

Drafts of the pin changes are in `evidence/consumer-drafts/`:
`devenv-py-pin.diff`, `copyroom-pin.diff`, `vendomat-pin.diff`, and
`relock-notes.txt`. The dry run (REPORT addendum) shows the same results on
`0.5.0` as on the current pins for all four consumers.

Rollback: each consumer restores its previous pin and lock.

## Step 10 — `0.6.0`: `nix` language fixes (D3) — fixes landed on `main`, no version bump

Use one workspace per fix. Order by risk: float tag, `$output: "out"`,
`nix_nullable`, denormal guard, `$nix` documentation. REPORT.md Q5 gives the
design and the tests. Each fix needs a negative test. Coordinate with devenv-py:
its `{"$nix": "null"}` and `$output` workarounds change meaning.

Result (2026-10-12): the five fixes are on `main`. No version bump and no tag;
the owner decides the `0.6.0` tag. The float fix routes `| nix` through
`| tojson | nix_json`, because MiniJinja 2.22.0, 2.24.0, and 3.0.0 all pass
`7.0` to a Python filter as the int `7`. `$output: "out"` renders `.out` now.
`nix_nullable` renders `None` as `null`. A subnormal float raises. The README
states that `$nix` is raw Nix. devenv-py must drop its `{"$nix": "null"}` and
`$output` workarounds when it moves to these fixes.
