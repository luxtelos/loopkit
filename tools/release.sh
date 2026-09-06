#!/usr/bin/env bash
# release.sh — one version, one tag, one GitHub release, notes from CHANGELOG.md.
#
#   tools/release.sh <version> [--target <sha|ref>] [--dry-run]
#
# Reads the "## <version> — …" section of CHANGELOG.md as the release notes,
# creates an annotated tag v<version> at --target (default: the current HEAD),
# pushes the tag, and creates the GitHub release with `gh`. A version
# containing "-" (alpha, beta, rc) is marked a pre-release. Refuses to run on
# a dirty tree, to re-tag an existing tag, or when the section is missing —
# a release without notes is a number, not a release. Never merges anything.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
VERSION="${1:-}"; [ -n "$VERSION" ] || { echo "usage: tools/release.sh <version> [--target <ref>] [--dry-run]" >&2; exit 2; }
shift
TARGET="HEAD"; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
TAG="v$VERSION"
REMOTE_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || git remote get-url origin | sed -E 's#.*[:/]([^/]+/[^/]+?)(\.git)?$#\1#')"

# notes = the changelog section for this version, without its heading
NOTES="$(awk -v v="$VERSION" '
  /^## / { if (found) exit; if (index($0, "## " v " ") == 1 || $0 == "## " v) { found=1; next } }
  found { print }
' CHANGELOG.md)"
[ -n "$(printf '%s' "$NOTES" | tr -d '[:space:]')" ] || { echo "FAIL: no '## $VERSION' section in CHANGELOG.md — write the notes first" >&2; exit 3; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "FAIL: tracked files are modified; commit or set them aside first" >&2; exit 4; }
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "FAIL: tag $TAG already exists; a release is never re-pointed" >&2; exit 5; }

# The manifests are what an installed copy compares against. Claude Code caches
# a plugin per VERSION STRING (~/.claude/plugins/cache/<market>/<plugin>/<ver>),
# so a release whose manifest still carries the previous version reaches nobody:
# the cache directory already exists and the old copy keeps being used. Refuse.
for manifest in plugins/loopkit/.claude-plugin/plugin.json .claude-plugin/marketplace.json; do
  [ -f "$manifest" ] || continue
  mver="$(python3 -c 'import json,sys,re; t=open(sys.argv[1]).read(); m=re.search(r"\"version\"\s*:\s*\"([^\"]+)\"", t); print(m.group(1) if m else "")' "$manifest")"
  [ "$mver" = "$VERSION" ] || { echo "FAIL: $manifest says version $mver, releasing $VERSION — bump the manifest or nobody receives this release" >&2; exit 6; }
done
SHA="$(git rev-parse --verify "$TARGET^{commit}")"
PRE=""; case "$VERSION" in *-*) PRE="--prerelease" ;; esac

echo "RELEASE: $TAG at $SHA ($(git log -1 --format=%s "$SHA" | cut -c1-60))"
echo "NOTES ($(printf '%s\n' "$NOTES" | grep -c .) lines):"; printf '%s\n' "$NOTES" | head -6 | sed 's/^/  /'
[ "$DRY" = 1 ] && { echo "dry run — no tag, no release"; exit 0; }

git tag -a "$TAG" "$SHA" -m "LoopKit $VERSION"
# A bare push needs a working agent or a credential helper. When neither is
# there, fall back to gh's token — interpolated inline, never written down, and
# the output is filtered so a URL cannot leak into a log.
if ! git push -q origin "refs/tags/$TAG" 2>/dev/null; then
  if command -v gh >/dev/null 2>&1 && gh auth token >/dev/null 2>&1; then
    echo "note: plain push failed (no agent or helper); using the gh token" >&2
    git push -q "https://x-access-token:$(gh auth token)@github.com/${REMOTE_REPO}.git" \
      "refs/tags/$TAG" 2>&1 | grep -v x-access-token
    [ "${PIPESTATUS[0]}" = 0 ] || { echo "FAIL: could not push $TAG by either route" >&2; exit 7; }
  else
    echo "FAIL: could not push $TAG and gh is not authenticated" >&2; exit 7
  fi
fi
printf '%s\n' "$NOTES" | gh release create "$TAG" --repo "$REMOTE_REPO" --target "$SHA" --title "LoopKit $VERSION" $PRE --notes-file -
echo "RELEASED: https://github.com/$REMOTE_REPO/releases/tag/$TAG"
