"""Software bill of materials of an image, in the CycloneDX 1.5 JSON format.

    python3 image/sbom.py MANIFEST OUTPUT --commit COMMIT --epoch SECONDS

MANIFEST: the package list mkosi writes next to the image (.manifest). The
SBOM lists the Debian packages of the image, the Python packages of its
virtual environment (image/requirements.txt) and PandoraBox itself. It only
depends on its inputs: the same image gives the same SBOM (image/release.sh).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPOSITORY = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPOSITORY / "image" / "requirements.txt"
PYPROJECT = REPOSITORY / "pyproject.toml"
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", re.MULTILINE)


def _purl_version(version: str) -> str:
    # A purl percent-encodes the epoch colon and the plus of Debian versions
    return quote(version, safe=".-_~")


def debian_packages(manifest: dict[str, Any], release: str) -> list[dict[str, Any]]:
    components = []
    for package in manifest["packages"]:
        if package.get("type") != "deb":
            continue
        name, version, arch = package["name"], package["version"], package["architecture"]
        purl = f"pkg:deb/debian/{name}@{_purl_version(version)}?arch={arch}&distro=debian-{release}"
        components.append(
            {"type": "library", "bom-ref": purl, "name": name, "version": version, "purl": purl}
        )
    return components


def python_packages(requirements: str) -> list[dict[str, Any]]:
    components = []
    for name, version in _PIN.findall(requirements):
        name = re.sub(r"[-_.]+", "-", name).lower()
        purl = f"pkg:pypi/{name}@{_purl_version(version)}"
        components.append(
            {"type": "library", "bom-ref": purl, "name": name, "version": version, "purl": purl}
        )
    return components


def sbom(manifest: dict[str, Any], requirements: str, project: dict[str, Any],
         commit: str, epoch: int) -> dict[str, Any]:  # fmt: skip
    config = manifest["config"]
    image_version = str(config["version"])
    components = debian_packages(manifest, str(config["release"]))
    components += python_packages(requirements)
    purl = f"pkg:github/sourcentis/pandora-box@{commit}"
    components.append({
        "type": "application", "bom-ref": purl, "name": project["name"],
        "version": project["version"], "purl": purl,
        "licenses": [{"license": {"id": project["license"]}}],
    })  # fmt: skip
    components.sort(key=lambda c: str(c["bom-ref"]))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        # Derived from the image: the same image gives the same SBOM
        "serialNumber": uuid.uuid5(uuid.NAMESPACE_URL, f"{purl}#{image_version}").urn,
        "version": 1,
        "metadata": {
            "timestamp": datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z"),
            "component": {
                "type": "operating-system",
                "bom-ref": "pandora-box-image",
                "name": "pandora-box",
                "version": image_version,
                "description": f"PandoraBox image, Debian {config['release']}"
                f" {config['architecture']}",
            },
        },
        "components": components,
        "dependencies": [
            {"ref": "pandora-box-image", "dependsOn": [c["bom-ref"] for c in components]}
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SBOM of a PandoraBox image (CycloneDX)")
    parser.add_argument("manifest", type=Path, help="package list of the image (mkosi)")
    parser.add_argument("output", type=Path)
    parser.add_argument("--commit", required=True, help="commit the image was built from")
    parser.add_argument("--epoch", type=int, required=True, help="SOURCE_DATE_EPOCH of the build")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text())
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    document = sbom(manifest, REQUIREMENTS.read_text(), project, args.commit, args.epoch)
    args.output.write_text(json.dumps(document, indent=1) + "\n")
    print(f"{args.output}: {len(document['components'])} components")
    return 0


if __name__ == "__main__":
    sys.exit(main())
