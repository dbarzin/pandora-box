#!/bin/sh
# Pin the runtime dependencies installed in the image, with their hashes
# (image/requirements.txt), using the Python of Debian 13 (build container).
set -eu

cd "$(dirname "$0")"
docker build -q -t pandora-box-builder . >/dev/null
docker run --rm -v "$PWD/..:/src:ro" pandora-box-builder sh -c '
    python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -q pip-tools
    cd /tmp && cp /src/pyproject.toml /src/README.md /src/LICENSE . && mkdir -p src/pandora_box
    /tmp/venv/bin/pip-compile -q --generate-hashes --strip-extras --allow-unsafe \
        --no-header -o - pyproject.toml' > requirements.txt.new
{
    echo "# Runtime dependencies installed in the image, pinned with their hashes."
    echo "# Regenerate after a change of the dependencies in pyproject.toml:"
    echo "#   image/lock-requirements.sh"
    cat requirements.txt.new
} > requirements.txt
rm requirements.txt.new
