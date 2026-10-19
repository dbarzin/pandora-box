#!/bin/sh
# Build a release again from its tag, and compare it with its published files
# (docs/release.md):
#   image/reproduce.sh TAG DIR
# DIR: the files of the release (gh release download TAG -D DIR), checked
# first with their signed checksums and the update key of the tag. The tag is
# built with the same inputs as the release (image/reproducible.sh), trusting
# the same update key, but signed with development keys: everything must be
# identical except the signatures, which need the release keys and hold the
# time they were made:
#   root filesystem and dm-verity hash tree   identical
#   unified kernel image                      every section identical, the
#                                             kernel without its signature;
#                                             not the signatures of the PCRs
#   software bill of materials                identical
# release/TAG-reproduce/ holds the build; the status is 0 when it matches.
set -eu

if [ $# -ne 2 ]; then
    sed -n '2,17p' "$0" >&2
    exit 2
fi
tag=$1
assets=$(realpath "$2")
cd "$(dirname "$0")/.."
if ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    echo "error: no tag $tag (git fetch --tags)" >&2
    exit 1
fi
out=release/$tag-reproduce
if [ -e "$out" ]; then
    echo "error: $out exists: remove it to build the release again" >&2
    exit 1
fi
mkdir -p "$out/src"
git archive "$tag" | tar -x -C "$out/src"
. "$out/src/image/reproducible.sh"
reproducible_options "$tag"
# shellcheck disable=SC2086  # options: several words
"$out/src/image/build.sh" $options --environment=UPDATE_PUBLIC_KEY=release/update.pem

docker run --rm -v "$PWD/$out:/reproduce" -v "$assets:/release:ro" -w /reproduce/src \
    -e TAG="$tag" -e COMMIT="$commit" -e EPOCH="$epoch" pandora-box-builder bash -c '
        set -euo pipefail
        cd /release
        openssl pkeyutl -verify -pubin -inkey /reproduce/src/image/release/update.pem -rawin \
            -in SHA256SUMS -sigfile SHA256SUMS.sig >/dev/null
        sha256sum --quiet -c SHA256SUMS
        cd /reproduce/src
        output=image/mkosi.output
        work=$(mktemp -d)
        tar -C "$work" -xf "/release/pandora-box-image-$TAG.tar"
        files=$work/pandora-box-image/files
        status=0
        compare() {
            if cmp -s "$2" "$3"; then
                echo "identical  $1"
            else
                echo "DIFFERENT  $1"
                status=1
            fi
        }
        xz -dc "$files"/*.root.raw.xz >"$work/root.raw"
        compare "root filesystem" "$work/root.raw" "$output/pandora-box.root-x86-64.raw"
        xz -dc "$files"/*.verity.raw.xz >"$work/verity.raw"
        compare "dm-verity hash tree" "$work/verity.raw" "$output/pandora-box.root-x86-64-verity.raw"
        released=$(echo "$files"/pandora-box_*.efi)
        for section in $(objdump -h "$released" | awk "\$2 ~ /^\\./ {print \$2}"); do
            case "$section" in .pcrsig | .pcrpkey) continue ;; esac
            for uki in released built; do
                file=$released
                [ "$uki" = built ] && file=$output/pandora-box.efi
                objcopy -O binary --only-section="$section" "$file" "$work/$uki$section"
                # The kernel is signed for Secure Boot: compared without
                if [ "$section" = .linux ]; then
                    sbattach --remove "$work/$uki$section" >/dev/null
                fi
            done
            compare "kernel image section $section" "$work/released$section" "$work/built$section"
        done
        python3 image/sbom.py "$output/pandora-box.manifest" "$work/sbom.json" \
            --commit "$COMMIT" --epoch "$EPOCH" >/dev/null
        compare "software bill of materials" "/release/pandora-box-$TAG.cdx.json" "$work/sbom.json"
        rm -rf "$work"
        exit $status'
echo "release $tag reproduced from its tag"
