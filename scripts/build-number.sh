#!/bin/sh
# Commits since the most recent release tag: the build number an image is
# stamped with. Prints nothing when it cannot answer honestly, and the
# application then calls itself a source build rather than naming a build it
# does not have. That covers no repository, no history, and a shallow clone,
# where a count is a count of the fetch depth rather than of the release.
set -eu

git_dir=$(git rev-parse --git-dir 2>/dev/null) || exit 0
# The file, not `--is-shallow-repository`: an older git does not know that
# flag, echoes it back and exits 0, so the check would pass and the wrong
# number would ship.
[ -f "$git_dir/shallow" ] && exit 0

# Releases only. A pre-release or a nightly left on a branch would otherwise
# restart the count, so the number would claim one commit since a release
# that was months ago.
tag=$(git describe --tags --abbrev=0 \
        --match 'v[0-9]*.[0-9]*.[0-9]*' --exclude '*-*' 2>/dev/null || true)
# With no tag the range would collapse to "..HEAD", which git reads as
# HEAD..HEAD and counts as zero while exiting happily, so this branches
# rather than falling back after a failure that never comes.
if [ -n "$tag" ]; then
  git rev-list --count "$tag..HEAD" 2>/dev/null || true
else
  git rev-list --count HEAD 2>/dev/null || true
fi
