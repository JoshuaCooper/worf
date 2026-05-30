#!/usr/bin/env python3
"""
seed_local_repo.py — Populate infra/packages/ with a small set of Wolfi packages.

Creates:
  infra/packages/os/wolfi-signing.rsa.pub
  infra/packages/os/x86_64/<package>.apk  (for each seed package)
  infra/packages/os/x86_64/APKINDEX.tar.gz (filtered to only local packages)

After running, the apk-server nginx container will serve these files directly
(nginx tries local disk first, proxies upstream only on a miss).

Usage:
    python infra/support_files/seed_local_repo.py
"""

import io
import os
import sys
import tarfile
import urllib.request

BASE_URL = "https://packages.wolfi.dev/os"
ARCH     = "x86_64"

REPO_DIR = os.path.join(os.path.dirname(__file__), "..", "packages")
PKG_DIR  = os.path.join(REPO_DIR, "os", ARCH)
KEY_DIR  = os.path.join(REPO_DIR, "os")

SEED_PACKAGES = [
    "wolfi-baselayout",
    "busybox",
    "curl",
    "wget",
    "ca-certificates",
]


# ── Index helpers ──────────────────────────────────────────────────────────────

def fetch_index_raw() -> str:
    url = f"{BASE_URL}/{ARCH}/APKINDEX.tar.gz"
    print(f"Fetching upstream index: {url}")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.extractfile("APKINDEX")
        return member.read().decode("utf-8")


def parse_entries(raw: str) -> list[dict]:
    entries, current = [], {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
        elif ":" in line:
            k, _, v = line.partition(":")
            current[k.strip()] = v.strip()
    if current:
        entries.append(current)
    return entries


def entries_to_tar(entries: list[dict]) -> bytes:
    text = "".join(
        "\n".join(f"{k}:{v}" for k, v in e.items()) + "\n\n"
        for e in entries
    ).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="APKINDEX")
        info.size = len(text)
        tar.addfile(info, io.BytesIO(text))
    return buf.getvalue()


# ── Download helpers ───────────────────────────────────────────────────────────

def download(url: str, dest: str, label: str = ""):
    tag = label or os.path.basename(dest)
    if os.path.exists(dest):
        print(f"  skip  {tag} (already exists)")
        return
    print(f"  fetch {tag}")
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
    with open(dest, "wb") as f:
        f.write(data)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(PKG_DIR, exist_ok=True)
    os.makedirs(KEY_DIR, exist_ok=True)

    # Signing key
    download(
        f"{BASE_URL}/wolfi-signing.rsa.pub",
        os.path.join(KEY_DIR, "wolfi-signing.rsa.pub"),
        "wolfi-signing.rsa.pub",
    )

    # Parse upstream index
    raw     = fetch_index_raw()
    entries = parse_entries(raw)
    print(f"Upstream index: {len(entries)} packages")

    # Select latest version of each seed package
    selected: dict[str, dict] = {}
    for entry in entries:
        name = entry.get("P", "")
        if name in SEED_PACKAGES:
            existing = selected.get(name)
            if existing is None or entry.get("V", "") > existing.get("V", ""):
                selected[name] = entry

    missing = set(SEED_PACKAGES) - set(selected)
    if missing:
        print(f"Warning: not found in index: {', '.join(missing)}", file=sys.stderr)

    # Download .apk files
    print(f"\nDownloading {len(selected)} packages:")
    for name, entry in sorted(selected.items()):
        filename = f"{name}-{entry['V']}.apk"
        download(
            f"{BASE_URL}/{ARCH}/{filename}",
            os.path.join(PKG_DIR, filename),
            filename,
        )

    # Write filtered APKINDEX
    index_path = os.path.join(PKG_DIR, "APKINDEX.tar.gz")
    print(f"\nWriting local APKINDEX ({len(selected)} entries) → {index_path}")
    with open(index_path, "wb") as f:
        f.write(entries_to_tar(list(selected.values())))

    print("\nLocal repo contents:")
    for name, entry in sorted(selected.items()):
        print(f"  {name:<30} {entry['V']}")

    print("\nDone. Restart apk-server to pick up the new files:")
    print("  docker compose restart apk-server")


if __name__ == "__main__":
    main()
