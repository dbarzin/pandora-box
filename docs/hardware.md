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

## Installation

The BIOS settings of the M720q, the writing of the image and the first boot
are described in [Initial installation](install.md).
