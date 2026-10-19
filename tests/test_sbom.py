"""SBOM of an image (image/sbom.py)."""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "sbom", Path(__file__).parent.parent / "image" / "sbom.py"
)
assert _SPEC is not None and _SPEC.loader is not None
sbom = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sbom)

MANIFEST = {
    "manifest_version": 1,
    "config": {
        "name": "pandora-box",
        "distribution": "debian",
        "architecture": "x86-64",
        "version": "20261019000000",
        "release": "trixie",
    },
    "packages": [
        {"type": "deb", "name": "libcap2", "version": "1:2.75-10+deb13u1", "architecture": "amd64"},
        {"type": "deb", "name": "adduser", "version": "3.152", "architecture": "all"},
    ],
}
REQUIREMENTS = """\
pillow==12.3.0 \\
    --hash=sha256:00808c5e14ef63ac5161091d242999076604ff74b883423a11e5d7bbb38bf756
    # via pandora-box
python_magic==0.4.27 \\
    --hash=sha256:00808c5e14ef63ac5161091d242999076604ff74b883423a11e5d7bbb38bf756
"""
PROJECT = {"name": "pandora-box", "version": "1.0.0", "license": "GPL-3.0-or-later"}


def test_components() -> None:
    document = sbom.sbom(MANIFEST, REQUIREMENTS, PROJECT, "abc123", 1760832000)
    purls = [c["purl"] for c in document["components"]]
    assert purls == sorted(purls)
    assert "pkg:deb/debian/libcap2@1%3A2.75-10%2Bdeb13u1?arch=amd64&distro=debian-trixie" in purls
    assert "pkg:pypi/python-magic@0.4.27" in purls
    assert "pkg:pypi/pillow@12.3.0" in purls
    assert "pkg:github/sourcentis/pandora-box@abc123" in purls
    assert len(purls) == 5
    assert document["metadata"]["timestamp"] == "2025-10-19T00:00:00Z"
    assert document["metadata"]["component"]["version"] == "20261019000000"
    assert len(document["dependencies"][0]["dependsOn"]) == 5


def test_reproducible(tmp_path: Path) -> None:
    """The same image gives the same SBOM."""
    manifest = tmp_path / "pandora-box.manifest"
    manifest.write_text(json.dumps(MANIFEST))
    outputs = []
    for name in ("a.json", "b.json"):
        output = tmp_path / name
        assert sbom.main([str(manifest), str(output), "--commit", "abc", "--epoch", "1"]) == 0
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
