"""
package_index.py — Fetch and parse the Wolfi OS APKINDEX

The Wolfi package index lives at:
    https://packages.wolfi.dev/os/<arch>/APKINDEX.tar.gz

It is a gzipped tarball containing a single file called APKINDEX. That file
is plain text: key:value pairs one per line, entries separated by blank lines.
Single-letter keys map to package fields (P=name, V=version, T=description…).

This module exposes three public functions:

    fetch_raw(arch, base_url)  → (raw_text, etag)
        Downloads and decompresses the index. Returns the raw text and the
        HTTP ETag header. The ETag is stored by the caller and passed to the
        watcher later so it can detect index changes without re-downloading
        the whole file every time.

    parse(raw_text)            → list[Package]
        Turns the raw text into Package dataclass instances. Pure function —
        no network calls, easy to unit test with a fixture string.

    fetch(arch, base_url)      → (list[Package], etag)
        Convenience wrapper: fetch_raw → parse → deduplicate. This is the
        entry point most callers will use.

Supported architectures: x86_64, aarch64
"""

import io
import tarfile
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# Default upstream Wolfi repository. Override via base_url argument to point
# at the local apk-server proxy instead (useful in offline/air-gapped mode).
WOLFI_BASE_URL = "https://packages.wolfi.dev/os"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Package:
    """
    A single package entry from the APKINDEX.

    Fields match the single-letter keys in the index:
        P → name            V → version         A → arch
        T → description     L → license         o → origin
        D → dependencies    p → provides        C → checksum
        S → size            I → installed_size
    """
    name:           str
    version:        str
    arch:           str
    description:    str
    license:        str
    # origin is the source package name — e.g. "curl" for curl, curl-dev,
    # curl-doc. Useful for grouping related packages.
    origin:         str
    # dependencies and provides are space-separated in the raw index;
    # we split them into lists here for easier consumption.
    dependencies:   list[str] = field(default_factory=list)
    provides:       list[str] = field(default_factory=list)
    checksum:       str = ""
    # sizes are in bytes
    size:           int = 0
    installed_size: int = 0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse(raw: str) -> list[Package]:
    """
    Parse raw APKINDEX text into a list of Package objects.

    The format is:
        C:Q1checksum...
        P:package-name
        V:1.2.3-r0
        ...blank line separates entries...

    This is a pure function with no side effects — pass it any string that
    follows the APKINDEX format and it will return the corresponding packages.
    """
    packages: list[Package] = []
    current:  dict[str, str] = {}

    for line in raw.splitlines():
        if not line.strip():
            # blank line = end of current entry
            if current:
                packages.append(_fields_to_package(current))
                current = {}
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()

    # catch a final entry if the file doesn't end with a trailing blank line
    if current:
        packages.append(_fields_to_package(current))

    return packages


def _fields_to_package(f: dict[str, str]) -> Package:
    """Convert a raw field dict into a Package. Not part of the public API."""
    return Package(
        name           = f.get("P", ""),
        version        = f.get("V", ""),
        arch           = f.get("A", ""),
        description    = f.get("T", ""),
        license        = f.get("L", ""),
        origin         = f.get("o", ""),
        dependencies   = f.get("D", "").split() if f.get("D") else [],
        provides       = f.get("p", "").split() if f.get("p") else [],
        checksum       = f.get("C", ""),
        size           = int(f.get("S", 0)),
        installed_size = int(f.get("I", 0)),
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_raw(
    arch:     str = "x86_64",
    base_url: str = WOLFI_BASE_URL,
) -> tuple[str, Optional[str]]:
    """
    Download APKINDEX.tar.gz for the given architecture and return
    (raw_index_text, etag).

    The ETag is an opaque string the server uses to identify this specific
    version of the index file. Store it alongside the parsed data and compare
    it on the next poll — if it hasn't changed, skip the download entirely.
    If the server doesn't return an ETag, this returns None.

    Raises urllib.error.URLError on network failure.
    """
    url = f"{base_url}/{arch}/APKINDEX.tar.gz"

    with urllib.request.urlopen(url) as resp:
        etag: Optional[str] = resp.headers.get("ETag")
        data: bytes = resp.read()

    # The tarball contains a single file called APKINDEX. Extract it in-memory
    # to avoid writing a temp file to disk.
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.extractfile("APKINDEX")
        if member is None:
            raise ValueError("APKINDEX not found inside APKINDEX.tar.gz")
        raw = member.read().decode("utf-8")

    return raw, etag


def fetch(
    arch:     str = "x86_64",
    base_url: str = WOLFI_BASE_URL,
) -> tuple[list[Package], Optional[str]]:
    """
    Fetch and parse the Wolfi package index. Returns (packages, etag).

    Packages are deduplicated by name. The Wolfi index normally lists each
    package name exactly once, but if duplicates appear (e.g. from a custom
    mirror with overlapping entries) the highest version wins.

    The etag should be persisted by the caller for change detection:

        packages, etag = fetch()
        # ... later ...
        _, new_etag = fetch_raw()
        if new_etag != etag:
            packages, etag = fetch()   # re-parse only when index changed
    """
    raw, etag = fetch_raw(arch, base_url)
    packages  = parse(raw)
    unique    = _deduplicate(packages)
    return unique, etag


def _deduplicate(packages: list[Package]) -> list[Package]:
    """
    Keep one entry per package name. If a name appears more than once,
    the entry with the lexicographically higher version string wins.

    APK version strings (e.g. "1.2.3-r4") sort correctly with plain string
    comparison for the dedup use-case — we are not doing semver resolution.
    """
    seen: dict[str, Package] = {}
    for pkg in packages:
        existing = seen.get(pkg.name)
        if existing is None or pkg.version > existing.version:
            seen[pkg.name] = pkg
    return list(seen.values())
