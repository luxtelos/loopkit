#!/usr/bin/env bash
# stop-gate-branch-shapes.sh — the stop gate must gate on every branch shape a
# real checkout can be in, not only on a named branch that has a local `main`.
#
# WHY THIS PIN EXISTS. The gate was fixed once to look at the whole branch
# against its merge base instead of only the working tree. That fix asked
# `git rev-parse --abbrev-ref HEAD` for a branch name and `git show-ref
# refs/heads/main` for the trunk, and so reverted to the old broken behaviour
# on four shapes:
#
#   * detached HEAD          — how every review worktree here is made, and how
#                              CI checks out a pull request head
#   * a default branch named `develop` or `trunk` — this is a distributed
#                              plugin, other repos' trunks are not called main
#   * no local `main` ref    — the ordinary CI checkout
#   * an orphan branch       — no common ancestor at all
#
# Each one silently reverted to "working tree only", which is the exact bug the
# gate exists to prevent. A merge base does not care what a branch is called or
# whether it has a name, so the gate must never ask.
#
# Two control shapes are pinned alongside them, because a gate that gates on
# everything is as useless as one that gates on nothing: an unchanged branch
# must still SKIP, and a plain feature branch must still run.
#
# usage: bash tests/pins/stop-gate-branch-shapes.sh [<repo-root>]

set -uo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
# Absolutise it. This pin cds into fixture repos, so a RELATIVE argument — the
# perfectly plausible `bash tests/pins/stop-gate-branch-shapes.sh .` — made
# every subsequent "$REPO/..." path resolve inside the fixture and reported 13
# failures that were not there. The suite passes it absolute, so the suite never
# saw it. A pin that cries wolf on a plausible invocation teaches people to
# ignore it, which is the same reasoning as the gpg-signing fix below.
REPO="$(cd "$REPO" 2>/dev/null && pwd)" || { echo "FAIL no such repo root: $1"; exit 2; }
GATE="$REPO/plugins/loopkit/hooks/stop_gate.sh"
[ -f "$GATE" ] || { echo "FAIL no stop_gate.sh at $GATE"; exit 2; }

fails=0
ok()   { echo "  ok   $1"; }
bad()  { echo "  FAIL $1"; fails=$((fails+1)); }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/loopkit-branch-shapes.XXXXXX")"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT

# `-c commit.gpgsign=false`: a fixture must not depend on the caller's commit
# signing. With signing on and the agent locked, every fixture commit fails and
# the pin reports defects that are not there.
git_q() { git -c user.email=t@t -c user.name=t -c init.defaultBranch=main -c advice.detachedHead=false -c commit.gpgsign=false "$@"; }

# An upstream whose default branch is named <2>, cloned to <1>. Cloning is what
# gives the clone an `origin/HEAD`, which is the only name-independent record of
# what the project's trunk actually is.
mk_clone() {  # <name> <default-branch>
    local name="$1" trunk="$2" up="$WORK/$1.upstream" wt="$WORK/$1"
    mkdir -p "$up"
    git_q init -q -b "$trunk" "$up" >/dev/null 2>&1
    printf 'print("base")\n' > "$up/app.py"
    git_q -C "$up" add app.py >/dev/null 2>&1
    git_q -C "$up" commit -q -m "base" >/dev/null 2>&1
    printf 'print("second")\n' > "$up/other.py"
    git_q -C "$up" add other.py >/dev/null 2>&1
    git_q -C "$up" commit -q -m "second" >/dev/null 2>&1
    git_q clone -q "$up" "$wt" >/dev/null 2>&1
    printf '%s' "$wt"
}

# Run the gate in <1> and echo its combined output. The suite is a marker echo,
# so "the gate ran the suite" is observable rather than inferred.
run_gate() {  # <worktree> [env assignments...]
    local wt="$1"; shift
    ( cd "$wt" && env CLAUDE_PROJECT_DIR="$wt" \
        LOOP_TEST_CMD="echo SUITE-RAN-MARKER" \
        LOOP_LINT_CMD="true" \
        LOOP_TYPECHECK_CMD="" \
        LOOP_BUILD_CMD="" \
        "$@" bash "$GATE" </dev/null 2>&1 )
}

ran()     { grep -q 'SUITE-RAN-MARKER' <<<"$1"; }
skipped() { grep -q 'SKIP: no changed code files' <<<"$1"; }
noted()   { grep -q 'NOTE: no merge base' <<<"$1"; }

echo "== branch shapes that must GATE"

# --- 1. control: a plain named feature branch, change committed, tree clean ---
# This one already worked. It is pinned so the fix for the others cannot break
# the ordinary path — a fix that breaks the common case is a regression wearing
# a fix's name.
wt="$(mk_clone named main)"
git_q -C "$wt" checkout -q -b fix/thing
printf 'print("changed")\n' > "$wt/app.py"
git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "change"
out="$(run_gate "$wt")"
ran "$out" && ok "named feature branch, committed change, clean tree" \
           || bad "named feature branch did not run the suite: $(tail -3 <<<"$out")"

# --- 2. detached HEAD -------------------------------------------------------
wt="$(mk_clone detached main)"
git_q -C "$wt" checkout -q -b fix/thing
printf 'print("changed")\n' > "$wt/app.py"
git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "change"
git_q -C "$wt" checkout -q --detach
out="$(run_gate "$wt")"
ran "$out" && ok "detached HEAD (review worktree / CI PR checkout)" \
           || bad "detached HEAD reverted to working-tree-only: $(tail -3 <<<"$out")"

