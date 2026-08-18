#!/usr/bin/env bash
#
# Publish the paramount ArthurKeen/main to the arango-solutions mirror at a milestone.
#
# ArthurKeen/main is the authoritative source of truth for this repo; the
# arango-solutions copy is a strictly-downstream mirror the solutions team clones.
# Everyday work lands via PRs into ArthurKeen (protected). Run this ONLY when
# cutting a milestone the solutions team should see as a stable update.
#
# This never writes to ArthurKeen — it republishes ArthurKeen/main's exact commit
# to the mirror. It refuses if the mirror has diverged (commits not in ArthurKeen),
# which is the "mirror ahead of paramount" footgun: a plain dual-push is two
# non-atomic pushes, and the protected primary can reject while the mirror accepts.
#
# Usage: make milestone-push   (or: bash scripts/milestone-push.sh)
set -euo pipefail

ORIGIN_REMOTE=origin              # ArthurKeen (paramount)
MIRROR_REMOTE=arango-solutions    # downstream mirror

if ! git remote get-url "$MIRROR_REMOTE" >/dev/null 2>&1; then
  echo "milestone-push: no '$MIRROR_REMOTE' remote configured. Add it once:" >&2
  echo "  git remote add $MIRROR_REMOTE https://github.com/arango-solutions/contextual-data-fabric.git" >&2
  exit 2
fi

git fetch --quiet "$ORIGIN_REMOTE" main
git fetch --quiet "$MIRROR_REMOTE" main 2>/dev/null || true

paramount=$(git rev-parse "$ORIGIN_REMOTE/main")
mirror=$(git rev-parse "$MIRROR_REMOTE/main" 2>/dev/null || echo "")

if [ "$paramount" = "$mirror" ]; then
  echo "milestone-push: mirror already at ${paramount:0:12} — nothing to publish."
  exit 0
fi

# Safe only when the mirror is an ancestor of ArthurKeen/main (a clean fast-forward).
if [ -n "$mirror" ] && ! git merge-base --is-ancestor "$mirror" "$paramount"; then
  echo "milestone-push: REFUSING — $MIRROR_REMOTE/main (${mirror:0:12}) has commits NOT in" >&2
  echo "  ArthurKeen/main (${paramount:0:12}); the mirror has diverged / is ahead of the" >&2
  echo "  paramount repo. ArthurKeen is authoritative — reconcile only after review with:" >&2
  echo "    git push $MIRROR_REMOTE $paramount:refs/heads/main --force" >&2
  exit 1
fi

echo "milestone-push: publishing ArthurKeen/main (${paramount:0:12}) -> $MIRROR_REMOTE/main"
git push "$MIRROR_REMOTE" "$paramount:refs/heads/main"
echo "milestone-push: done. Solutions team now sees ${paramount:0:12}."
