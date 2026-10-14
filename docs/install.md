# Initial installation

A kiosk is installed once: the BIOS is configured, the image is written to its
disk, Secure Boot is locked to the image key at the first boot, then a first
signature set is installed from a USB key. Afterwards it is only updated with
signed USB keys or online ([Maintenance](update.md)).

## In short

1. Build the image: [Building the image](build.md).
2. [Configure the BIOS](#bios-configuration): supervisor password, Secure
   Boot in setup mode, boot from the internal disk only.
3. [Write the image](#writing-the-image) to the disk of the kiosk.
4. [First boot](#first-boot): the image enrolls its Secure Boot key; check
   that Secure Boot is in **User** mode.
5. [Insert a first signature set](#first-signature-set): until then, the
   kiosk shows `NO VALID SIGNATURES` and scans nothing.
6. [Check the kiosk](#checks) with a test key and a maintenance device.

The settings below are those of the validated hardware, a Lenovo ThinkCentre
M720q Tiny (see [Hardware](hardware.md)).

## BIOS configuration

The settings of the ThinkCentre M720q (press **F1** at power on). The names
are those of the Lenovo BIOS of this generation and may vary slightly with
the version; the goal of each setting matters more than its exact name.

### Security

| Setting | Value | Why |
|---|---|---|
| Security → Supervisor Password | set, kept by the operator | nobody changes the boot order, Secure Boot or the settings below |
| Security → Power-On Password | none | the kiosk starts by itself after a power failure |
| Security → Secure Boot → Secure Boot | Enabled | the firmware only starts the signed boot loader of the image |
| Security → Secure Boot → Secure Boot Mode | Custom, then **Reset to Setup Mode** (or Clear All Secure Boot Keys) | at its first boot, the image enrolls its own certificate (`loader/keys/auto`): the computer then only starts PandoraBox images |
| Security → Device Guard / Intel TXT | default | not used |
| Security → Chassis Intrusion Detection | Enabled, if the enclosure has the switch | an opened kiosk is reported at the next start |

Enrolling only the image certificate also refuses the option ROMs signed by
Microsoft: fine with the integrated Intel graphics of the M720q, but a
computer that needs an add-in graphics card or disk controller would not
start it.

### Startup

| Setting | Value | Why |
|---|---|---|
| Startup → Boot Mode | UEFI only | the image only boots in UEFI mode |
| Startup → Primary Boot Sequence | the internal SSD only | the kiosk never starts from a USB key inserted by a user |
| Startup → USB Boot | Disabled | same |
| Startup → PXE / Network Boot | Disabled | no start from the network |
| Startup → Boot Menu (F12) | Disabled, if offered | no other boot device chosen at power on (the supervisor password protects it otherwise) |
| Startup → Fast Boot | default | |

### Devices and advanced settings

| Setting | Value | Why |
|---|---|---|
| Advanced → CPU Setup → Intel Virtualization Technology for Directed I/O (VT-d) | Enabled | the IOMMU protects the memory against DMA from devices (the kernel uses it in strict mode) |
| Devices → Network Setup → Wake on LAN | Disabled | |
| Devices → USB Setup | the ports not used by the users or the touchscreen disabled, if the BIOS offers it per port | fewer ways in (physical hardening) |
| Devices → Audio / Serial / Card reader | Disabled when present | not used |

### Power

| Setting | Value | Why |
|---|---|---|
| Power → After Power Loss | Power On | the kiosk starts again after a power failure |

## Writing the image

The SSD is written once, from another computer (in a USB enclosure), with
the build container:

```sh
lsblk -o NAME,SIZE,MODEL,TRAN          # the disk of the SSD, e.g. /dev/sdX
docker run --rm --device /dev/sdX -v "$PWD/image/mkosi.output:/o:ro" \
    pandora-box-builder dd if=/o/pandora-box.raw of=/dev/sdX bs=4M conv=fsync status=progress
```

> [!CAUTION]
> Check the disk name twice: `dd` overwrites the whole disk.

The image can be written to a disk of any size: at every boot, the data
partition (`/var`) grows to fill the disk.

## First boot

Put the SSD back in the kiosk and start it. With the firmware in Secure Boot
setup mode, systemd-boot enrolls the certificate of the image in place of the
Microsoft keys (PK, KEK and db, from `loader/keys/auto/` on the ESP), then the
kiosk starts. From then on, the computer only starts images signed with the
same key.

After this first boot, check in the BIOS that Secure Boot is on: it shows the
mode **User**. To give the computer back to another use: Security → Secure
Boot → **Restore Factory Keys**.

The boot loader has no menu and does not read the keyboard: there is no way
to choose another system or to open a session on the kiosk.

## First signature set

A new kiosk has no signature set: it shows `NO VALID SIGNATURES` and only
accepts a signature update device. Prepare one on the computer that holds
the update key, and insert it (see [Signatures](signatures.md#updating-a-kiosk)):

```sh
export ABUSECH_AUTH_KEY=...
publish/publish.sh /srv/pandora-box/pandora-box-signatures image/update.key
cp -r /srv/pandora-box/pandora-box-signatures /media/$USER/KEY/
```

The kiosk verifies and installs the set, loads its engines, asks to remove
the key, then waits for a key to scan. A kiosk built with the profile
`online` and connected to the network also downloads its signatures itself,
5 minutes after boot ([Online updates](signatures.md#online-updates)).

## Checks

- Insert a key holding the [EICAR test file](https://www.eicar.org/download-anti-malware-testfile/):
  the kiosk reports it, and removes it after a touch of the screen.
- The touchscreen confirms a cleaning. If it does not, USBGuard blocks it: its
  rule is tied to the serial number and port of the touch controller
  ([USB devices](image.md#usb-devices)).
- Bring the logs back with a [maintenance device](logs.md#maintenance-device):
  `failed-services.txt` is empty, `signatures.txt` shows the installed set.
