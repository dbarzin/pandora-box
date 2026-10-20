#!/bin/sh
# Copy to a real server (airgap): an SSH server (SFTP, rsync over SSH, rsync
# restricted by rrsync) in a container, then the tests of tests/airgap in a
# runner container. Needs Docker with Compose.
#   tests/airgap/run.sh [pytest options]
set -eu

cd "$(dirname "$0")"
compose() { docker compose -p pandora-box-airgap -f compose.yml "$@"; }
trap 'compose down -v --remove-orphans >/dev/null 2>&1' EXIT
compose build -q
compose run --rm -T keygen
compose up -d sshd
# The SSH server writes known_hosts once its host keys exist
for _ in $(seq 30); do
    if compose exec -T sshd test -s /secrets/known_hosts; then break; fi
    sleep 1
done
compose run --rm -T runner /venv/bin/pytest -m airgap -v tests/airgap "$@"
