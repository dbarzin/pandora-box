#!/bin/sh
# Build a signed image update (A/B, docs/update.md) from a built image, in the
# build container:
#   image/package-update.sh IMAGE OUTPUT KEY
# IMAGE: disk image built by image/build.sh (with its split
# partitions and UKI next to it). OUTPUT: folder of the update (replaced), to
# copy as pandora-box-image at the root of an image update device. KEY: the
# update key (Ed25519, PEM).
set -eu

if [ $# -ne 3 ]; then
    sed -n '2,8p' "$0" >&2
    exit 2
fi
image=$(realpath --relative-to="$(dirname "$0")/.." "$1")
output=$(realpath -m --relative-to="$(dirname "$0")/.." "$2")
key=$(realpath --relative-to="$(dirname "$0")/.." "$3")
cd "$(dirname "$0")/.."
docker build -q -t pandora-box-builder image >/dev/null
exec docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/src" -w /src \
    -e PYTHONPATH=/src/src -e PYTHONDONTWRITEBYTECODE=1 pandora-box-builder \
    python3 -m pandora_box.imageupdate package "$image" "$output" --key "$key"
