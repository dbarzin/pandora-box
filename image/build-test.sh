#!/bin/sh
# Build the images of the virtual machine test (image/vm.sh test):
#   image/mkosi.output/pandora-box-test.raw   the test image
#   image/mkosi.output/updates/2, 3, 4        signed image updates to three
#                                             newer versions (A/B update test)
#   image/mkosi.output/updates/versions       the four versions, in order
# The versions are timestamps, as for a release (YYYYMMDDHHMMSS: the longest
# partition labels). Arguments are passed to image/build.sh.
set -eu

cd "$(dirname "$0")"
# Keys of the copy to a server (airgap) of the test image: the SFTP server of
# the test (image/vm/sftp_server.py) presents the host key and accepts the
# client key; the image trusts the host key and signs the key identifiers
if [ ! -f vm/airgap/token.key ]; then
    rm -rf vm/airgap
    mkdir -p vm/airgap
    ssh-keygen -q -t ed25519 -N "" -C airgap-test-host -f vm/airgap/host
    ssh-keygen -q -t ed25519 -N "" -C airgap-test-kiosk -f vm/airgap/client
    printf '[10.0.2.2]:2222 %s\n' "$(cut -d' ' -f1,2 vm/airgap/host.pub)" >vm/airgap/known_hosts
    openssl genpkey -algorithm ed25519 -out vm/airgap/token.key
fi
base=$(date -u +%Y%m%d%H%M%S)
./build.sh --profile test --image-version="$base" "$@"
rm -rf mkosi.output/updates
mkdir -p mkosi.output/updates
versions=$base
for step in 2 3 4; do
    version=$((base + step - 1))
    versions="$versions $version"
    ./build.sh --profile test --image-version="$version" \
        --output-directory="mkosi.output/v$step" "$@"
    ./package-update.sh "mkosi.output/v$step/pandora-box-test.raw" \
        "mkosi.output/updates/$step" update.key
done
echo "$versions" >mkosi.output/updates/versions
