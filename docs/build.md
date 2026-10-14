# Building the image

The kiosk is a complete system image, built with
[mkosi](https://github.com/systemd/mkosi) from Debian 13 (trixie) packages.
Everything runs in a Debian 13 container (`image/Dockerfile`: mkosi, QEMU,
OVMF): the build computer only needs Docker.

> [!WARNING]
> The image and the signature sets are signed with development keys only.
> The production image has no signature set: the kiosk scans nothing until a
> signature update device is inserted (see [Signatures](signatures.md)).
> See the phase 2 tasks in the [roadmap](https://github.com/dbarzin/pandora-box/blob/main/ROADMAP.md#phase-2--minimal-hardened-image).

## In short

```sh
image/build.sh                     # offline kiosk
image/build.sh --profile online    # kiosk with network: online updates, SIEM, NTP
```

The image is `image/mkosi.output/pandora-box.raw`: write it to the disk of the
kiosk ([Initial installation](install.md)), or test it first in a virtual
machine ([Testing with QEMU](vm.md)).

## Requirements

Docker, about 5 GB of disk space, network access (Debian packages and Python
wheels).

## Before the build

The image is read-only: everything below is part of it, and changing it
means building a new image, then installing it as an
[update](update.md).

| What | Where |
|---|---|
| Configuration of the kiosk | `packaging/pandora-box.toml` (see [Configuration](configuration.md)) |
| Theme of the interface | `image/theme/` (see [Interface](interface.md)) |
| Mode of the screen | `video=` on the kernel command line, `image/mkosi.conf` (see [Screen](image.md#screen)) |
| Touchscreen allowed by USBGuard | `image/mkosi.extra/etc/usbguard/rules.conf` (see [USB devices](image.md#usb-devices)) |
| Disk controller of another computer | `KernelModulesInitrdInclude=` of `image/mkosi.conf` (see [Unified kernel image](image.md#unified-kernel-image)) |
| Certificate authorities of the SIEM | `image/siem-ca.pem` (see [SIEM](siem.md)) |
| abuse.ch Auth-Key, profile `online` | `image/credentials.toml` (see [Profile online](#profile-online)) |
| NTP servers, profile `online` | see [Time](#time) |

### Keys

The image is signed with `image/mkosi.key` and `image/mkosi.crt`
(systemd-boot, unified kernel image, dm-verity root hash): the firmware of a
kiosk only starts an image signed with this key. It trusts the signature
sets, image updates and maintenance requests signed with `image/update.key`:
its public key `image/update.pem` is installed in the image.

When they do not exist, `image/build.sh` generates a **development** pair of
each, valid 10 years, that never leaves the build computer (ignored by git).
Keep them: a kiosk only accepts the updates signed with the keys of its
image. A kiosk must only trust the release keys of the project, which are not
defined yet: they will be kept out of the repository, and mkosi can sign with
a key in a hardware token (`--secure-boot-key-source=provider:pkcs11`).

## Build

```sh
image/build.sh                  # production image
image/build.sh --profile online # production image with network
image/build.sh --profile test   # test image, for the virtual machine only
```

The image is written to `image/mkosi.output/`: `pandora-box.raw` (or
`pandora-box-test.raw`, a GPT disk image), the unified kernel image (`.efi`)
and the list of installed packages (`.manifest`). The first build takes a few
minutes; the following ones reuse the installed packages (mkosi incremental
build, cached in the `pandora-box-mkosi` Docker volume) and take less than a
minute.

The cache is not rebuilt when `image/requirements.txt` or
`image/mkosi.prepare.chroot` change: rebuild it with `image/build.sh -f`
(`docker volume rm pandora-box-mkosi` removes it completely).

The version of the image is the date and time of its build, UTC, like the
serial of a signature set: `20261005143700` (`--image-version=N` overrides
it). It is in `/etc/os-release` (`IMAGE_VERSION`), in the labels of its
partitions and in the name of its unified kernel image. An update must have a
newer version than the running one.

## Profile online

Without a profile, the image has no network configuration at all.
`--profile online` adds online signature updates from their sources (see
[Signatures](signatures.md#online-updates)): systemd-networkd (DHCP on the
wired network), systemd-resolved, systemd-timesyncd, freshclam, the
`pandora-box-update` timer and a firewall that only lets the update service,
the log export to a SIEM (see [SIEM](siem.md)) and NTP out
(`image/mkosi.profiles/online/`). Its `postinst.chroot` enables `[updates]`
in the configuration of the kiosk, and installs the credentials of
`image/credentials.toml` (outside git) in `/etc/credstore`.

```sh
cp image/credentials.toml.example image/credentials.toml   # then the abuse.ch Auth-Key
image/build.sh --profile online
```

> [!WARNING]
> An image built with credentials **must not be published**: anyone with the
> image or the disk of the kiosk can read them.

### Time

The clock of the kiosk dates its logs and scan reports, and decides whether
the certificates of the update servers and of the SIEM are valid. An online
kiosk synchronizes it with **systemd-timesyncd**, from the NTP servers of
`NTP=` in
`image/mkosi.profiles/online/mkosi.extra/usr/lib/systemd/timesyncd.conf.d/50-pandora-box.conf`
(empty by default), else those the DHCP server gives, else the Debian pool
(`FallbackNTP=`). To use the servers of the organization, set them there
before building the image:

```ini
[Time]
NTP=ntp1.example.org ntp2.example.org
```

Only systemd-timesyncd may send NTP requests (UDP 123) through the firewall;
`timedatectl timesync-status` shows the server in use. NTP is not
authenticated (timesyncd has no NTS): someone in the path of the requests
can shift the clock, so prefer NTP servers of the local network. Without the
profile, the kiosk keeps the time of its hardware clock (UTC).

## Dependencies

Runtime dependencies are pinned with their hashes in `image/requirements.txt`.
After a change of the dependencies in `pyproject.toml`, regenerate it with
`image/lock-requirements.sh`, then rebuild the cache (`image/build.sh -f`).

## Files

| Path | Role |
|---|---|
| `image/mkosi.conf` | Image definition: Debian 13, x86_64, systemd-boot, unified kernel image (UKI), packages |
| `image/mkosi.repart/` | Partitions: ESP, two system slots (read-only root filesystem, its dm-verity hash and signature), data partition |
| `image/mkosi.images/initrd/` | The initrd: the default initrd of mkosi, which restarts instead of opening an emergency shell, without the files a read-only root does not need |
| `image/mkosi.prepare.chroot` | Runtime dependencies in a virtual environment (`/usr/lib/pandora-box`), from `image/requirements.txt` (pinned with their hashes) |
| `image/mkosi.build.chroot` | pandora-box wheel, built from the repository without network |
| `image/mkosi.postinst.chroot` | Installs pandora-box, its configuration, systemd service, tmpfiles and logrotate files (`packaging/`), and the clamd settings |
| `image/mkosi.extra/` | Files copied into the image: systemd presets (enabled and disabled units), `/etc/fstab`, growth of the data partition (`/usr/lib/repart.d/`), A/B updates (`/usr/lib/sysupdate.d/`, boot assessment), USBGuard policy, kernel settings (`sysctl.d`), firewall (`nftables.conf`), clamd sandbox |
| `image/mkosi.profiles/online/` | Profile `online` |
| `image/mkosi.profiles/test/` | Test profile, for the virtual machine tests only |
| `image/vm/` | Virtual machine tests: corpus, QEMU driver, automated test |
| `image/build.sh`, `image/vm.sh` | Build and run in the container |
| `image/build-test.sh`, `image/package-update.sh` | Build the images of the virtual machine test; build a signed image update |
| `image/maintenance-key.sh` | Prepare a maintenance device |

What the image contains and how it is protected: see
[Image internals](image.md).
