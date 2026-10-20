#!/bin/sh
# Copy to a Windows server (airgap), in a QEMU virtual machine of the build
# container (docs/airgap.md, "Windows server in a virtual machine"):
#   tests/windows/windows.sh prepare ISO [--index N]   install Windows (once)
#   tests/windows/windows.sh test                      copy keys to it
#   tests/windows/windows.sh logs                      logs of sshd and of the
#                                                      setup, from the disk
# ISO: Windows Server 2025 evaluation, from the Microsoft Evaluation Center.
# The installation can be watched over VNC on localhost:5900.
set -eu

[ $# -ge 1 ] || { sed -n '2,10p' "$0" >&2; exit 2; }
cd "$(dirname "$0")/../.."
docker build -q -t pandora-box-builder image >/dev/null
user="--user $(id -u):$(id -g) -e PYTHONDONTWRITEBYTECODE=1"
kvm=""
if [ -w /dev/kvm ]; then
    kvm="--device /dev/kvm --group-add $(stat -c %g /dev/kvm)"
fi
if [ "$1" = logs ]; then
    # Read from the disk of the last test (or the installed one), with the
    # NTFS tools: a loop device, hence a privileged container
    exec docker run --rm --privileged -v "$PWD/tests/windows/work:/work:ro" \
        pandora-box-builder sh -c '
        set -eu
        disk=/work/test.qcow2
        [ -f "$disk" ] || disk=/work/windows.qcow2
        qemu-img convert -O raw "$disk" /tmp/disk.raw
        start=$(sfdisk -J /tmp/disk.raw | python3 -c "
import json, sys
parts = json.load(sys.stdin)[\"partitiontable\"][\"partitions\"]
print(max(parts, key=lambda p: p[\"size\"])[\"start\"] * 512)")
        dev=$(losetup -f)
        [ -e "$dev" ] || mknod "$dev" b 7 "${dev#/dev/loop}"
        losetup -r -o "$start" "$dev" /tmp/disk.raw
        mkdir -p /mnt/windows
        ntfs-3g -o ro "$dev" /mnt/windows
        echo "=== C:\\pandora-box-setup.log ($disk)"
        tr -d "\r" </mnt/windows/pandora-box-setup.log | grep -v "^\s*\$" || true
        echo "=== C:\\ProgramData\\ssh\\logs\\sshd.log (last lines)"
        tr -d "\r" </mnt/windows/ProgramData/ssh/logs/sshd.log 2>/dev/null | tail -80 || true
        umount /mnt/windows
        losetup -d "$dev"'
fi
# The key of the kiosks, accepted by the account of the server (ssh-keygen
# needs a user name, which the container does not have)
mkdir -p tests/windows/work
if [ ! -f tests/windows/work/kiosk ]; then
    ssh-keygen -q -t ed25519 -N "" -C pandora-box-kiosk -f tests/windows/work/kiosk
fi
iso=""
if [ "$1" = prepare ]; then
    [ $# -ge 2 ] || { echo "usage: $0 prepare ISO [--index N]" >&2; exit 2; }
    iso="-v $(realpath "$2"):/windows.iso:ro"
    shift 2
    set -- prepare /windows.iso "$@"
fi
# shellcheck disable=SC2086
exec docker run --rm $user $kvm $iso -v "$PWD:/src" -w /src -p 127.0.0.1:5900:5900 \
    pandora-box-builder python3 tests/windows/windows.py "$@"
