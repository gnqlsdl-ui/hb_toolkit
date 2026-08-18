"""Build a Blender Extension zip into ``release/``.

The project root IS the extension package. The archive is staged so its
top-level directory is ``hb_toolkit/`` containing ``blender_manifest.toml``
plus all Python modules.

Reads version from ``blender_manifest.toml`` and produces
``release/hb_toolkit_v{MAJOR}.{MINOR}.{PATCH}.zip``.

Usage:
    python build.py
    python build.py --index ARCHIVE_URL [--zip PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Iterator

PACKAGE_NAME = "hb_toolkit"
ROOT = Path(__file__).resolve().parent
RELEASE_DIR = ROOT / "release"
MANIFEST = ROOT / "blender_manifest.toml"

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".cursor", "release", "docs", ".vscode", ".idea",
    "reference", "scripts", ".github",
}
EXCLUDE_FILES = {"build.ps1", "build.py", "README.md", ".gitignore"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip"}

_VERSION_RE = re.compile(r'^\s*version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', re.MULTILINE)


def read_version() -> tuple[int, int, int]:
    """Parse ``version`` from ``blender_manifest.toml``."""
    if not MANIFEST.is_file():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST}")
    text = MANIFEST.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text)
    if not m:
        raise RuntimeError(f"'version' not found in {MANIFEST}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def read_manifest() -> dict:
    with MANIFEST.open("rb") as fh:
        return tomllib.load(fh)


def iter_addon_files() -> Iterator[tuple[Path, Path]]:
    """Yield (absolute_path, archive_path) for every file to include.

    ``archive_path`` is prefixed with ``hb_toolkit/`` so the zip's top-level
    folder matches the package name (required by Blender).
    """
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if path.name in EXCLUDE_FILES:
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        yield path, Path(PACKAGE_NAME) / rel


def build() -> Path:
    version = read_version()
    version_str = ".".join(str(v) for v in version)
    RELEASE_DIR.mkdir(exist_ok=True)
    zip_path = RELEASE_DIR / f"{PACKAGE_NAME}_v{version_str}.zip"

    if zip_path.exists():
        zip_path.unlink()

    files = list(iter_addon_files())
    if not any(arc.name == "blender_manifest.toml" for _, arc in files):
        raise RuntimeError("blender_manifest.toml is missing from the package")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in files:
            zf.write(src, arc.as_posix())

    return zip_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_index(zip_path: Path, archive_url: str, out_path: Path) -> Path:
    """Write a Blender static-extensions ``index.json`` for one zip."""
    manifest = read_manifest()
    entry = {
        "schema_version": manifest.get("schema_version", "1.0.0"),
        "id": manifest["id"],
        "name": manifest["name"],
        "tagline": manifest.get("tagline", ""),
        "version": manifest["version"],
        "type": manifest.get("type", "add-on"),
        "archive_size": zip_path.stat().st_size,
        "archive_hash": "sha256:" + sha256_file(zip_path),
        "blender_version_min": manifest["blender_version_min"],
        "maintainer": manifest.get("maintainer", ""),
        "tags": manifest.get("tags", []),
        "license": manifest.get("license", []),
        "archive_url": archive_url,
    }
    index = {"version": "v1", "blocklist": [], "data": [entry]}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build HB Toolkit extension zip")
    parser.add_argument(
        "--index",
        metavar="ARCHIVE_URL",
        help="Write Blender index.json for the built zip, using this download URL",
    )
    parser.add_argument("--zip", dest="zip_path", help="Existing zip to index")
    parser.add_argument(
        "--out",
        default=str(ROOT / "extensions" / "index.json"),
        help="index.json output path",
    )
    args = parser.parse_args()

    try:
        if args.index:
            zip_path = Path(args.zip_path) if args.zip_path else build()
            out = write_index(zip_path, args.index, Path(args.out))
            print(f"[index] OK -> {out}")
            return 0
        out = build()
        version = ".".join(str(v) for v in read_version())
    except Exception as exc:
        print(f"[build] FAILED: {exc}", file=sys.stderr)
        return 1
    size_kb = out.stat().st_size / 1024
    print(f"[build] OK -> {out.relative_to(ROOT)} ({size_kb:.1f} KB)  [Extension v{version}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
