#!/usr/bin/env python3
"""finding-sources-closed-set.py — a concept's source is one of a CLOSED set of
shapes, and an immutable artifact is anchored on the OBJECT, never on a label.

WHY. PR #47 round 2 (2026-09-09). The write path refused four queues by
`startswith` on the raw string, so any scheme prefix walked past it:
`x://state/triage.md` was accepted, as was `tag://main/state/triage.md` (a
BRANCH under the tag scheme) and a tag that did not exist. And a `tag://`
source was called immutable: the reviewer moved the tag, then deleted it, the
bytes differed, and `scan-drift` and `verify` both stayed silent.

That refusal was a deny-list: it named the known-bad and permitted the unknown.
This pin is written the other way round. THREE things are checked, and each can
fail on its own:

  1. ACCEPTED — every shape in the closed set passes through the real write
     path (`enqueue` → `drain`) and lands on disk as specified. Without this
     half, an `enqueue` that refuses everything passes the whole file.
  2. REFUSED — shapes outside the set are refused with rc=3 and NOTHING is
     queued. The list deliberately includes shapes nobody reported. Most
     refused rows cite a path that is accepted on its own (`src/mech.py`), so
     the row isolates the ONE thing wrong with it; a row that is wrong twice
     (a branch AND a queue) cannot say which check caught it.
  3. ANCHOR — a tag given at write time is resolved to its commit and the
     COMMIT is what is recorded. Then the reviewer's two attacks are replayed:
     move the tag, delete the tag. The recorded bytes must not move, and the
     label going wrong must be REPORTED. Control: an untouched tag reports
     nothing. Last, the recorded commit itself is made unresolvable, which
     must be reported too — silence there is the original defect.

Run red against the pre-fix tree:
    LOOPKIT_PLUGIN_DIR=<checkout at 4e7b8b4>/plugins/loopkit python3 <this file>
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
PLUGIN = Path(os.environ.get("LOOPKIT_PLUGIN_DIR") or HERE.parents[2] / "plugins" / "loopkit").resolve()
sys.path.insert(0, str(PLUGIN))
from loopkit_memory import okf as okf_mod  # noqa: E402

fails = 0


def ok(msg):
    print(f"  ok   {msg}")


def fail(msg):
    global fails
    fails += 1
    print(f"  FAIL {msg}")


def check(cond, msg, detail=""):
    ok(msg) if cond else fail(f"{msg}{' — ' + str(detail)[:300] if detail else ''}")


def git(root, *args, check_rc=True):
    env = dict(os.environ, GIT_AUTHOR_NAME="pin", GIT_AUTHOR_EMAIL="pin@example.invalid",
               GIT_COMMITTER_NAME="pin", GIT_COMMITTER_EMAIL="pin@example.invalid")
    p = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                       cwd=root, capture_output=True, text=True, env=env)
    if check_rc and p.returncode:
        raise SystemExit(f"setup: git {' '.join(args)} failed: {p.stderr}")
    return p.stdout.strip()


T = Path(tempfile.mkdtemp(prefix="lk-sources-")).resolve()
try:
    # ---- a scratch project: two commits, a lightweight tag, an annotated tag
    # ---- with a slash in its name, a branch, the four queues, a symlink.
    git(T, "init", "-q", "-b", "main")
    for rel, text in {
        "src/mech.py": "WANT = ('new',)\n",
        "docs/a.md": "a\n",
        "state/triage.md": "| finding |\n",
        "state/ticks.jsonl": "{}\n",
        "state/progress.md": "p\n",
        "state/2026-01-01-note.md": "a dated note\n",
        "inbox/needs-human.md": "door\n",
        "plugins/x/y.md": "y\n",
    }.items():
        (T / rel).parent.mkdir(parents=True, exist_ok=True)
        (T / rel).write_text(text, encoding="utf-8")
    os.symlink("../state/triage.md", T / "docs" / "link.md")
    git(T, "add", "--", "src", "docs", "state", "inbox", "plugins")
    git(T, "commit", "-q", "-m", "c1")
    C1 = git(T, "rev-parse", "HEAD")
    BLOB1 = git(T, "rev-parse", f"{C1}:src/mech.py")
    git(T, "tag", "v1")
    git(T, "tag", "-a", "rel/1.0", "-m", "annotated")
    (T / "src/mech.py").write_text("WANT = ('new', 'blocked')\n", encoding="utf-8")
    git(T, "add", "--", "src/mech.py")
    git(T, "commit", "-q", "-m", "c2")
    C2 = git(T, "rev-parse", "HEAD")
    # an orphan commit nothing will keep alive, for the unresolvable case
    git(T, "checkout", "-q", "--orphan", "doomed")
    git(T, "commit", "-q", "-m", "c3 doomed")
    C3 = git(T, "rev-parse", "HEAD")
    git(T, "checkout", "-q", "main")

    kn = okf_mod.OkfKnowledge(T, {})

    def queued():
        return len(list((T / "state/knowledge-mailbox").rglob("*.msg.md")))

    def enqueue(name, sources, *, in_frontmatter=False):
        fm = {"type": "Finding", "title": name, "status": "draft"}
        payload = {"frontmatter": fm}
        if in_frontmatter:
            fm["sources"] = sources
        else:
            payload["sources"] = sources
        return kn.enqueue(op="upsert", reason=f"pin {name}", by="process:pin/1",
                          target=f"/findings/{name}.md", payload=payload, body=f"claim {name}\n")

    def src(resource):
        return [{"id": "s", "resource": resource, "title": str(resource)}]

    def on_disk(name):
        p = T / "knowledge" / "findings" / f"{name}.md"
        if not p.is_file():
            return None
        fm, _ = okf_mod.okf.parse_frontmatter(p.read_text(encoding="utf-8"))
        return fm

    # ---- 1. ACCEPTED -------------------------------------------------------
    print("== accepted: every shape in the closed set passes the real write path")
    accepted = {
        "repo-file": "src/mech.py",
        "dated-note": "state/2026-01-01-note.md",
        "commit": f"commit://{C1}/src/mech.py",
        "tag-light": "tag://v1/src/mech.py",
        "tag-annotated-slash": "tag://rel/1.0/src/mech.py",
        "doomed": f"commit://{C3}/src/mech.py",
    }
    for name, resource in accepted.items():
        rc, out = enqueue(name, src(resource))
        check(rc == 0, f"accepted at enqueue: {name}  ({resource[:60]})", f"rc={rc} {out}")
    rc, out = kn.drain()
    check(rc == 0, "drain applies all of them, nothing dead-lettered", f"rc={rc} {out}")

    fm = on_disk("repo-file") or {}
    digests = fm.get("x_source_digests") or []
    check(len(digests) == 1 and str(digests[0].get("digest", "")).startswith("git-blob:"),
          "a repo file is still digested by the actor (the ordinary path is unchanged)", digests)

    want = f"commit://{C1}/src/mech.py"
    for name, label in (("commit", None), ("tag-light", "tag:v1"), ("tag-annotated-slash", "tag:rel/1.0")):
        fm = on_disk(name) or {}
        s = (fm.get("sources") or [{}])[0]
        check(s.get("resource") == want, f"{name}: the RECORDED source is the commit, {want[:24]}…", s)
        check(s.get("x_blob") == BLOB1, f"{name}: the blob id of the cited bytes is recorded", s)
        check(s.get("x_label") == label, f"{name}: label note is {label!r}", s)
        check(not [d for d in (fm.get("x_source_digests") or []) if d], f"{name}: the actor digests nothing for it", fm.get("x_source_digests"))
        check("tag://" not in json.dumps(fm), f"{name}: no tag:// string survives on disk")

    # ---- 2. REFUSED --------------------------------------------------------
    print("== refused: everything outside the set, including shapes nobody reported")
    refused = [
        # the four queues and a directory — criterion 1's original five
        ("queue: triage", "state/triage.md"),
        ("queue: ticks", "state/ticks.jsonl"),
        ("queue: progress", "state/progress.md"),
        ("queue: inbox", "inbox/needs-human.md"),
        ("a directory", "plugins/"),
        ("a directory, no slash", "plugins"),
        # the reviewer's three
        ("F  tag that does not exist", "tag://v9.9.9-never/src/mech.py"),
        ("G  branch dressed as a tag, and the queue", "tag://main/state/triage.md"),
        ("H  arbitrary scheme, and the queue", "x://state/triage.md"),
        # the same two, isolated: the path alone would be ACCEPTED
        ("branch dressed as a tag, good path", "tag://main/src/mech.py"),
        ("arbitrary scheme, good path", "x://src/mech.py"),
        # shapes nobody reported
        ("real tag, queue path", "tag://v1/state/triage.md"),
        ("real commit, queue path", f"commit://{C1}/state/triage.md"),
        ("real commit, inbox path", f"commit://{C1}/inbox/needs-human.md"),
        ("abbreviated sha", f"commit://{C1[:12]}/src/mech.py"),
        ("uppercase sha", f"commit://{C1.upper()}/src/mech.py"),
        ("sha that resolves to nothing", f"commit://{'0' * 40}/src/mech.py"),
        ("40 hex that is a BLOB, not a commit", f"commit://{BLOB1}/src/mech.py"),
        ("real commit, path not in it", f"commit://{C1}/src/nope.py"),
        ("real commit, path is a tree", f"commit://{C1}/src"),
        ("real commit, path is a symlink to the queue", f"commit://{C1}/docs/link.md"),
        ("a ref name in the commit slot", "commit://main/src/mech.py"),
        ("uppercase scheme", f"COMMIT://{C1}/src/mech.py"),
        ("https url", "https://example.invalid/mech.py"),
        ("file url", "file:///etc/hosts"),
        ("git revision syntax", "v1:src/mech.py"),
        ("absolute path", str(T / "src/mech.py")),
        ("dot-dot out of the repo", "../outside.md"),
        ("dot-dot round to the queue", "src/../state/triage.md"),
        ("dot-slash to the queue", "./state/triage.md"),
        ("dot-slash, good path", "./src/mech.py"),
        ("doubled slash", "src//mech.py"),
        ("case-dressed queue", "State/Triage.md"),
        ("symlink to the queue", "docs/link.md"),
        ("file that does not exist", "src/nope.py"),
        ("trailing space", "src/mech.py "),
        ("trailing newline", "src/mech.py\n"),
        ("empty resource", ""),
    ]
    for label, resource in refused:
        before = queued()
        rc, out = enqueue("refused-probe", src(resource))
        check(rc == 3 and "refused" in out and queued() == before,
              f"refused: {label}  ({resource[:48]!r})", f"rc={rc} queued {before}->{queued()} {out}")

    odd = [
        ("entry with no resource key", [{"id": "s"}]),
        ("resource is not a string", [{"id": "s", "resource": 7}]),
        ("entry is a list", [["src/mech.py"]]),
        ("sources is a bare dict naming the queue", {"id": "s", "resource": "state/triage.md"}),
        ("one good source beside one queue", src("src/mech.py") + src("state/triage.md")),
    ]
    for label, sources in odd:
        before = queued()
        rc, out = enqueue("refused-probe", sources)
        check(rc == 3 and queued() == before, f"refused: {label}", f"rc={rc} {out}")

    before = queued()
    rc, out = enqueue("refused-probe", src("state/triage.md"), in_frontmatter=True)
    check(rc == 3 and queued() == before,
          "refused: the queue passed as payload.frontmatter.sources (the side door apply_upsert copies from)", f"rc={rc} {out}")
    rc, out = enqueue("refused-probe", src("x://src/mech.py"), in_frontmatter=True)
    check(rc == 3, "refused: a scheme passed through the same side door", f"rc={rc} {out}")
    check(on_disk("refused-probe") is None, "no refused probe ever became a concept")

    # control for the side door: a good source through it is still accepted
    rc, out = enqueue("side-door-good", src("docs/a.md"), in_frontmatter=True)
    check(rc == 0, "control: a good source through payload.frontmatter.sources is accepted", f"rc={rc} {out}")
    kn.drain()

    # ---- 3. ANCHOR ---------------------------------------------------------
    print("== anchor: the object, not the label")
    drift = getattr(kn, "artifact_drift", None)
    if drift is None:
        fail("OkfKnowledge.artifact_drift does not exist — nothing can notice a moved label")
        drift = lambda: []  # noqa: E731

    def reports():
        return {(r["target"], f["kind"]) for r in drift() for f in r["findings"]}

    def scan_lists(target):
        rc, out = kn.scan_drift()
        try:
            return any(r.get("target") == target for r in json.loads(out))
        except Exception:
            return False

    T_TAG, T_SHA, T_DOOM = "/findings/tag-light.md", "/findings/commit.md", "/findings/doomed.md"
    base = reports()
    check(not [r for r in base if r[0] in (T_TAG, T_SHA, "/findings/tag-annotated-slash.md", T_DOOM)],
          "CONTROL: untouched tags and a resolving sha report nothing", base)
    check(not scan_lists(T_TAG), "CONTROL: scan-drift does not list the untouched tag concept")

    git(T, "tag", "-f", "v1", C2)
    moved_bytes_differ = git(T, "rev-parse", "v1:src/mech.py") != BLOB1
    check(moved_bytes_differ, "attack 1 set up: v1 now names different bytes for the same path")
    now = reports()
    check((T_TAG, "label-moved") in now, "MOVED tag: reported as label-moved", now)
    check(scan_lists(T_TAG), "MOVED tag: the concept is listed by `knowledge scan-drift`")
    check(not [r for r in now if r[0] == T_SHA], "MOVED tag: a commit-only concept is unaffected — it never named the tag", now)
    check(git(T, "rev-parse", f"{C1}:src/mech.py") == BLOB1, "MOVED tag: the RECORDED source still resolves to the recorded bytes")

    git(T, "tag", "-d", "v1")
    now = reports()
    check((T_TAG, "label-gone") in now, "DELETED tag: reported as label-gone", now)
    check(scan_lists(T_TAG), "DELETED tag: the concept is listed by `knowledge scan-drift`")
    check(not [r for r in now if r[0] == T_SHA], "DELETED tag: a commit-only concept is unaffected", now)

    git(T, "tag", "v1", C1)
    check(not [r for r in reports() if r[0] == T_TAG], "CONTROL: the tag put back where it was reports nothing again")

    git(T, "branch", "-D", "doomed")
    git(T, "reflog", "expire", "--expire=now", "--all")
    git(T, "gc", "-q", "--prune=now")
    gone = subprocess.run(["git", "cat-file", "-e", C3], cwd=T, capture_output=True).returncode != 0
    check(gone, "attack 3 set up: the recorded commit no longer resolves")
    now = reports()
    check((T_DOOM, "artifact-unresolvable") in now, "UNRESOLVABLE sha: reported, not silent", now)
    check(scan_lists(T_DOOM), "UNRESOLVABLE sha: the concept is listed by `knowledge scan-drift`")
    check(not [r for r in now if r[0] == T_SHA], "CONTROL: the sha that still resolves is still clean", now)

    # a concept already on disk with a shape outside the set (the round-2
    # bundles have them; a hand edit could make one) is loud on both doors.
    legacy = T / "knowledge" / "findings" / "legacy.md"
    legacy.write_text((T / "knowledge/findings/commit.md").read_text(encoding="utf-8")
                      .replace(f"commit://{C1}/src/mech.py", "tag://v1/src/mech.py"), encoding="utf-8")
    now = reports()
    check(("/findings/legacy.md", "source-shape-unknown") in now, "a tag:// source already ON DISK is reported", now)
    rc, out = kn.verify()
    check(rc == 4 and "legacy.md" in out, "…and `knowledge verify` fails on it (rc=4)", f"rc={rc} {out[-300:]}")
    legacy.unlink()
    rc, out = kn.verify()
    check(rc == 0, "CONTROL: with it removed, verify passes (rc=0)", f"rc={rc} {out[-300:]}")
finally:
    shutil.rmtree(T, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
