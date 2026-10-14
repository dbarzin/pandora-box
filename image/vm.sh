#!/bin/sh
# Run a PandoraBox image in a QEMU/KVM virtual machine, in the build container.
#   image/vm.sh test [--workdir DIR]          automated end-to-end test of the test image
#   image/vm.sh run [IMAGE] [--usb DEVICE]    interactive machine (default: the test image)
#   image/vm.sh usb                           USB storage devices of this computer
# --usb passes a USB device of this computer through to the machine, when it
# is plugged in: VENDOR:PRODUCT or BUS-PORT, as listed by "image/vm.sh usb".
# Build the test image first: image/build.sh --profile test
set -eu

cd "$(dirname "$0")/.."

# USB storage devices: port (BUS-PORT), VENDOR:PRODUCT, name, and the host
# block devices and mount points of each
list_usb() {
    found=""
    for device in /sys/bus/usb/devices/[0-9]*-*; do
        case "$device" in *:*) continue ;; esac
        grep -qx 08 "$device"/*:*/bInterfaceClass 2>/dev/null || continue
        found=yes
        name="$(cat "$device/manufacturer" "$device/product" 2>/dev/null | tr -s ' \n' ' ')"
        echo "$(basename "$device")  $(cat "$device/idVendor"):$(cat "$device/idProduct")  $name"
        for block in "$device"/*/host*/target*/*/block/*; do
            [ -e "$block" ] || continue
            lsblk -n -o NAME,SIZE,FSTYPE,MOUNTPOINTS "/dev/$(basename "$block")" | sed 's/^/    /'
        done
    done
    [ -n "$found" ] || echo "No USB storage device plugged in."
}

command="${1:-}"
[ $# -gt 0 ] && shift
case "$command" in
usb)
    list_usb
    exit 0
    ;;
test | run) ;;
*)
    sed -n '2,8p' "$0" >&2
    exit 2
    ;;
esac

docker build -q -t pandora-box-builder image >/dev/null
# Run as the calling user, so that the files written in the repository
# (work folder) belong to them
user="--user $(id -u):$(id -g) -e PYTHONDONTWRITEBYTECODE=1"
# KVM when available, otherwise QEMU emulation (much slower)
kvm=""
if [ -w /dev/kvm ]; then
    kvm="--device /dev/kvm --group-add $(stat -c %g /dev/kvm)"
fi

if [ "$command" = test ]; then
    # shellcheck disable=SC2086
    exec docker run --rm $user $kvm -v "$PWD:/src" -w /src \
        pandora-box-builder python3 image/vm/run_tests.py "$@"
fi

passthrough=""
for arg in "$@"; do
    case "$arg" in --usb | --usb=*) passthrough=yes ;; esac
done
if [ -z "$passthrough" ]; then
    # shellcheck disable=SC2086
    exec docker run --rm -it $user $kvm -v "$PWD:/src" -w /src -p 127.0.0.1:5900:5900 \
        pandora-box-builder python3 image/vm/machine.py run "$@"
fi

# USB passthrough: QEMU opens the host USB devices (root, all devices) and
# attaches them when they are plugged in, which needs the devices created
# later (/dev/bus/usb) and the udev events of the host (host network). VNC
# then listens on localhost of this computer.
echo "WARNING: a device passed through is taken from this computer while it runs."
echo "Unmount it first, and disable the automatic mounting of USB devices."
# shellcheck disable=SC2086
exec docker run --rm -it --privileged --network host $kvm \
    -v /dev/bus/usb:/dev/bus/usb -v /run/udev:/run/udev:ro \
    -v "$PWD:/src" -w /src -e PYTHONDONTWRITEBYTECODE=1 -e HOST_IDS="$(id -u):$(id -g)" \
    pandora-box-builder sh -c '
        status=0
        python3 image/vm/machine.py run --vnc-listen 127.0.0.1 "$@" || status=$?
        chown -R "$HOST_IDS" image/vm/work
        exit $status' \
    sh "$@"
