# Image build and test environment: mkosi, QEMU/KVM and OVMF from Debian 13
# (trixie), so that the developer only needs Docker. Used by image/build.sh
# and image/vm.sh. The PKCS#11 tools sign a release with the keys of a
# YubiKey (image/release.sh); SoftHSM and OpenSC test it without one.
FROM debian:trixie-slim

# Debian packages of snapshot.debian.org at that time, YYYYMMDDTHHMMSSZ: the
# tools of a reproducible build (image/build.sh --snapshot)
ARG SNAPSHOT=
RUN if [ -n "$SNAPSHOT" ]; then \
        for archive in "debian trixie trixie-updates" "debian-security trixie-security"; do \
            set -- $archive; name=$1; shift; \
            printf '%s\n' "Types: deb" \
                "URIs: http://snapshot.debian.org/archive/$name/$SNAPSHOT/" \
                "Suites: $*" "Components: main" \
                "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg" \
                "Check-Valid-Until: no" ""; \
        done >/etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        mkosi systemd-container systemd-ukify systemd-boot-efi systemd-repart \
        apt debian-archive-keyring dpkg-dev \
        dosfstools e2fsprogs erofs-utils exfatprogs ntfs-3g mtools cpio zstd \
        python3 python3-venv python3-pip git ca-certificates \
        qemu-system-x86 qemu-utils ovmf swtpm swtpm-tools \
        openssl sbsigntool python3-virt-firmware \
        python3-paramiko openssh-client \
        pkcs11-provider p11-kit p11-kit-modules ykcs11 yubico-piv-tool pcscd libccid \
        softhsm2 opensc \
    && rm -rf /var/lib/apt/lists/* \
    # p11-kit loads the PKCS#11 module of the token only (image/pkcs11.sh)
    && rm -f /usr/share/p11-kit/modules/*.module

# Reproducible microcode initrd (image/patch-mkosi.py)
COPY patch-mkosi.py /tmp/
RUN python3 /tmp/patch-mkosi.py && rm /tmp/patch-mkosi.py
