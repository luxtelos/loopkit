# Adopting LoopKit in a project that already has hooks

GETTING-STARTED covers a fresh project. This page is for the other case: a
repo that already carries its own copies of loop hooks, contracts or state
files, and wants the plugin to own them from now on.

## Order of operations

1. **Install the plugin, do not init yet.** With the plugin active and the
   project's own hooks still wired, every prompt fires both — you will see two
   doctrine blocks per tick. That is the signal, not a bug.
2. **Run the doctor.** `/loopkit:doctor` lists every project hook whose
   basename matches a plugin hook as `DUPLICATE`. The fix is deletion of the
   project copy, never a merge of the two — a hook that exists twice with two
   versions of one rule is worse than either alone.
3. **Init, and read the KEPT lines.** `loopkit-init.sh` never overwrites:
   every existing `state/`, `inbox/`, contract or `.loopkit/` file prints
   `KEPT`. Diff each kept file against the plugin template once, by hand, and
   take the template's newer lines where they apply.
4. **Point the memory registry at what is real.** `.loopkit/memory.json`
   names the indexed graph project and the palace path. `memory.py status`
   prints `DEGRADED` with the exact index command for anything not there yet;
   leave it degraded rather than pointing it at a store that belongs to
   another checkout.
5. **Dry-run the rulings extractor.** `rulings-extract.py --dry-run` lists the
   decisions already buried in `inbox/`, ADRs, specs and `state/`. Nothing is
   written; the owner ratifies which become concepts. That ratification is
   human by design.
6. **Record the baseline before ratifying anything.** `loop-metrics.py` over
   an empty ticks ledger prints `n=0` for every number. Commit that line: the
   falsification clock for the knowledge layer starts there.
7. **Seed the test baseline** once: `test-regressions.sh --update-baseline`.
   From then on a green gate means "no new failures", and only that.

## Gotchas

- **Your `.gitignore` / `.prettierignore` decisions survive init, once init
  has seen them.** Init writes `.loopkit/config.env` and `state/triage.md` one
  time each, with a `# loopkit:decided <line>` marker beside them. If the line
  is already in your file when you first init, init adds only the marker
  (`MARKED`), and from then on the file is yours: delete the line, keep the
  marker, and later runs print `KEPT`. Delete both and init reads it as a file
  that has never seen the line, and adds it back. Adopters coming from an older
  install get the marker on their next init — that one run writes a comment
  line, and nothing after it does.
- Two `CLAUDE.md` files load in a git worktree — the parent's and the
  worktree's. If the parent already carries the LoopKit block, init in the
  worktree prints `KEPT` for it only when the marker is present in the
  worktree's own copy.
- Deleting a project hook is a governance write in some repos; run the
  deletion from the project's own rules, not the plugin's escape variable.
- `dev`-style env configs that belong to another branch still work when
  pointed at by mistake. The registry refuses to fan out across stores for
  the same reason: one palace, one project, per checkout.
