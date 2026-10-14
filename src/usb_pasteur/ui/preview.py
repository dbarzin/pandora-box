"""usb-pasteur-theme: check a theme and draw its screens, without a kiosk."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from usb_pasteur.device import UsbDevice
from usb_pasteur.engines import EngineResult, Verdict
from usb_pasteur.results import FileResult
from usb_pasteur.ui.base import Screen
from usb_pasteur.ui.screens import REFERENCE, Renderer, ViewState
from usb_pasteur.ui.theme import ThemeError, load_theme, theme_languages


def sample_states() -> dict[str, ViewState]:
    """Every screen, with made-up files."""

    def result(path: str, verdict: Verdict, detection: str = "", error: str = "") -> FileResult:
        engine = EngineResult(
            "clamav", verdict, (detection,) if detection else (), error=error or None
        )
        return FileResult(
            Path("/media/usb-pasteur", path), 52_000, verdict, (engine,), rel_path=path
        )

    threats = (
        result("Factures/facture-2026-10.pdf.exe", Verdict.MALICIOUS, "Win.Trojan.Agent-1234"),
        result("eicar.com", Verdict.MALICIOUS, "Eicar-Signature"),
        result("Documents/budget.xlsm", Verdict.SUSPICIOUS, "Heuristics.OLE2.ContainsMacros"),
    )
    unscanned = (result("archive-protégée.zip", Verdict.ERROR, error="Heuristics.Encrypted.Zip"),)
    recent = (
        threats[0],
        result("Factures/facture-2026-09.pdf", Verdict.CLEAN),
        result("Photos/vacances/IMG_2041.jpg", Verdict.CLEAN),
        result("Photos/vacances/IMG_2040.jpg", Verdict.CLEAN),
        result("Notes de réunion.docx", Verdict.CLEAN),
    )
    device = UsbDevice("/dev/sda1", "vfat", "CLE-USB", "Cruzer Blade", "4C53", "SanDisk")
    info = (
        "USB-Pasteur 20261014120000 of 2026-10-14 12:00 UTC",
        "Signatures: set 42 of 2026-10-14 06:00 UTC",
        "Engines: malwarebazaar, hashlookup, clamav (1.4.3), yara (1.21.0), heuristics",
    )
    scanning = ViewState(
        Screen.SCANNING,
        device=device,
        usage=(16_008_609_792, 2_254_857_830),
        percent=37,
        done=152,
        total=411,
        threats=1,
        recent=recent,
    )
    return {
        "ready": ViewState(Screen.READY, info=info),
        "unavailable": ViewState(
            Screen.UNAVAILABLE,
            messages=(
                "NO VALID SIGNATURES: no signature set installed",
                "This kiosk cannot scan: insert a signature update device.",
            ),
        ),
        "preparing": ViewState(Screen.SCANNING, device=device),
        "scanning": scanning,
        "threats": ViewState(Screen.THREATS, files=threats),
        "cleaning": ViewState(
            Screen.CLEANING, messages=tuple(f"{f.rel_path} removed" for f in threats[:2])
        ),
        "clean": ViewState(Screen.CLEAN),
        "warning": ViewState(Screen.WARNING, files=threats[2:] + unscanned),
        "cleaned": ViewState(Screen.CLEANED, files=threats),
        "not_cleaned": ViewState(Screen.NOT_CLEANED, files=threats[:1]),
        "not_verified": ViewState(Screen.NOT_VERIFIED, files=unscanned),
        "error": ViewState(Screen.ERROR, messages=("Cannot mount device: filesystem not allowed",)),
        "service": ViewState(
            Screen.SERVICE,
            messages=(
                "Signature update device: verifying the signature set...",
                "Signatures updated: set 43",
                "A signature update device is not scanned. Remove the device.",
            ),
        ),
    }


def _size(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", value)
    if match is None or not (320 <= int(match[1]) <= 7680 and 240 <= int(match[2]) <= 4320):
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT, e.g. 1024x600")
    return int(match[1]), int(match[2])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="usb-pasteur-theme", description="Check a kiosk theme and draw its screens"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="check a theme folder")
    preview = sub.add_parser("preview", help="draw every screen into PNG files")
    for command in (check, preview):
        command.add_argument("theme", type=Path, nargs="?", help="theme folder (default: built-in)")
        command.add_argument("--language", default="", help="language of the texts (default: all)")
    preview.add_argument("output", type=Path, help="folder of the PNG files")
    preview.add_argument(
        "--size", type=_size, default=REFERENCE, help="screen size (default: 1024x600)"
    )
    args = parser.parse_args(argv)

    try:
        # Every language of the theme, unless one is given
        languages = [args.language] if args.language else theme_languages(args.theme)
        themes = [load_theme(args.theme, language) for language in languages]
    except ThemeError as ex:
        print(f"usb-pasteur-theme: {ex}", file=sys.stderr)
        return 2
    if args.command == "check":
        print(f"{args.theme or 'built-in theme'}: OK ({', '.join(languages)})")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    for theme in themes:
        renderer = Renderer(theme, args.size)
        for name, state in sample_states().items():
            frame = renderer.render(state)
            path = args.output / f"{theme.language}-{name}.png"
            frame.image(0.0).save(path)
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
