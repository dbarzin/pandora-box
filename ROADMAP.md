# PandoraBox Roadmap

This roadmap follows the development of **PandoraBox** phase by phase: goals, tasks done and to do, decisions. For an overview of the project, see the [README](README.md); for the documentation, see the [documentation site](https://sourcentis.github.io/pandora-box/).

## Vision

Provide a **free, auditable and hardened** USB decontamination kiosk, a credible alternative to commercial solutions, suited to sensitive environments (healthcare, industry, public administration).

## Guiding principles

- **Minimal, hardened image**: the system contains only what is needed to scan.
- **Defense in depth**: the USB device is hostile, and analyzers are assumed to be compromisable.
- **Offline by default**: no network connection is required to scan; updates go through a signed channel.
- **Plugin engines**: every detection engine implements a common interface.
- **Traceability**: every scan produces a structured, timestamped report.
- **Language**: Python 3.11+ for the orchestrator (reuse of PandoraBox v1 code, analysis ecosystem).

## Priorities

The open tasks of the phases below, by priority.

**1. First public release**

1. Release keys of the project: create them in two YubiKeys and publish their fingerprints; the signing tools are ready (phase 2, [docs/release.md](docs/release.md))
2. Documented threat model (phase 6)
3. Deployment guide: kiosk placement, user procedure, associated USB policy (phase 6)
4. Physical hardening and assembly guide (phase 2)

**2. Hardening and user features**

1. Filesystems of the devices parsed outside the kernel of the kiosk: NTFS, exFAT and ext4 through FUSE in the sandbox of the engines, vfat in the kernel (phase 2)
2. File transfer stations: signed key identifier, then inbound station, then outbound station (phase 4); they need the `online` profile, a firewall opening to the server and SFTP/SMB clients in the image, to cover in the threat model
3. Selective copy of clean files to a second device, sharing the selection of the inbound station, and the reformatting option, which writes back the clean files too (phase 4)
4. Office documents with `oletools` (phase 4)
5. Scan receipt for the user (phase 5)
6. German and Dutch texts, accessibility (phase 3)
7. Protected administrator mode (phase 3)

**3. Later**

1. Fleet management: central server, centralized distribution of updates, dashboard (phase 5)
2. Fuzzing of the entry points, then external security audit (phase 6)
3. Mapping to NIS2 and ISO 27001 (phase 6)
4. Executables with `capa`, content disarm and reconstruction with Dangerzone (phase 4)
5. Software TPM, once measured boot is used (phase 2)
6. Maintenance console or SSH, to reconsider: it goes against the absence of any login while the maintenance device is enough (phase 2)

---

## Phase 0 — Foundations

Goal: restart from a clean base while reusing what works in PandoraBox v1.

### Code reused from PandoraBox v1

| Component | Decision |
|---|---|
| State machine (START, WAIT, SCAN, CLEAN, STOP) | Kept, refactored into a dedicated module |
| USB detection with `pyudev` | Kept |
| Device mounting / unmounting | Kept, hardened (mount options, read-only) |
| Quarantine of infected files | Kept |
| Curses interface (administrator mode) | Kept |
| Logging, logrotate, systemd service | Kept, moved to structured JSON |
| USB auto-mount mode | Kept for development only (`device.auto_mount`): never on the kiosk, which mounts devices read-only itself |
| Image slideshow display (`fim`) | Removed, replaced by the graphical kiosk interface in phase 3, which reuses its images |
| Single-instance lock | Kept |
| `pypandora` calls | Removed, replaced by the engine pipeline |
| Comodo engine | Removed (no longer maintained on Linux) |

### Tasks

- [x] Project name: **PandoraBox** (repository `pandora-box`, Python module `pandora_box`)
- [x] New repository, GPL-3.0 license, `CONTRIBUTING.md`, `SECURITY.md`
- [x] Python package structure (`pyproject.toml`), type hints, `ruff`, `mypy`
- [x] TOML configuration (replaces the `.ini`), validated at startup
- [x] Keep `FAKE_SCAN` mode for development
- [x] Continuous integration: lint, unit tests, package build
- [x] End-to-end tests in a container with a simulated USB disk image (loop device)

### Current status

The PandoraBox v1 code has been ported to the `pandora_box` package (`src/pandora_box/`):

| Module | Role |
|---|---|
| `cli.py` | Command line entry point (`pandora-box`) |
| `config.py` | TOML configuration, validated at startup |
| `statemachine.py`, `kiosk.py` | State machine and kiosk workflow |
| `monitor.py` | USB detection with udev |
| `device.py` | Hardened mounting (`ro,noexec,nosuid,nodev` on a read-only block device, read-write only to clean) |
| `inventory.py` | Device inventory: never follows links, never leaves the device, limits |
| `scanner.py`, `workers.py`, `worker.py` | Scan in supervised worker processes, timeouts and watchdog |
| `sigsets.py`, `publish.py` | Signed signature sets: verification, installation, publication from the sources, `pandora-box-signatures` command |
| `sandbox.py`, `seccomp.py`, `protocol.py` | Worker sandbox (bubblewrap, dedicated user, system call filter), validated JSON messages from the workers |
| `pipeline.py`, `hashing.py`, `filetype.py` | Per file: hashes (SHA-256, SHA-1, MD5), libmagic type, engines |
| `engines/` | Engine interface, MalwareBazaar, Hashlookup, ClamAV, YARA-X and fake engine |
| `hashdb.py`, `bloom.py`, `clamd.py` | MalwareBazaar database, DCSO Bloom filter reader, clamd client |
| `policy.py` | Verdict aggregation per file and per device |
| `report.py`, `schemas/` | JSON scan report and its JSON Schema |
| `signatures.py` | Signature provenance and freshness, verification hook (phase 2) |
| `quarantine.py` | Copy of infected files with a manifest referencing the report |
| `ui/` | Graphical kiosk interface (framebuffer, themes), curses (administrator) and console interfaces |
| `logs.py`, `lock.py` | JSON logs, single-instance lock |

Deployment files are in `packaging/`: example configuration, systemd service, tmpfiles and logrotate.

The detection engines, their data files and the verdict policy are described in [docs/engines.md](docs/engines.md). Every engine is enabled by default: the kiosk refuses to start when an enabled engine cannot load its signatures, or when no content engine (ClamAV or YARA-X) is enabled.

Development setup, configuration and tests: see the [development guide](https://sourcentis.github.io/pandora-box/development/).

## Phase 1 — Scanning engine (MVP)

Goal: replace Pandora with an equivalent or better local pipeline.

- [x] Common plugin interface: `scan(file) -> Verdict` (clean, suspicious, malicious, error, skipped)
- [x] Device inventory and hash computation (SHA-256, SHA-1, MD5)
- [x] **Hashlookup (CIRCL)** as an offline Bloom filter: skip the content engines for known files (known does not mean benign)
- [x] **MalwareBazaar**: offline list of malicious hashes
- [x] **ClamAV** through `clamd` (Unix socket) + third-party signatures
- [x] **YARA-X** + YARA Forge and signature-base rules
- [x] Real file type identification (`libmagic`)
- [x] Parallel scan in supervised worker processes, timeout per file and per engine
- [x] Verdict aggregation with a configurable policy (one positive engine is enough, etc.); `scan.suspicious` and `scan.on_error` policies (`block` / `warn`)
- [x] Limits: maximum file size, number of files, directory depth
- [x] Keys with several partitions: every filesystem scanned in turn, one verdict, report and confirmation for the key; an encrypted or unknown partition makes the key not verified
- [x] JSON scan report (device, files, verdicts, engines, signature versions) with a JSON Schema
- [x] Test set: EICAR, harmless samples, known false positives (`tests/corpus/`)

See [docs/engines.md](docs/engines.md) for the engines and the verdict policy.

## Phase 2 — Minimal hardened image

Goal: ship a ready-to-flash system image with the smallest possible attack surface.

### Current status

A first image is built with mkosi from Debian 13 packages and boots in a QEMU/KVM virtual machine, where an automated test plays the whole workflow with an emulated USB key and the real engines. The root filesystem is read-only (EROFS) and protected by dm-verity, the data (`/var`) is on its own partition, grown to fill the disk at boot, and the bootloader and unified kernel image are signed for Secure Boot. Only USB storage devices are allowed (USBGuard), and the image has no driver for USB network, wireless or Bluetooth devices. The system is hardened (kernel settings and lockdown, firewall, no login console, clamd sandbox) and the scan workers run in a sandbox (bubblewrap, dedicated user, system call filter). The kiosk only uses a signed signature set, verified at each start; without one, it scans nothing and waits for a signature update device. With the image profile `online`, the kiosk downloads the signatures from their sources itself (ClamAV, YARA Forge, MalwareBazaar, Hashlookup), with the credentials of the image, and signs the set with its own key. See [docs/image.md](docs/image.md):

```sh
image/build-test.sh   # build the test images (Docker only)
image/vm.sh test      # end-to-end test in a virtual machine
```

### Build

- [x] Image based on Debian 13 (trixie), built with `mkosi`
- [x] Single target: x86_64 (`Architecture=x86-64` in `image/mkosi.conf`, see reference hardware); ARM64 is not targeted, as the Raspberry Pi proved too slow
- [x] Signed image and published checksums: `image/release.sh` builds a tag only, signs the image with the release keys of a YubiKey, and publishes the compressed disk image, the signed image update and the checksums signed with the update key as a draft GitHub release ([docs/release.md](docs/release.md))
- [x] Reproducible builds and an SBOM generated for every release: inputs derived from the tag (Debian snapshot, `SOURCE_DATE_EPOCH`, seed), `image/reproduce.sh` builds a release again and compares everything but the signatures, CycloneDX SBOM published with the release ([docs/release.md](docs/release.md#reproducing-a-release))

### Reference hardware

- **Computer**: refurbished Lenovo ThinkCentre (x86_64)
- **Memory**: 2 GB minimum, the size of the test virtual machine (clamd alone uses about 1 GB with the full ClamAV databases)
- **Display**: Waveshare 7-inch HDMI LCD (C), capacitive touchscreen, 1024x600 (USB touch controller `0eef:0005`)
- **Enclosure**: 3D-printed

Validated hardware and its settings: [docs/hardware.md](docs/hardware.md); BIOS configuration: [docs/install.md](docs/install.md).

Tasks:

- [x] List of validated ThinkCentre models (CPU, RAM, USB ports, Secure Boot support): ThinkCentre M720q Tiny (10T7, Core i5-9400T, Intel UHD 630)
- [x] Touchscreen model selection and driver support: Waveshare 7-inch HDMI LCD (C), mode of the screen on the kernel command line, touch controller allowed by USBGuard
- [ ] Physical hardening: only the scanning USB ports reachable from the outside, other ports and BIOS access protected
- [ ] Assembly guide (bill of materials, printing settings, wiring, USB port layout for the user)
- [x] Enclosure design published in the repository (source files + STL), under an open hardware license: the STL of the 7-inch and 10-inch models and the OpenSCAD source of the 7-inch model are in [3D/](3D/), under the CERN-OHL-S v2; the source of the 10-inch model remains to be added
- [x] BIOS configuration guide (password, boot order locked, Secure Boot with the image key enrolled from setup mode, VT-d, restart after a power failure)

### System integrity

- [x] Read-only root filesystem, variable data on a separate partition
- [x] `dm-verity` on the system partition
- [x] Unified Kernel Image (UKI) and Secure Boot (development keys, or the release keys of a YubiKey with `image/build.sh --release`)
- [x] Atomic A/B partition updates (`systemd-sysupdate`), installed from a signed image update device, with boot assessment and automatic rollback
- [x] Online image updates (`updates.image_url`): downloaded by the update service, installed without network, the idle kiosk restarts on the new version
- [x] Smaller UKI: 40 MB instead of 130 MB, the initrd only holds the kernel modules needed to mount the root filesystem

### Hardening

- [x] No interactive account by default, no SSH: no `login` program, no root password, no login console, Ctrl-Alt-Del masked
- [x] Maintenance device: a USB key with a request signed with the update key (valid 7 days) brings back the logs of the kiosk, without login or network access
- [ ] Maintenance console or SSH, if the logs are not enough
- [x] Hardened kernel settings (`sysctl`, `lockdown=confidentiality`, memory initialization, IOMMU, unused modules removed)
- [x] `nftables` firewall: deny all by default
- [x] **USBGuard**: only mass-storage devices (and hubs) are allowed (BadUSB protection), no device is authorized before USBGuard starts (`usbcore.authorized_default=0`)
- [x] No USB network, wireless, Bluetooth, USB serial and modem drivers in the image; USB HID is kept for the touchscreen, keyboards and mice are blocked by USBGuard
- [x] USBGuard rule allowing the touchscreen of the reference hardware (Waveshare `0eef:0005`, its serial, HID interface only, internal port), touch tested in the virtual machine
- [x] No boot loader menu: systemd-boot does not read the keyboard (a keyboard works in the firmware, before USBGuard starts)
- [x] Devices mounted with `ro,noexec,nosuid,nodev` by the kiosk, no automount (no udisks in the image); the block device is read-only while it is scanned (`blockdev --setro`), so the kernel writes nothing to it, and an ext4 filesystem whose journal needs a replay is not mounted (device not verified) rather than scanned without its journal
- [x] Limited set of supported filesystems (vfat, exfat, ntfs3, ext4): the kiosk refuses to mount any other
- [ ] Filesystems of the devices parsed outside the kernel of the kiosk: today its kernel parses the structures given by the device, and a flaw of a filesystem driver (ext4, ntfs3 and exfat get regular fuzzing findings on crafted images) runs code in the kernel, before any sandbox; the read-only block device, `noexec`, the four filesystems and the root filesystem on dm-verity limit the damage without removing the risk. Hybrid approach: NTFS, exFAT and ext4 mounted through FUSE (ntfs-3g, exfat-fuse, fuse2fs, Debian packages), the driver running in the sandbox of the engines (dedicated user, no network, system call filter, only the block device and `/dev/fuse` passed by the kiosk), with a watchdog for a crashed or stuck driver; vfat stays in the kernel, its simplest and oldest driver, as fusefat is experimental and lklfuse is not packaged in Debian. A compromised FUSE driver no longer takes over the kiosk, but can still hide files from the scan: the reformatting option remains the answer to a forged structure. A disposable micro-VM reading the device through virtio-blk would also cover vfat, at a much higher cost
- [x] Hardened systemd services (`ProtectSystem`, `NoNewPrivileges`, seccomp filters; `PrivateNetwork` and `MemoryDenyWriteExecute` for clamd), exposure measured by `systemd-analyze security` in the virtual machine test
- [x] Each scan worker runs in a `bubblewrap` sandbox, without network, under a dedicated user without capabilities, with a system call filter; the kiosk opens the files and passes their descriptors, and only accepts validated JSON from the workers
- [x] One worker per engine, so that a compromised engine cannot forge the results of another one: the kiosk hashes the files itself, hash engines never get the file, the kiosk combines the results
- [x] Audit log (`auditd`) for sensitive operations
- [x] Assessment with `lynis` and the ANSSI configuration recommendations for GNU/Linux systems

### Signature updates

See [docs/signatures.md](docs/signatures.md).

- [x] Signed signature sets (Ed25519, verified with `openssl`): no rollback, atomic installation, previous set kept
- [x] Signature verification before loading databases: the installed set is verified at each start, the engines (and clamd) only read it
- [x] Offline updates from a signed USB device, for air-gapped kiosks
- [x] Publication: `pandora-box-signatures publish` downloads the sources (ClamAV, YARA Forge, MalwareBazaar, Hashlookup) with a cache, checks the set with the kiosk engines, then signs it; container `publish/`
- [x] Online updates through a dedicated channel: image profile `online`, update service as the only user allowed out by the firewall, HTTP(S) and proxy, only the changed files downloaded, new set loaded when the kiosk is idle
- [x] Online updates from the sources themselves (freshclam, YARA Forge, MalwareBazaar, Hashlookup): the abuse.ch Auth-Key is a systemd credential of the image (`image/credentials.toml`, outside git), the set is signed with a key generated on the kiosk
- [ ] Release keys of the project: stored in two YubiKeys 5 (Secure Boot key RSA 2048, update key Ed25519), used through PKCS#11 by the build ([docs/release.md](docs/release.md)); the keys remain to be created and their fingerprints published, and the publication schedule to define
- [x] Time synchronization of online kiosks: systemd-timesyncd, NTP servers of the organization or from DHCP, only timesyncd allowed to send NTP requests by the firewall

### Testing in a virtual machine

The container end-to-end tests (`tests/e2e/run.sh`) cover the scan workflow, but not the image itself: boot, Secure Boot, dm-verity, kernel settings, udev, USBGuard, systemd services. The whole chain is tested locally in a QEMU/KVM virtual machine, without the reference hardware: the image boots with UEFI firmware (OVMF) and an empty USB 3 controller, and emulated USB keys (disk images) are inserted and removed while the kiosk runs, which triggers the same udev events as a real device. Hostile samples are only ever copied into key images, never onto the development desktop. See [docs/vm.md](docs/vm.md) for the automated test and the interactive virtual machine.

Tasks:

- [x] Test image profile: test-only signatures that detect the test corpus, root shell on the virtio console
- [x] Automated end-to-end test in the virtual machine: insertion through QMP, verdicts of every engine, cleaning confirmed on the kiosk screen, quarantine, report, eject, removal, clean key inserted again
- [x] Read-only root filesystem, data partition grown to fill the disk, data kept after a reboot
- [x] OVMF with the image signing keys enrolled: Secure Boot enabled, kernel lockdown, root filesystem on dm-verity; a modified root block cannot be read; firmware trusting another key refuses the image
- [x] USB key images for every supported filesystem (vfat, exfat, ntfs3, ext4), plus partitioned, unsupported and corrupted filesystems
- [x] An emulated keyboard (`usb-kbd`) and network adapter (`usb-net`) are blocked by USBGuard: no input device, no network interface
- [x] No outgoing network outside the update channel
- [x] A/B update and rollback tested in the virtual machine
- [x] Run in continuous integration (KVM when nested virtualization is available, TCG emulation otherwise)
- [ ] Software TPM (`swtpm`), once measured boot is used

## Phase 3 — Kiosk interface

### Current status

The kiosk draws a graphical interface on its touch screen: waiting for a key, scan progress (files scanned, threats found, latest files and their verdict), the dangerous files listed before they are removed, a touch anywhere on the screen to clean, then the result. It is drawn with Pillow straight into the framebuffer, without a display server or a browser. An administrator customizes it with a theme built into the image: logo, colors, texts and images. See [docs/interface.md](docs/interface.md).

- [x] Graphical interface drawn into the framebuffer (Pillow), console in graphics mode: no display server, browser or local web server (the earlier plan, FastAPI + htmx in `cage` + Chromium, would have added a browser and a socket to the image)
- [x] User flow: insertion, progress, dangerous files listed, cleaning on a touch of the screen (no buttons), verdict
- [x] Touch-first interface designed for the 7-inch screen (1024x600), drawn for the size of any screen
- [x] Customization by the administrator: theme folder built into the image (logo, colors, texts of every screen, images, fonts), checked at build time, `pandora-box-theme preview` to see the screens without a kiosk
- [x] Internationalization: FR and EN built in, chosen with `display.language`, a theme can add a language
- [ ] German and Dutch texts built in
- [ ] Accessibility (contrast, font size)
- [ ] Protected administrator mode (curses or web): scan reports, signature status

## Phase 4 — Advanced analysis

- [x] PDF: JavaScript and Launch actions are suspicious, automatic actions, embedded files and forms reported (like `pdfid`, `engines.heuristics`)
- [x] Archives: unpacked and scanned by ClamAV within its limits; beyond them (decompression bombs) the file is not fully scanned
- [x] Encrypted archives and documents flagged as unscannable (ClamAV `AlertEncrypted`)
- [x] Detection of programs disguised as documents: extension / real type mismatch, double extension (`engines.heuristics`)
- [ ] Unique identifier of each key for the file transfer stations: the USB serial number is missing or shared on some keys, so at the first insertion of a verified key the kiosk writes a token, a random identifier (UUID) signed with a key of the kiosks of the organization (Ed25519, systemd credential of the image), in a hidden file `.pandora-box` at the root of the key (with the hidden attribute where the filesystem and its Linux driver allow it), if the file does not exist; on a key with several partitions, on the first supported one. The file is read as hostile data (size limit, strict format) and its signature is verified: when the token is malformed or not signed by the kiosks, the error is logged and the kiosk replaces it with a new token (a new identifier, with an empty outbound directory). The signature prevents forging an identifier, not copying the file to another key
- [ ] Copy of the clean files of the key to a network share (SFTP, SCP over SSH or SMB), configured on the kiosk: only a verified key is handled (see Decisions); from it, only the clean files are copied, never the malicious or suspicious ones; one directory per key, named after its identifier, with one subdirectory per scan named after the `report_id` of the report. With this option, the kiosk becomes an inbound file transfer station: the key is not cleaned, the identifier file is the only file written on it
- [ ] Outbound file transfer station: copy files from a network share (SFTP, SCP over SSH or SMB), configured on the kiosk, to the inserted key; the key is scanned first, only a verified key without malicious files, nor suspicious files when they are removed (`scan.suspicious = "block"`), receives files (see Decisions), and it must stay in the kiosk from the scan to the end of the copy (removed, it is scanned again at its next insertion), the files are scanned before being written. After the scan, the kiosk compares the outbound directory of the key on the server (named after its identifier) with the key: the files that are new or modified since (size, modification time) are copied from the server to the key. The free space of the key is checked before the copy, and the differences between filesystems are handled: maximum file size (4 GiB on vfat), forbidden characters, name length and case-insensitive names (vfat, exfat, ntfs3); a file that cannot be written is reported, never renamed silently
- [-] Selective copy of clean files only to a second device (rejected option)
- [-] Reformatting option for the cleaning: the kiosk recreates the filesystem of the key and writes back only the clean files, so that the next computer no longer mounts a structure made by the attacker (rejected option)
- [-] Office documents: `oletools` (olevba, mraptor); (rejected option) documents with macros are already suspicious (ClamAV `AlertOLE2Macros`)
- [ ] Executables: `capa` (optional, can be disabled for performance)
- [ ] **Content disarm and reconstruction (CDR)** to a trusted device using Dangerzone

## Phase 5 — Fleet management and traceability

- [x] Log export as syslog / JSON to a SIEM (Wazuh, etc.): `pandora-box-siem` service of the image profile `online`, syslog RFC 5424 or JSON over TLS, TCP or UDP, the log file as queue ([docs/siem.md](docs/siem.md))
- [ ] Printed or exported scan receipt for the user
- [ ] Optional central server: kiosk inventory, reports, signature status
- [ ] Centralized distribution of signed updates
- [ ] Statistics dashboard (devices scanned, detections)

## Phase 6 — Assurance and compliance

- [x] Detection regression test corpus (`tests/corpus/`): checked with the fake engine in every test run, with the real engines in the integration tests
- [ ] Documented threat model
- [ ] Deployment guide: kiosk placement, user procedure, associated USB policy
- [ ] Fuzzing of entry points (mounting, metadata parsing)
- [ ] External security audit of the image
- [ ] Mapping to NIS2 and ISO 27001 requirements (control 7.10 "Storage media")

---

## Out of scope (for now)

- Workstation agent blocking devices that have not been scanned
- Dynamic analysis (execution sandbox)
- Encrypted devices (BitLocker, VeraCrypt)
- Smartphones and MTP devices
- `.deb` package installable on an existing system: PandoraBox is only distributed as a complete hardened image

## Decisions

- Policy for a "suspicious" verdict: configurable with `scan.suspicious`, `block` (default) or `warn`.
- Policy for files that could not be fully scanned (engine error or timeout, limits): configurable with `scan.on_error`. `block` (default) reports the device as not verified; unscanned files are listed but never removed, since they are not known to be malicious.
- A Hashlookup hit means "known file", not "benign file": content engines also scan known files by default (the CIRCL filter lists the EICAR test file, tools and malware; `engines.hashlookup.skip_content_engines` skips them, opt-in), a malicious hash always wins, and the decision is recorded in the scan report.
- YARA Forge already includes signature-base: only YARA Forge `core` is configured by default, signature-base is opt-in.
- The file transfer stations (inbound and outbound) only handle a verified key: a key the kiosk could scan entirely. From such a key, the inbound station copies the clean files only, never the malicious or suspicious ones; the outbound station refuses it as long as it holds a file the cleaning would remove: a malicious file, or a suspicious file when the suspicious files are removed (`scan.suspicious = "block"`, the default; with `"warn"`, they stay on the key and do not prevent the copy). The key must be cleaned first (by a kiosk that cleans, not an inbound station, which never cleans), then inserted again. A key not verified is refused, without copy in either direction and without identifier written on it. An ext4 key whose journal needs a replay (removed without being ejected) is not verified: the kiosk never mounts it, neither read-only to scan it (it would see other files than a computer that replays the journal) nor read-write to write files on it (the kernel would replay a journal given by the device); the kiosk asks to eject it properly from a computer, then to insert it again. Reformatting such a key is not offered: its identifier could not be read, and the key would lose its outbound files.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, follow [SECURITY.md](SECURITY.md).

## License

PandoraBox is free software, licensed under the [GNU General Public License v3.0](LICENSE).