# --- 3. a default branch that is not called main or master ------------------
# `production` is in this list on purpose: it is outside the gate's documented
# fallback name list, so only origin/HEAD can resolve it. Without it, the
# fallback alone passes every case here and the name-independent lookup is
# never actually exercised — a pin that agrees with a guess is not evidence.
for trunk in develop trunk production; do
    wt="$(mk_clone "dflt-$trunk" "$trunk")"
    git_q -C "$wt" checkout -q -b fix/thing
    printf 'print("changed")\n' > "$wt/app.py"
    git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "change"
    out="$(run_gate "$wt")"
    ran "$out" && ok "default branch named '$trunk'" \
               || bad "default branch '$trunk' skipped: $(tail -3 <<<"$out")"
done

# --- 4. no local trunk ref at all (the ordinary CI checkout) ----------------
wt="$(mk_clone nolocal main)"
git_q -C "$wt" checkout -q -b fix/thing
printf 'print("changed")\n' > "$wt/app.py"
git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "change"
git_q -C "$wt" branch -q -D main
out="$(run_gate "$wt")"
ran "$out" && ok "no local 'main' ref, only origin/*" \
           || bad "CI-shaped checkout skipped: $(tail -3 <<<"$out")"

# --- 4b. the remote is not called `origin` (a fork checkout) ---------------
# `upstream/main` is right there, but the gate probed a hard-coded `origin` in
# both the symbolic-ref lookup and the fallback list, fell back to the working
# tree, and told the reader "no trunk-shaped ref in this clone" — which was
# false. Same class as the branch-name guess this pin exists for, one field
# over: a question with a wrong answer in an ordinary checkout.
wt="$(mk_clone upstreamed main)"
git_q -C "$wt" remote rename origin upstream >/dev/null 2>&1
git_q -C "$wt" checkout -q -b fix/thing
printf 'print("changed")\n' > "$wt/app.py"
git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "change"
git_q -C "$wt" branch -q -D main >/dev/null 2>&1 || true
out="$(run_gate "$wt")"
ran "$out" && ok "remote named 'upstream', no local trunk (fork checkout)" \
           || bad "remote named 'upstream' reverted to working-tree-only: $(tail -3 <<<"$out")"

# --- 5. on the default branch, with a commit of its own --------------------
# A base IS available here (HEAD~1). Falling back to the working tree on the
# trunk was a choice, not a necessity, and it left every trunk commit unread.
wt="$(mk_clone ontrunk main)"
printf 'print("changed")\n' > "$wt/app.py"
git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "change"
out="$(run_gate "$wt")"
ran "$out" && ok "on the default branch, committed change (base = HEAD~1)" \
           || bad "trunk commit not covered: $(tail -3 <<<"$out")"

echo "== shapes with genuinely no base: loud, and still gating the tree"

# --- 6. orphan branch, clean tree ------------------------------------------
# There is no honest base. That must be SAID, every time, not swallowed.
wt="$(mk_clone orphan main)"
git_q -C "$wt" checkout -q --orphan orph
git_q -C "$wt" rm -rq --cached . >/dev/null 2>&1 || true
printf 'print("orphan")\n' > "$wt/app.py"
rm -f "$wt/other.py"
git_q -C "$wt" add app.py; git_q -C "$wt" commit -q -m "orphan root"
out="$(run_gate "$wt")"
noted "$out" && ok "orphan branch says out loud that it has no merge base" \
             || bad "orphan branch fell back silently: $(tail -5 <<<"$out")"

# --- 7. no base AND a dirty tree -------------------------------------------
# The gate runs, so it looks covered — and the committed work is not. This is
# the silent fallback the fix promised to remove, and it printed nothing.
printf 'print("dirty")\n' > "$wt/dirty.py"
out="$(run_gate "$wt")"
ran "$out"   && ok "no base + dirty tree still runs the suite" \
             || bad "no base + dirty tree did not run: $(tail -3 <<<"$out")"
noted "$out" && ok "no base + dirty tree still prints the NOTE" \
             || bad "no base + dirty tree ran with NO note — silent fallback: $(tail -5 <<<"$out")"

# --- 8. LOOP_FORCE_GATE=1 must not suppress the note -----------------------
out="$(run_gate "$wt" LOOP_FORCE_GATE=1)"
noted "$out" && ok "LOOP_FORCE_GATE=1 still prints the NOTE" \
             || bad "LOOP_FORCE_GATE=1 hid the note: $(tail -5 <<<"$out")"

echo "== the note must diagnose the shape it is actually in"
# "on the default branch there is no merge base" was printed while detached, on
# develop, and on a CI checkout. A message that names the one situation you are
# not in is worse than no message.
grep -q 'on the default branch there is no merge base' "$GATE" \
    && bad "the gate still hard-codes 'on the default branch' as the reason" \
    || ok "the note no longer asserts one fixed reason"
grep -q 'orphan\|no history\|shares no' <<<"$out" \
    && ok "the note names the shape (orphan / unrelated history)" \
    || bad "the note does not say which situation obtains: $(grep NOTE -A2 <<<"$out")"

echo "== control: a branch with nothing changed must still SKIP"
# The cheapest wrong fix is "always run". It would pass every check above and
# make the gate useless on conversational turns, so it is pinned against.
wt="$(mk_clone unchanged main)"
git_q -C "$wt" checkout -q -b fix/nothing
out="$(run_gate "$wt")"
skipped "$out" && ok "unchanged branch still short-circuits" \
               || bad "gate now runs on a no-op turn: $(tail -3 <<<"$out")"

echo
if [ "$fails" = 0 ]; then echo "BRANCH SHAPES: ALL PASS"; else echo "BRANCH SHAPES: $fails FAILURE(S)"; fi
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
