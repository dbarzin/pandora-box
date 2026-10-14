# System updates

The system of a kiosk is read-only: it is updated as a whole, with a new
image, never file by file. The disk has two system slots (A/B): the update is
written to the free slot while the kiosk runs, and the previous version stays
until the new one has booted successfully. The configuration and the theme
are part of the image: changing them is also an update.

The signatures are updated separately, far more often: see
[Signatures](signatures.md).

## In short

```sh
image/build.sh                    # new version, same keys and profile as the kiosk
image/package-update.sh image/mkosi.output/pandora-box.raw update image/update.key
cp -r update /media/$USER/KEY/pandora-box-image
```

Insert the key into the kiosk: it installs the update and restarts on the new
version.

## Building an update

Build a new version with the same keys as the installed image (its version is
the time of the build: newer than the running one), and with the same
profile, then package it:

```sh
image/build.sh
image/package-update.sh image/mkosi.output/pandora-box.raw update image/update.key
```

`update/` holds the partitions of slot A of the new image (compressed
with xz, the format that `systemd-sysupdate` of Debian 13 decompresses),
named with their UUID, the unified kernel image, and a manifest signed with
the update key (the format of the signature sets, content `image`, serial =
version). About 215 MB, of which 40 MB for the kernel image.

Two keys protect the update: the update key decides what is installed, the
Secure Boot key decides what can boot (the kernel image is signed and holds
the root hash of its slot). An update built with other keys is refused, or
does not boot.

## Installing from a USB key

Copy `update/` as `pandora-box-image/` at the root of a USB key (vfat,
exFAT, NTFS or ext4) and insert it into the kiosk
(`updates.image_from_devices`, on by default).

The kiosk verifies the signature, that the version is newer than the running
one and every file, copies them to `/var/lib/pandora-box-image`, then
`systemd-sysupdate` writes the partitions to the free slot and the kernel
image to the ESP with 3 boot tries (`pandora-box_2+3-0.efi`), and the kiosk
restarts. The running version is never overwritten; the device is not
scanned.

One key can hold a maintenance request, a signature set and an image update:
the kiosk handles them in that order, each verified on its own; the image
update comes last as it restarts the kiosk.

## Online

With the profile `online` ([Building the image](build.md#profile-online))
and `updates.image_url`, the URL of a published `update/` folder,
`pandora-box-update.service` downloads a newer update as its unprivileged
user (every file checked), then installs it as root without network
(`pandora-box-image install`), with the same checks as an update device. The
kiosk restarts on the new version when it is idle (between two devices): a
scan is never interrupted.

## If the new version fails

The new version is kept (`systemd-bless-boot`) once `boot-complete.target`
is reached, which requires the kiosk to be ready (`Type=notify`). A boot that
fails restarts: kernel panic (`panic=10`), failure in the initrd (its
emergency shell restarts, `image/mkosi.images/initrd/`), or no
`boot-complete.target` in 15 minutes. After 3 failed tries, systemd-boot
boots the previous version again; the data (`/var`: reports, quarantine,
logs, signatures) is shared by the versions.

`boot-entries.txt` and `boots.txt` of a
[maintenance device](logs.md#maintenance-device) show the installed versions
and the boots.

## What is not updated

The ESP is not part of the image updates: systemd-boot, its settings and the
Secure Boot keys enrolled in the firmware stay as installed. Changing the
Secure Boot key needs a new [installation](install.md).
