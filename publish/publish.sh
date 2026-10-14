#!/bin/sh
# Publish a signed signature set from the sources of the day, in a container.
#   publish/publish.sh OUTPUT KEY [--sources ...] [--serial N]
# OUTPUT: folder of the set (replaced), to copy as pandora-box-signatures at
# the root of a signature update device. KEY: Ed25519 private key (PEM).
# MalwareBazaar needs ABUSECH_AUTH_KEY. Downloads are cached in
# ~/.cache/pandora-box-signatures: unchanged sources are not downloaded again.
set -eu

if [ $# -lt 2 ]; then
    sed -n '2,7p' "$0" >&2
    exit 2
fi
output=$(realpath -m "$1")
key=$(realpath "$2")
shift 2
cache="${XDG_CACHE_HOME:-$HOME/.cache}/pandora-box-signatures"
mkdir -p "$cache" "$(dirname "$output")"

cd "$(dirname "$0")/.."
docker build -q -t pandora-box-publish -f publish/Dockerfile . >/dev/null
exec docker run --rm --user "$(id -u):$(id -g)" -e HOME=/cache \
    -e ABUSECH_AUTH_KEY \
    -v "$cache:/cache" -v "$(dirname "$output"):/out" -v "$key:/key.pem:ro" \
    pandora-box-publish "/out/$(basename "$output")" --key /key.pem --cache /cache "$@"
