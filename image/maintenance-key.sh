#!/bin/sh
# Prepare a maintenance device: the kiosk writes its logs to it (journal,
# kiosk, USBGuard, services, hardware), then ejects it; nothing changes on
# the kiosk (docs/image.md).
#   image/maintenance-key.sh DEVICE [KIOSK]
# DEVICE: the mounted USB key (vfat, exfat, NTFS or ext4). KIOSK: only the
# kiosk of that name (kiosk.name), default any. The request is signed with
# image/update.key and valid 7 days.
set -eu

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    sed -n '2,8p' "$0" >&2
    exit 2
fi
device=$(realpath "$1")
cd "$(dirname "$0")/.."
docker build -q -t pandora-box-builder image >/dev/null
exec docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/src" -v "$device:/device" -w /src \
    -e PYTHONPATH=/src/src -e PYTHONDONTWRITEBYTECODE=1 pandora-box-builder \
    python3 -m pandora_box.sigsets maintenance /device/pandora-box-maintenance \
    --key image/update.key --kiosk "${2:-}"
