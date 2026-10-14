# Security Policy

PandoraBox is a USB decontamination kiosk designed for sensitive environments. We take security issues seriously and appreciate responsible disclosure.

## Supported versions

PandoraBox is under active development and has not yet reached a stable release. Security fixes are applied to the `main` branch and to the latest published image.

| Version | Supported |
|---|---|
| `main` / latest release | Yes |
| Older releases | No |
| PandoraBox v1 (based on Pandora) | No |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions or pull requests.**

Report them privately using GitHub's [private vulnerability reporting](https://github.com/dbarzin/pandora-box/security/advisories/new) (Security tab → "Report a vulnerability").

Please include as much of the following as possible:

- the type of issue (e.g. sandbox escape, malware not detected, privilege escalation, BadUSB bypass, signature update tampering),
- the affected component (orchestrator, detection engine, image, update channel, interface) and version or commit,
- the hardware or environment used,
- step-by-step instructions to reproduce the issue,
- a proof of concept, if available,
- the potential impact.

**Do not send live malware samples.** Provide hashes (SHA-256), a harmless reproducer, or ask us for a secure way to transfer a sample.

## What to expect

- **Acknowledgment** within 5 business days.
- **Initial assessment** within 15 days, including whether the report is accepted.
- **Fix and disclosure**: we aim to release a fix within 90 days, coordinated with the reporter. A GitHub Security Advisory (and a CVE when relevant) will be published once a fix is available.
- **Credit**: reporters are credited in the advisory unless they prefer to remain anonymous.

## Scope

In scope:

- the `pandora_box` orchestrator and its detection engine plugins,
- the hardened system image (build configuration, services, sandboxing, USBGuard, mount options),
- the signature update channel (online and offline) and its signature verification,
- the kiosk and administrator interfaces.

Out of scope:

- vulnerabilities in upstream projects (ClamAV, YARA-X, Debian packages, etc.) — please report them upstream; let us know if PandoraBox needs to react,
- malware that no integrated engine detects, unless it results from a PandoraBox defect (e.g. files silently skipped, wrong verdict aggregation),
- attacks requiring physical access beyond the exposed scanning USB ports (opening the enclosure, BIOS access on a deployment that ignores the hardening guide),
- denial of service from a deliberately oversized device, when the configured limits behave as documented.

## Safe harbor

We will not pursue legal action against researchers who act in good faith, avoid privacy violations and service disruption, test only on systems they own or are authorized to test, and give us reasonable time to fix the issue before public disclosure.
