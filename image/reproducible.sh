# Inputs of a reproducible build of a tag (docs/release.md), sourced by
# image/release.sh and image/reproduce.sh: the same tag always gives the same
# options of image/build.sh.
#   reproducible_options TAG   sets epoch, version, snapshot, seed and options
#
# The time of the tag (of its commit, for a lightweight tag) is the
# SOURCE_DATE_EPOCH of the build, the image version and the time of the Debian
# snapshot the packages come from; the partition UUIDs and the dm-verity salt
# come from a seed derived from the commit.
reproducible_options() {
    commit=$(git rev-parse "$1^{commit}")
    epoch=$(git for-each-ref --format='%(creatordate:unix)' "refs/tags/$1")
    version=$(date -u -d "@$epoch" +%Y%m%d%H%M%S)
    snapshot=$(date -u -d "@$epoch" +%Y%m%dT%H%M%SZ)
    seed=$(python3 -c 'import sys, uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, sys.argv[1]))' \
        "https://github.com/sourcentis/pandora-box/commit/$commit")
    options="--snapshot=$snapshot --image-version=$version --source-date-epoch=$epoch --seed=$seed"
}
