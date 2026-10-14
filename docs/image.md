# Kiosk image

The kiosk is distributed as a complete system image (phase 2), built with
[mkosi](https://github.com/systemd/mkosi) from Debian 13 (trixie) packages,
and tested in a QEMU/KVM virtual machine. Everything runs in a Debian 13
container (`image/Dockerfile`: mkosi, QEMU, OVMF), so the developer only needs
Docker.

> [!WARNING]
> The image and the signature sets are signed with development keys only.
> The production image has no signature set: the kiosk scans nothing until a
> signature update device is inserted (see [signatures.md](signatures.md)).
> See the phase 2 tasks in the [roadmap](https://github.com/dbarzin/pandora-box/blob/main/ROADMAP.md#phase-2--minimal-hardened-image).

## Layout

| Path | Role |
|---|---|
| `image/mkosi.conf` | Image definition: Debian 13, x86_64, systemd-boot, unified kernel image (UKI), packages |
| `image/mkosi.repart/` | Partitions: ESP, two system slots (read-only root filesystem, its dm-verity hash and signature), data partition |
| `image/mkosi.images/initrd/` | The initrd: the default initrd of mkosi, which restarts instead of opening an emergency shell, without the files a read-only root does not need |
| `image/mkosi.prepare.chroot` | Runtime dependencies in a virtual environment (`/usr/lib/pandora-box`), from `image/requirements.txt` (pinned with their hashes) |
| `image/mkosi.build.chroot` | pandora-box wheel, built from the repository without network |
| `image/mkosi.postinst.chroot` | Installs pandora-box, its configuration, systemd service, tmpfiles and logrotate files (`packaging/`), and the clamd settings |
| `image/mkosi.extra/` | Files copied into the image: systemd presets (enabled and disabled units), `/etc/fstab`, growth of the data partition (`/usr/lib/repart.d/`), A/B updates (`/usr/lib/sysupdate.d/`, boot assessment), USBGuard policy, kernel settings (`sysctl.d`), firewall (`nftables.conf`), clamd sandbox |
| `image/mkosi.profiles/test/` | Test profile, for the virtual machine tests only |
| `image/vm/` | Virtual machine tests: corpus, QEMU driver, automated test |
| `image/build.sh`, `image/vm.sh` | Build and run in the container |
| `image/build-test.sh`, `image/package-update.sh` | Build the images of the virtual machine test; build a signed image update |

## Build

Requirements: Docker, about 5 GB of disk space, network access (Debian
packages and Python wheels).

```sh
image/build.sh                  # production image
image/build.sh --profile test   # test image
```

The image is written to `image/mkosi.output/`: `pandora-box.raw` (or
`pandora-box-test.raw`, a GPT disk image), the UKI (`.efi`) and the list of
installed packages (`.manifest`). The first build takes a few minutes; the
following ones reuse the installed packages (mkosi incremental build, cached
in the `pandora-box-mkosi` Docker volume) and take less than a minute.

The cache is not rebuilt when `image/requirements.txt` or
`image/mkosi.prepare.chroot` change: rebuild it with `image/build.sh -f`
(`docker volume rm pandora-box-mkosi` removes it completely).

The disk image has two system slots (A/B updates) and a data partition:

| Partition | Filesystem | Content |
|---|---|---|
| ESP | vfat, 512 MB | systemd-boot and the UKIs (`pandora-box_<version>.efi`), all signed |
| root, slot A (`pandora-box_<version>`) | EROFS, read-only, 1 GB | the whole system but `/var` (about 400 MB used) |
| root-verity, slot A | 64 MB | dm-verity hash tree of the root filesystem |
| root-verity-sig, slot A | 16 KB | signature of the root hash |
| root, root-verity, root-verity-sig, slot B (`_empty`) | same sizes | the next version (A/B updates) |
| data (`pandora-box-data`) | ext4, 1 GB in the image | `/var`: scan reports, quarantine, logs, signed signature sets (clamd reads its databases there) |

The version of the image is the date and time of its build, UTC, like the
serial of a signature set: `20261005143700` (`image/build.sh` sets it,
`--image-version=N` overrides it). It is in `/etc/os-release`
(`IMAGE_VERSION`), in the labels of its partitions (`pandora-box_<version>`,
`_verity`, `_veritysig`: 36 characters at most) and in the name of its UKI.

The root filesystem is read-only by design (EROFS cannot be written at all):
the system and its configuration only change with a new image. Everything
the kiosk writes is in `/var`. The data partition is the last one: at every
boot, `systemd-repart` grows it to fill the disk
(`/usr/lib/repart.d/20-var.conf`), then `systemd-growfs` grows its
filesystem (`x-systemd.growfs` in `/etc/fstab`), so the image can be written
to a disk of any size. As `/etc` is read-only, the machine ID is generated
again at every boot (a new journal folder per boot, within the journald
size limits).

The image contains:

- the kernel, systemd and udev, Python 3 and clamd (`clamav-daemon`), with no
  network service: `systemd-networkd`, `systemd-resolved`, freshclam and the
  ClamAV on-access scanner are disabled (`10-pandora-box.preset`);
- pandora-box in `/usr/lib/pandora-box` (`/usr/bin/pandora-box`), its
  configuration in `/etc/pandora-box/pandora-box.toml` and its service, which
  shows the curses interface on the first console;
- no `login` program and no root password: no interactive session is
  possible.

## Signing, Secure Boot and dm-verity

The boot chain is verified from the firmware to every block of the system:

1. with Secure Boot, the firmware only starts systemd-boot if it is signed
   with a key of its `db`, and systemd-boot only starts a signed UKI;
2. the UKI holds the kernel, the initrd and the kernel command line, which
   contains the dm-verity root hash (`roothash=`): changing it breaks the
   signature;
3. the root filesystem is opened with dm-verity: every block read is checked
   against the hash tree, whose root is that hash. A modified block cannot
   be read (I/O error, "data block ... is corrupted" in the kernel log);
4. with Secure Boot, the Debian kernel enables its lockdown mode
   (`integrity`): even root cannot modify the running kernel.

`/var` is not protected by dm-verity: it holds the data of the kiosk, never
programs or configuration.

The image is signed with `image/mkosi.key` and `image/mkosi.crt` (systemd-boot,
UKI, root hash). When they do not exist, `image/build.sh` generates a
**development** key pair, valid 10 years, that never leaves the developer's
computer (ignored by git). A kiosk must only trust the release keys of the
project, which are not defined yet: they will be kept out of the repository,
and mkosi can sign with a key in a hardware token
(`--secure-boot-key-source=provider:pkcs11`).

The image also trusts the signature sets signed with `image/update.key`: its
public key `image/update.pem` is installed in `/usr/share/pandora-box/keys/`
(under dm-verity). `image/build.sh` generates a development pair when they
do not exist; the test profile signs its signature set with it. See
[signatures.md](signatures.md).

On a kiosk, the certificate is enrolled in the firmware in place of the
Microsoft keys (PK, KEK and db), so that it starts nothing else. The ESP
contains the keys in the format expected by the firmware
(`loader/keys/auto/`): with the firmware in Secure Boot setup mode (keys
cleared in the firmware setup), systemd-boot enrolls them at the first boot
(`secure-boot-enroll force`). Enrolling only the project certificate also
refuses the option ROMs signed by Microsoft: a computer whose display or
disk controller needs one (a graphics card, for instance) would not start
it. The virtual machines enroll the certificate with `virt-fw-vars` instead
(`image/vm/machine.py`).

systemd-boot has no menu (`/efi/loader/loader.conf`, `timeout
menu-disabled`): it starts the default entry, the newest version not marked
bad, and does not read the keyboard. A keyboard plugged into the kiosk
cannot select another version, edit the kernel command line (refused with
Secure Boot anyway), power off the machine or reboot into the firmware
setup. The ESP is not part of the image updates: this file stays as
installed.

## Screen

The kiosk draws its graphical interface into the framebuffer of the graphics
driver (`/dev/fb0`), without a display server or a browser, and puts the
first console (`tty1`) in graphics mode so that the kernel does not draw text
over it: see [interface.md](interface.md). The screen of the reference
hardware is a Waveshare 7-inch HDMI LCD (1024x600). The firmware of the
ThinkCentre starts in 1024x768, and the Intel driver keeps that mode: the
bottom of the interface would be off the screen. The kernel command line
therefore sets the mode of the screen, `video=1024x600M@60` (CVT timings, as
Waveshare documents them); with another screen, change it in
`image/mkosi.conf`. The Intel driver still keeps the framebuffer of the
firmware, large enough for that mode: the kiosk only draws its visible part,
the size of `video=`, and follows a change of the framebuffer size. It logs
the size and the pixel format it draws for (`display_started`).

The curses interface (`kiosk.interface = "curses"`, administrator mode) draws
text on the console instead: it shrinks the console to the screen of
`video=` (8x16 font: 37 lines, 128 columns) the same way, and logs the size
it draws for (`display_started`, `display_resized`).

## Maintenance device

A kiosk has no login and accepts no connection: to diagnose it without
taking its disk out, a **maintenance device** brings its logs back. On the
computer that holds the update key, with a USB key mounted:

```sh
image/maintenance-key.sh /media/$USER/KEY            # any kiosk
image/maintenance-key.sh /media/$USER/KEY kiosk-1    # only kiosk.name = kiosk-1
```

writes `pandora-box-maintenance/` on the key: a request signed with the
update key, valid 7 days. Inserted into the kiosk, even one without
signatures, the key is not scanned: the kiosk verifies the request, writes
its logs to `pandora-box-maintenance/<kiosk>-<date>/` (journal of the boot
and of the previous one, kernel, services, logs of the kiosk and of
USBGuard, USB devices and rules, disks, boot entries, hardware and screens,
installed signature set) and ejects the key (`src/pandora_box/maintenance.py`).
Nothing changes on the kiosk, and the export holds no key, credential or
scan report. A request signed by another key, too old, for another kiosk
or modified is refused (`maintenance_refused` in the log).

One key can hold a maintenance request, a signature set
(`pandora-box-signatures/`) and an image update (`pandora-box-image/`):
the kiosk handles them in that order in one insertion, each verified on its
own; the export shows the kiosk before the updates, and the image update
comes last as it restarts the kiosk.

## USB devices

A kiosk must only use USB storage devices: a key that is also a keyboard
(BadUSB) could type commands, a network adapter could open a network.

- **USBGuard** (`/etc/usbguard/rules.conf`) allows hubs and the devices
  whose interfaces are all mass storage; it blocks every other device,
  including a key that has a storage interface and another one. Blocked
  devices are listed in `/var/log/usbguard/usbguard-audit.log`.
- **No device is authorized before USBGuard has started**: the kernel
  command line sets `usbcore.authorized_default=0`. If USBGuard does not
  start, no USB device is usable.
- **Drivers removed from the image** (`KernelModulesExclude=` in
  `image/mkosi.conf`): USB network adapters, wireless, Bluetooth, USB serial
  adapters and modems, plus the staging drivers and batman-adv, which would
  bring the wireless modules back as dependencies. Such devices have no
  driver even if they were authorized.
- **USB HID is kept for the touchscreen** of the reference hardware, a
  Waveshare 7-inch HDMI LCD (C), allowed by its own rule:

  ```
  allow id 0eef:0005 serial "220211" name "WS170120" with-interface 03:00:00 via-port "1-8"
  ```

  its USB touch controller (eGalax) with its single generic HID interface (a
  keyboard has `03:01:01`), on the internal port of the enclosure, this unit
  only. A device that copies its identifiers on another port, or that adds a
  keyboard interface, is blocked; so is every keyboard and mouse. Another
  unit or port: adapt the rule to the line the audit log shows when it is
  blocked. The kernel (`mousedev`) turns a touch into a left click on
  `/dev/input/mice`, which the kiosk reads to confirm a cleaning.

  The test image allows the touch tablet of the virtual machine (QEMU
  `usb-tablet`, same interface) in its own rule
  (`/etc/usbguard/rules.d/50-vm-test.conf`): the test confirms a cleaning
  with a touch. The keyboard of the virtual machines is not a USB device.

## System hardening

After the ANSSI configuration recommendations for GNU/Linux systems:

- **Kernel settings** (`/usr/lib/sysctl.d/90-pandora-box.conf`): kernel
  logs and addresses hidden, no ptrace, no kexec, no unprivileged eBPF, user
  namespaces or userfaultfd, io_uring disabled, no SysRq, no core dumps,
  protected links and FIFOs, no IP forwarding or redirects.
- **Kernel command line**: memory initialized on allocation and free, no
  slab merging, randomized page allocator and kernel stack, no legacy
  vsyscall, no debugfs, lockdown in `confidentiality` mode (Secure Boot only
  enables `integrity`: even root cannot read kernel memory), IOMMU in strict
  mode and no DMA before the kernel starts (DMA attacks through ports).
- **Firewall** (`/etc/nftables.conf`): every packet is dropped, in and out,
  except on the loopback interface.
- **No login**: no login console on the screens (`getty@` and `getty-static`
  disabled), no `login` program, no root password, Ctrl-Alt-Del masked.
- **clamd sandbox** (`clamav-daemon.service.d/50-pandora-box.conf`): no
  network, read-only system, no device, system call filter,
  `MemoryDenyWriteExecute` (the Debian clamd interprets bytecode signatures,
  without JIT); it runs as the `clamav` user.
- **Kiosk service** (`packaging/systemd/pandora-box.service`): read-only
  system, system call filter, limited capabilities. It keeps `AF_NETLINK`
  (udev events), the capabilities needed to start the worker sandbox
  (namespaces, change of user, loopback interface of the workers) and
  cannot have `MemoryDenyWriteExecute` or `RestrictSUIDSGID`: the first one
  would also apply to the YARA-X workers, which compile rules to native
  code, the second one refuses a system call bubblewrap needs.
- **Kernel modules removed**: network protocols the kiosk never uses (DCCP,
  SCTP, RDS, TIPC) and FireWire, another way to reach the memory of the
  kiosk, besides the USB network, Bluetooth and Wi-Fi drivers. The protocols
  and FireWire are also listed in `/etc/modprobe.d/pandora-box.conf`, where
  the auditing tools look for them.
- **Audit log** (`/etc/audit/audit.rules`, auditd): loading of kernel modules,
  mounts (except those of systemd and bubblewrap), every program started,
  ptrace, changes of the clock, of the signature sets and of the staged image
  updates. The rules are locked (`-e 2`): changing them needs a reboot. They
  are loaded by `auditctl -R` (`augenrules` writes into `/etc`, read-only).
  The log is in `/var/log/audit/audit.log`, for instance
  `ausearch -k signatures -i`.

The test profile includes `lynis`, run by the automated test: hardening index
73, no warnings. Its remaining suggestions do not apply to a kiosk without
login (password policy, banners, package tools, fail2ban) or come later
(remote logging, phase 5).

## Scan worker sandbox

The scan workers parse hostile files with several engines (libmagic, YARA-X,
the clamd client...): they are assumed compromisable (`scan.sandbox`,
`src/pandora_box/sandbox.py`).

- **One engine per worker**: a compromised engine (a crafted file
  exploiting YARA-X or libmagic) cannot forge the result of another engine.
  The kiosk hashes each file itself, asks the worker of each hash engine
  (MalwareBazaar, Hashlookup) with the hashes only, then passes the file to
  a worker of each content engine (ClamAV, YARA-X), and combines their
  results itself; a worker only reports the result of its own engine.
  Content engines have `scan.workers` workers each, hash engines one.
  `scan.workers = "auto"` (the default) scans one file per CPU at the same
  time, as many as the memory allows (1.5 GB for clamd and the system, then
  256 MB each, for the YARA rules mostly): 6 on the ThinkCentre M720q (6
  cores, 8 GB), 1 or 2 on a computer with 2 GB.
- **No access to the device**: the kiosk opens each file safely (no link
  followed, the file of the inventory) and passes the open descriptor to the
  workers of the content engines, which never open a file of the device;
  the workers of the hash engines never get the file.
- **bubblewrap**: new PID, IPC, UTS, cgroup and network namespaces (no
  network interface but loopback); a file system holding only `/usr`, a few
  files of `/etc`, the signature folders of the enabled engines
  (read-only), the YARA-X cache and the clamd socket folder: no device
  mount point, no reports, quarantine, logs or configuration.
- **Dedicated user** `pandora-box-scan` (`packaging/sysusers/`), switched to
  by `setpriv`: no capability, no supplementary group, `no_new_privs`.
- **System call filter** (`src/pandora_box/seccomp.py`), installed once
  the engines are loaded: no program execution, debugging, mounts,
  namespaces, eBPF, kernel modules, io_uring, network sockets...
- **Untrusted answers**: workers answer in JSON (never pickle), limited in
  size and validated (`src/pandora_box/protocol.py`); the path and size of
  the file always come from the kiosk. A malformed answer kills the worker
  and the file is reported as an error.

bubblewrap runs as root (the kiosk service), so unprivileged user namespaces
stay disabled.

The virtual machine test checks these settings and prints the exposure of
the services measured by `systemd-analyze security` (0 to 10, lower is
better): about 1.6 for clamd, 3.8 for the kiosk (which starts the worker
sandbox), 2.8 for USBGuard.

## Unified kernel image

The UKI (about 40 MB) holds the kernel, the initrd of the image
(`image/mkosi.images/initrd/`, without the udev hardware database, charset
conversions, Perl, documentation and networkd) and an initrd of kernel
modules. That one only holds what mounts the root filesystem
(`KernelModulesInitrdInclude=` in `image/mkosi.conf`): disk controllers
(NVMe, SATA, Intel VMD, eMMC, virtio), dm-verity, EROFS and CRC32C, which
EROFS loads by name. The other modules are loaded from the root filesystem.
A computer whose disk controller is not in this list cannot find its root
filesystem: add its module there.

## A/B updates of the image

The system is updated as a whole, never file by file
(`src/pandora_box/imageupdate.py`, `image/mkosi.extra/usr/lib/sysupdate.d/`).

- **Building an update.** Build a new version (its version is the time of
  the build: newer than the running one), then package it:

  ```sh
  image/build.sh
  image/package-update.sh image/mkosi.output/pandora-box.raw update image/update.key
  ```

  `update/` holds the partitions of slot A of the new image (compressed
  with xz, the format that `systemd-sysupdate` of Debian 13 decompresses),
  named with their UUID, the UKI, and a manifest signed with the update key
  (the format of the signature sets, content `image`, serial = version).
  About 215 MB, of which 40 MB for the UKI.
- **Installing it.** Copy `update/` as `pandora-box-image/` at the root of a
  USB key and insert it into the kiosk (`updates.image_from_devices`). The
  kiosk verifies the signature, that the version is newer than the running
  one and every file, copies them to `/var/lib/pandora-box-image`, then
  `systemd-sysupdate` writes the partitions to the free slot and the UKI to
  the ESP with 3 boot tries (`pandora-box_2+3-0.efi`), and the kiosk
  restarts. The running version is never overwritten; the device is not
  scanned.
- **Online.** With the profile `online` and `updates.image_url` (the URL of
  a published update folder), `pandora-box-update.service` downloads a newer
  update as its unprivileged user (every file checked), then installs it as
  root without network (`pandora-box-image install`), with the same checks
  as an update device. The kiosk restarts on the new version when it is idle
  (between two devices): a scan is never interrupted.
- **Boot assessment.** The new version is kept (`systemd-bless-boot`) once
  `boot-complete.target` is reached, which requires the kiosk to be ready
  (`Type=notify`). A boot that fails restarts: kernel panic (`panic=10`),
  failure in the initrd (its emergency shell restarts,
  `image/mkosi.images/initrd/`), or no `boot-complete.target` in 15 minutes.
  After 3 failed tries, systemd-boot boots the previous version again; the
  data (`/var`) is shared by the versions.

Two keys protect the update: the update key decides what is installed, the
Secure Boot key decides what can boot (the UKI is signed and holds the root
hash of its slot).

## Dependencies

Runtime dependencies are pinned with their hashes in `image/requirements.txt`.
After a change of the dependencies in `pyproject.toml`, regenerate it with
`image/lock-requirements.sh`.

## Profile online

`--profile online` adds online signature updates from their sources (see
[signatures.md](signatures.md#online-updates)): systemd-networkd (DHCP on the
wired network), systemd-resolved, freshclam, the `pandora-box-update` timer
and a firewall that only lets the update service and the log export to a
SIEM out (see [siem.md](siem.md))
(`image/mkosi.profiles/online/`). Its `postinst.chroot` enables `[updates]`
in the configuration of the kiosk, and installs the credentials of
`image/credentials.toml` (outside git) in `/etc/credstore`: an image built
with them must not be published. Without the profile, the image has no
network configuration at all.

```sh
cp image/credentials.toml.example image/credentials.toml   # then the abuse.ch Auth-Key
image/build.sh --profile online
```

## Test profile

`--profile test` builds `pandora-box-test.raw`, for the virtual machine tests
only, never for a kiosk. It includes the profile `online`, and adds:

- a test-only signature set, generated from `image/vm/corpus.py`, signed
  with the development update key and installed (serial 1): a ClamAV
  database that detects the EICAR test file, a MalwareBazaar database, a
  Hashlookup filter and a YARA rule, each detecting one file of the test key;
- the matching configuration, with every engine enabled, and online updates
  from `http://10.0.2.2:8080/` (the build container, seen from the QEMU user
  network);
- a root shell without password on the virtio console (`hvc0`), and kernel
  messages on the serial port;
- `kiosk-screen`, which prints the texts of the kiosk screen in that shell (the
  graphical interface logs them, `display_screen` events at the DEBUG level).

## Automated test in a virtual machine

```sh
image/build-test.sh   # the test image (version 1) and updates to versions 2 to 4
image/vm.sh test
```

The test boots the test image in QEMU (KVM when `/dev/kvm` is writable,
otherwise much slower emulation), with UEFI firmware (OVMF) where the image
signing certificate is enrolled (Secure Boot enabled), and an empty USB 3
controller. It plays the whole user workflow, through QMP and the shell on
the virtio console:

1. the kiosk starts with its five engines and the signature set 1, verified,
   and no systemd unit fails;
2. the scan workers (one engine each) run in their sandbox (user,
   capabilities, `no_new_privs`, seccomp, network namespace, no device or
   kiosk data in their file system); Secure Boot is enabled, the kernel is
   locked down and the root filesystem is on dm-verity;
3. the root filesystem is read-only EROFS, and `/var` was grown to fill the
   8 GB disk of the machine;
4. an emulated USB key (a vfat disk image holding the corpus) is inserted,
   which triggers the same udev events as a real device;
5. each file gets the expected verdict from the expected engine;
6. the cleaning is confirmed with a key press on the kiosk screen; the
   infected files are quarantined (checked against their SHA-256) and
   removed from the key, which is ejected;
7. the key is removed: only the clean files are left on it;
8. the cleaned key is inserted again and reported clean;
9. keys of the other filesystems go through the same scan and cleaning, then
   are reported clean: exfat, NTFS, ext4 (its files private to their owner,
   as on the key of a Linux user) and a key with an MBR partition table and a
   vfat partition; a key on EROFS (a filesystem the kernel mounts, but not
   allowed) and an ext4 key whose group descriptors are overwritten are
   refused; a vfat key with a cut cluster chain is mounted, but the file
   cannot be read: the key is reported not verified (`image/vm/keys.py`;
   exfat and NTFS have no tool writing files without mounting them: they
   are formatted in the container, then filled by the kernel of the machine,
   the kiosk stopped);
10. a signature update key with a newer set (serial 2) is installed, the
    engines are reloaded and the new sample it detects is found; a modified
    set, a set signed with another key and an older set are refused; a newer
    set published on an HTTP server by the test is downloaded by the update
    service (only its changed file), installed and loaded by the idle kiosk,
    and only the update service user can open a network connection; the
    log entries of the kiosk are sent to a syslog receiver of the test
    (`[siem]`), by the SIEM export user, who cannot read the reports; an
    emulated USB keyboard and network adapter are blocked by USBGuard (no
    input device, no network interface) and the excluded drivers are not in
    the image; the kernel settings, the firewall and the service sandboxes are
    in place;
11. after a reboot, the scan reports are still there, the root filesystem is
    unchanged and the signature set 5 is verified at start; a key pressed
    while the firmware and the boot loader start does not open the boot
    loader menu;
12. an image update key to version 2 is inserted: the kiosk installs it in
    the free slot and restarts, version 2 boots from slot B and is kept (its
    UKI loses its boot counter), with the same data; version 3, published on
    an HTTP server by the test, is downloaded and installed by the update
    service, then the idle kiosk restarts on it and it is kept; finally a
    version 4 whose root filesystem is modified is installed from a key: it
    fails 3 boots (dm-verity, the initrd restarts) and systemd-boot goes back
    to version 3.

Two more machines boot the same image:

- with a block of the root filesystem modified on the disk (a canary file of
  the test image): it cannot be read and the kernel reports the corruption;
- with firmware trusting another key: the firmware refuses systemd-boot
  ("Access Denied") and nothing starts.

The built image is never modified: the machine writes to a new disk overlay
(`system.qcow2`) at every start. It takes about 7 minutes with KVM.
`image/vm.sh test --workdir DIR` keeps the serial console logs, the QEMU logs
and the key images in `DIR`, and the kiosk log (`kiosk.log`) when the test
fails.

## Interactive virtual machine

```sh
image/vm.sh run                              # test image
image/vm.sh run image/mkosi.output/pandora-box.raw
```

The kiosk screen is shown over VNC, display `:0`
(port 5900), for example with `gvncviewer localhost:0` (most viewers read
`host:N` as a display number, port 5900 + N); `kiosk-screen` also prints it in the shell of the test
image. The terminal shows this shell (virtio console), not the kiosk, and, after
`Ctrl-A C`, the QEMU monitor; `Ctrl-A X` stops the machine. A test key is
created in `image/vm/work/usbkey.img`; insert and remove it from the monitor:

```
(qemu) drive_add 0 if=none,id=key,format=raw,file=/src/image/vm/work/usbkey.img
(qemu) device_add usb-storage,bus=xhci.0,drive=key,id=usbkey,removable=on
(qemu) device_del usbkey
```

Paths are seen from the container, where the repository is mounted on `/src`.
Any disk image can be used as a key, but hostile samples should only ever be
copied into key images, never onto the development desktop.

### Real USB keys

A USB key plugged into this computer can be passed through to the machine.
List the USB storage devices of this computer, with their port (`BUS-PORT`)
and identifier (`VENDOR:PRODUCT`):

```sh
image/vm.sh usb
```
```
1-4  abcd:1234  General UDisk
    sda     3,8G
    └─sda1  3,7G vfat   /media/didier/6B20-F9DE
```

Then start the machine with `--usb`, followed by a port, which passes through
any key plugged into that port, like the USB port of a kiosk, or by an
identifier, which passes through that model of key on any port:

```sh
image/vm.sh run --usb 1-4
```

QEMU attaches the key to the machine as soon as it is plugged in (or at once
if it is already there) and takes it from this computer: its kernel driver is
detached. Before starting the machine, unmount the key
(`udisksctl unmount -b /dev/sda1`) and disable the automatic mounting of USB
devices of the desktop, so that this computer never mounts a key meant for
the kiosk. `--usb` can be given several times. On GNOME:

```sh
gsettings set org.gnome.desktop.media-handling automount false
gsettings set org.gnome.desktop.media-handling automount-open false
# after the tests: the same commands with "true"
```

QEMU then runs as root in the container, with access to the USB devices of
this computer and its network (to receive the udev events of the keys
plugged in later); the kiosk screen is on `localhost:5900` as before.
