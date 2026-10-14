"""Heuristics on the structure of a file, without signatures.

- **PDF with active content**: a PDF whose objects hold JavaScript or a
  Launch action (what pdfid looks for). The names are decoded, a PDF can hide
  them (/J#61vaScript). Names inside compressed object streams are not seen:
  ClamAV and YARA look there.
- **Disguised executable**: a program (PE, ELF, Mach-O, MSI) whose extension
  claims a document (facture.pdf), or a file name with a document extension
  followed by a program one (facture.pdf.exe).

A finding is suspicious, never malicious: these files are often legitimate
(PDF forms with JavaScript). The names of PDF objects that matter (automatic
actions, embedded files, forms) are reported as facts.
"""

from __future__ import annotations

import os
import posixpath
import re

from pandora_box.engines.base import Engine, EngineResult, FileInfo, Verdict

# PDF names: JavaScript and Launch actions are suspicious, the others facts
PDF_ACTIVE = {"JavaScript": "JavaScript", "JS": "JavaScript", "Launch": "Launch"}
PDF_FACTS = ("OpenAction", "AA", "EmbeddedFile", "RichMedia", "XFA", "AcroForm")
_PDF_NAME = re.compile(rb"/([A-Za-z0-9#]{1,64})")
_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_CHUNK = 4 * 1024 * 1024
# A name split between two chunks is read again in the next one
_OVERLAP = 256

EXECUTABLE_TYPES = frozenset(
    {
        "application/x-dosexec",
        "application/vnd.microsoft.portable-executable",
        "application/x-msdownload",
        "application/x-executable",
        "application/x-pie-executable",
        "application/x-sharedlib",
        "application/x-mach-binary",
        "application/x-msi",
    }
)
# Extensions under which a program is expected
EXECUTABLE_EXTENSIONS = frozenset(
    {
        "", ".exe", ".dll", ".sys", ".drv", ".ocx", ".cpl", ".scr", ".com", ".efi", ".msi",
        ".mui", ".ax", ".so", ".o", ".ko", ".bin", ".elf", ".run", ".appimage", ".dylib",
        ".bundle", ".node", ".pyd", ".mex", ".mexw64", ".xll", ".wll", ".sfx", ".tmp",
    }
)  # fmt: skip
# Extensions that a user opens as a document
DOCUMENT_EXTENSIONS = frozenset(
    {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
        ".rtf", ".txt", ".csv", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".mp3",
        ".mp4", ".avi", ".mov", ".wav", ".zip", ".html", ".htm",
    }
)  # fmt: skip
# Extensions of what Windows runs when the file is opened
RUN_EXTENSIONS = frozenset(
    {
        ".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".js", ".jse", ".vbs", ".vbe",
        ".wsf", ".wsh", ".ps1", ".hta", ".lnk", ".jar", ".msi", ".cpl", ".reg",
    }
)  # fmt: skip


class HeuristicsEngine(Engine):
    name = "heuristics"
    timeout = 60.0

    def version(self) -> str:
        return "1"

    def scan(self, file: FileInfo) -> EngineResult:
        findings: list[str] = []
        facts: dict[str, str | bool | int | float] = {}
        findings += disguised(file.name, file.mime)
        head = os.pread(file.fd, 1024, 0)
        if b"%PDF-" in head:
            names = pdf_names(file.fd)
            findings += sorted(
                {f"Heuristics.PDF.{PDF_ACTIVE[n]}" for n in names if n in PDF_ACTIVE}
            )
            for name in PDF_FACTS:
                if names.get(name):
                    facts[f"pdf_{name}"] = names[name]
        if findings:
            return EngineResult(self.name, Verdict.SUSPICIOUS, tuple(findings), facts=facts)
        return EngineResult(self.name, Verdict.CLEAN, facts=facts)


def disguised(name: str, mime: str | None) -> list[str]:
    """Findings about a program hidden behind the name of a document."""
    base, extension = posixpath.splitext(name.lower())
    findings = []
    if mime in EXECUTABLE_TYPES and extension not in EXECUTABLE_EXTENSIONS:
        findings.append("Heuristics.Executable.Disguised")
    previous = posixpath.splitext(base)[1]
    if extension in RUN_EXTENSIONS and previous in DOCUMENT_EXTENSIONS:
        findings.append("Heuristics.DoubleExtension")
    return findings


def pdf_names(fd: int) -> dict[str, int]:
    """Count the names of the PDF objects that matter, decoded (#xx escapes).

    The file is read in chunks; a name that starts in the last _OVERLAP bytes
    of a chunk (it may go on in the next one) is counted with the next chunk.
    """
    wanted = set(PDF_ACTIVE) | set(PDF_FACTS)
    counts: dict[str, int] = {}
    offset = 0
    tail = b""
    while chunk := os.pread(fd, _CHUNK, offset):
        offset += len(chunk)
        data = tail + chunk
        cut = len(data) if len(chunk) < _CHUNK else len(data) - _OVERLAP
        for match in _PDF_NAME.finditer(data):
            if match.start() >= cut:
                break
            raw = match.group(1)
            if b"#" in raw:
                raw = _HEX.sub(lambda m: bytes([int(m.group(1), 16)]), raw)
            name = raw.decode("latin-1")
            if name in wanted:
                counts[name] = counts.get(name, 0) + 1
        tail = data[cut:]
    return counts
