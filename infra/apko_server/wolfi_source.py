import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

from flask import Blueprint, jsonify, request

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
wolfi_bp = Blueprint("wolfi", __name__, url_prefix="/wolfi")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WOLFI_RAW_BASE = "https://raw.githubusercontent.com/wolfi-dev/os/main"
WOLFI_TREE_API = "https://api.github.com/repos/wolfi-dev/os/git/trees/main?recursive=1"
WOLFI_LIST_TTL = int(os.environ.get("WOLFI_LIST_TTL", 3600))

# ---------------------------------------------------------------------------
# File-list cache
# ---------------------------------------------------------------------------
_cache      = {"files": [], "fetched_at": 0.0}
_cache_lock = threading.Lock()


def _gh_get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "Accept":     "application/vnd.github.v3+json",
            "User-Agent": "worf-package-builder",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _fetch_file_list() -> list[str]:
    """Pull the Git tree from GitHub and return sorted root-level YAML filenames."""
    data = json.loads(_gh_get(WOLFI_TREE_API).decode())
    return sorted(
        item["path"]
        for item in data.get("tree", [])
        if item["type"] == "blob"
        and item["path"].endswith(".yaml")
        and "/" not in item["path"]
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@wolfi_bp.route("/list", methods=["GET"])
def list_packages():
    """
    Return a sorted list of package YAML filenames from wolfi-dev/os.

    Uses the GitHub Git Trees API — one lightweight JSON request — rather than
    cloning the repo. Cached for WOLFI_LIST_TTL seconds.
    """
    now = time.time()
    with _cache_lock:
        if _cache["files"] and now - _cache["fetched_at"] < WOLFI_LIST_TTL:
            return jsonify(_cache["files"])

    try:
        files = _fetch_file_list()
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"GitHub API returned {e.code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    with _cache_lock:
        _cache["files"]      = files
        _cache["fetched_at"] = time.time()

    return jsonify(files)


@wolfi_bp.route("/fetch", methods=["GET"])
def fetch_package():
    """
    Fetch a single package YAML from wolfi-dev/os on demand using wget.

    Query params:
        file — bare filename, e.g. "zlib.yaml"

    Returns the raw YAML so the UI can load it into the editor.
    Falls back to urllib if wget is not available in the container.
    """
    filename = request.args.get("file", "").strip()

    if not filename or not filename.endswith(".yaml") or "/" in filename or ".." in filename:
        return jsonify({"error": "invalid filename"}), 400

    url = f"{WOLFI_RAW_BASE}/{filename}"

    # Try wget first (explicit, visible in logs, matches the intended mechanism)
    try:
        result = subprocess.run(
            ["wget", "-qO-", url],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            return result.stdout, 200, {"Content-Type": "application/yaml; charset=utf-8"}
        return jsonify({"error": result.stderr or "wget failed"}), 502

    except FileNotFoundError:
        pass  # wget not in container, fall through to urllib

    # urllib fallback
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "worf-package-builder"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8")
        return content, 200, {"Content-Type": "application/yaml; charset=utf-8"}
    except urllib.error.HTTPError as e:
        status = 404 if e.code == 404 else 502
        return jsonify({"error": f"HTTP {e.code}"}), status
    except Exception as e:
        return jsonify({"error": str(e)}), 500
