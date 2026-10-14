# Reference hardware

A kiosk is a refurbished Lenovo ThinkCentre Tiny with a 7-inch touchscreen.
The image runs on any x86_64 UEFI computer, but only the hardware below is
validated: the image sets the mode of its screen and allows its touchscreen
(see [image.md](image.md#screen)).

## Validated hardware

| Part | Model | Notes |
|---|---|---|
| Computer | Lenovo ThinkCentre M720q Tiny (machine type 10T7), BIOS M1UKT62A | Core i5-9400T (6 cores), Intel UHD Graphics 630, 8 GB, SATA SSD 128 GB |
| Screen | Waveshare 7-inch HDMI LCD (C), 1024x600, capacitive touch | HDMI for the picture, USB for the touch controller `0eef:0005` "WS170120" |

What was checked on it: Secure Boot with the image key, boot and A/B
updates, the screen (all of the interface visible), the touchscreen
(confirms a cleaning), USB keys, detection (EICAR), online updates of the
signatures over the wired network, maintenance devices.

Notes for this hardware:

- **Screen**: the firmware starts in 1024x768 and the Intel driver keeps that
  framebuffer; the kernel command line sets the mode of the screen
  (`video=1024x600M@60`) and the kiosk shrinks the console to it.
- **Touchscreen**: USBGuard allows its touch controller by its identifiers,
  serial number and port (`1-8`, where it is connected inside the
  enclosure): `image/mkosi.extra/etc/usbguard/rules.conf`. Another unit or
  another port: change the rule, as shown by `usb-devices.txt` of a
  maintenance export, or by the audit log of USBGuard when it blocks it.
- **Graphics firmware**: the image has no firmware for the Intel graphics
  (the DMC firmware of Coffee Lake): the display works, without its deepest
  power saving states.
- **Speed**: about 0.5 s per MB for a PDF full of images (ClamAV), much
  less for other files (ClamAV takes almost all of it); six files are
  scanned at the same time, one per core.

A computer or a screen not listed here may need its own settings: the mode
of its screen, the rule of its touchscreen, the disk controller in the
initrd (`KernelModulesInitrdInclude=` of `image/mkosi.conf`: NVMe, SATA,
Intel VMD, eMMC).

## Enclosure

A 3D-printed desk enclosure, one part, about 191 x 184 x 86 mm: the screen
in the window of its tilted front face, ventilation slots on its rounded
back. The model and its views are in [3D/](https://github.com/dbarzin/pandora-box/tree/main/3D), under the CERN Open
Hardware Licence v2 - Strongly Reciprocal (CERN-OHL-S v2).

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

After the first boot of the image, check that Secure Boot is on (the BIOS
shows the mode **User**). To give the computer back to another use: Security
→ Secure Boot → **Restore Factory Keys**.

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

### Writing the image

The SSD is written once, from another computer (in a USB enclosure):

```sh
lsblk -o NAME,SIZE,MODEL,TRAN          # the disk of the SSD, e.g. /dev/sdX
docker run --rm --device /dev/sdX -v "$PWD/image/mkosi.output:/o:ro" \
    pandora-box-builder dd if=/o/pandora-box.raw of=/dev/sdX bs=4M conv=fsync status=progress
```

Then the kiosk is updated in place, with image update devices or online
(see [image.md](image.md#ab-updates-of-the-image)), and diagnosed with
maintenance devices (see [image.md](image.md#maintenance-device)).
