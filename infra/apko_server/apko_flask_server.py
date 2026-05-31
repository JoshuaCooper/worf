from flask import Flask, request, jsonify, render_template
import subprocess
import os
import tempfile
import uuid
import threading
import time
import dataclasses
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
import package_index

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------
# GGCR_INSECURE tells the Go container registry library (used by apko) to
# allow pushing to registries without TLS — needed for our local registry.
os.environ['GGCR_INSECURE'] = '1'

# REGISTRY_HOST is read from the environment so this value can be changed
# without modifying code. In docker-compose it defaults to registry.localhost:5000.
# In Kubernetes this will be set to the registry Service name (e.g. registry:5000).
REGISTRY_HOST = os.environ.get("REGISTRY_HOST", "registry.localhost:5000")


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------
# A simple in-memory dictionary that tracks the state of every build job.
# Each entry is keyed by a UUID and holds:
#   status  — "pending" | "running" | "success" | "failed"
#   output  — the final image reference on success, or error text on failure
#
# In-memory is fine for a single-process server. When this moves to Kubernetes
# with multiple replicas, swap this out for a Redis-backed store by replacing
# the get/set calls below — the rest of the code stays the same.
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()  # guards concurrent reads/writes to the dict


# ---------------------------------------------------------------------------
# Thread pool
# ---------------------------------------------------------------------------
# Limits how many apko builds run at the same time. apko builds are CPU and
# I/O heavy, so running too many in parallel degrades everything.
# MAX_WORKERS can be tuned via environment variable.
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 2))
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)


# ---------------------------------------------------------------------------
# Repository registry
# ---------------------------------------------------------------------------
# Each entry describes one APK repository the server can browse.
# base_url and keyring are used both for fetching the APKINDEX here and for
# generating the YAML that gets sent to apko.
#
# Override URLs via env vars so the same image works in different deployments
# without a rebuild (e.g. air-gapped, different registry hostnames).
REPOS: dict[str, dict] = {
    "wolfi": {
        "name":     "Wolfi OS",
        "base_url": os.environ.get("WOLFI_BASE_URL", package_index.WOLFI_BASE_URL),
        "keyring":  "https://packages.wolfi.dev/os/wolfi-signing.rsa.pub",
        "color":    "#58a6ff",
        "short":    "W",
    },
    "chainguard": {
        "name":     "Chainguard",
        "base_url": os.environ.get("CHAINGUARD_APK_URL", "https://packages.cgr.dev/os"),
        "keyring":  "https://packages.cgr.dev/os/chainguard.rsa.pub",
        "color":    "#f0883e",
        "short":    "C",
    },
    "local": {
        "name":     "Local",
        "base_url": os.environ.get("LOCAL_APK_URL", "http://apk-server:8080/os"),
        "keyring":  os.environ.get("LOCAL_APK_KEYRING", "http://apk-server:8080/os/wolfi-signing.rsa.pub"),
        "color":    "#3fb950",
        "short":    "L",
    },
}

# ---------------------------------------------------------------------------
# Package cache (per-repo)
# ---------------------------------------------------------------------------
PACKAGE_CACHE_TTL = int(os.environ.get("PACKAGE_CACHE_TTL", 300))

_repo_caches: dict[str, dict] = {
    rid: {"packages": [], "etag": None, "fetched_at": 0.0}
    for rid in REPOS
}
_repo_cache_locks: dict[str, threading.Lock] = {
    rid: threading.Lock() for rid in REPOS
}


def _get_packages_for_repo(repo_id: str) -> list:
    """Return the cached package list for a repo, refreshing if the TTL has expired."""
    repo  = REPOS.get(repo_id)
    if repo is None:
        raise KeyError(repo_id)
    cache = _repo_caches[repo_id]
    lock  = _repo_cache_locks[repo_id]
    now   = time.time()
    with lock:
        if now - cache["fetched_at"] < PACKAGE_CACHE_TTL and cache["packages"]:
            return cache["packages"]
        packages, etag = package_index.fetch(base_url=repo["base_url"])
        cache.update(packages=packages, etag=etag, fetched_at=time.time())
        return packages


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def logo():
    with open("logo.txt", "r") as file:
        print(file.read())

app = Flask(__name__)

# Feature flag: set ENABLE_WOLFI_SOURCE=1 to activate the /wolfi/* routes
if os.environ.get("ENABLE_WOLFI_SOURCE", "0") == "1":
    from wolfi_source import wolfi_bp
    app.register_blueprint(wolfi_bp)
    print("[feature] wolfi source browser enabled")


# ---------------------------------------------------------------------------
# Build worker
# ---------------------------------------------------------------------------
# This function runs inside a background thread managed by the ThreadPoolExecutor.
# It is NOT called directly by the HTTP handler — the handler submits it to the
# pool and returns immediately with a job ID.
def run_build(job_id: str, yaml_path: str, image_name: str, image_tag: str):
    """Execute `apko publish` for a given APKO YAML and update job state."""

    output_ref = f"{REGISTRY_HOST}/{image_name}:{image_tag}"

    # Mark the job as actively running so callers polling /status can see progress.
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    try:
        # apko publish builds the image from the YAML and pushes it directly
        # to the registry — no intermediate tar file, no docker daemon needed.
        # check=True means a non-zero exit code raises CalledProcessError.
        subprocess.run(
            ["apko", "publish", yaml_path, output_ref],
            check=True,
            capture_output=True,  # capture stdout/stderr so we can store them
            text=True,
        )

        with jobs_lock:
            jobs[job_id]["status"] = "success"
            jobs[job_id]["output"] = output_ref

        print(f"[{job_id}] Built {output_ref}")

    except subprocess.CalledProcessError as e:
        # apko exited non-zero — store stderr so the caller can see what went wrong.
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["output"] = e.stderr

        print(f"[{job_id}] Build failed: {e.stderr}")

    finally:
        # Always clean up the temporary YAML file regardless of outcome.
        if os.path.exists(yaml_path):
            os.remove(yaml_path)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/build", methods=["POST"])
def build():
    """
    Accept an APKO YAML file upload and enqueue a build job.

    Form fields:
        image_name  — name component of the output image reference
        image_tag   — tag component of the output image reference
        file        — the APKO YAML file

    Returns 202 Accepted immediately with a job_id.
    The caller uses GET /status/<job_id> to poll for the result.
    """
    image_name = request.form.get("image_name", "apko-image")
    image_tag  = request.form.get("image_tag",  "latest")
    file       = request.files.get("file")

    if not file:
        return jsonify({"error": "no file provided"}), 400

    # Save the uploaded YAML to a temp file. The build worker will clean it up
    # when the job finishes. We can't use a context manager here because the
    # file needs to outlive this request handler — the worker runs later.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as tmp:
        file.save(tmp.name)
        yaml_path = tmp.name

    # Generate a unique ID for this job so the caller can track it.
    job_id = str(uuid.uuid4())

    with jobs_lock:
        jobs[job_id] = {"status": "pending", "output": None}

    # Submit the build to the thread pool. This returns immediately —
    # the actual apko build happens in the background.
    executor.submit(run_build, job_id, yaml_path, image_name, image_tag)

    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    """
    Poll the status of a build job.

    Returns:
        200 with { job_id, status, output } if the job exists.
        404 if the job_id is unknown.

    Status values:
        pending  — job is queued, not yet started
        running  — apko build is in progress
        success  — image was built and pushed; output contains the image reference
        failed   — build failed; output contains the error from apko
    """
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        return jsonify({"error": "job not found"}), 404

    return jsonify({"job_id": job_id, **job}), 200


@app.route("/", methods=["GET"])
def index():
    """Serve the image builder UI."""
    return render_template("index.html")


@app.route("/melange", methods=["GET"])
def melange():
    """Serve the package builder UI."""
    return render_template("melange.html")


@app.route("/baselines", methods=["GET"])
def baselines():
    """Return the list of baseline image profiles."""
    path = os.path.join(os.path.dirname(__file__), "baselines.json")
    with open(path) as f:
        return jsonify(json.load(f))


@app.route("/repos", methods=["GET"])
def get_repos():
    """Return the list of configured APK repositories."""
    return jsonify([
        {
            "id":       rid,
            "name":     info["name"],
            "color":    info["color"],
            "short":    info["short"],
            "base_url": info["base_url"],
            "keyring":  info["keyring"],
        }
        for rid, info in REPOS.items()
    ])


@app.route("/packages", methods=["GET"])
def packages():
    """
    Return the package list for a given repository as JSON.

    Query params:
        repo — repository id (default: "wolfi")
        q    — optional search string, filters by package name (case-insensitive)
    """
    repo_id = request.args.get("repo", "wolfi")
    try:
        pkgs = _get_packages_for_repo(repo_id)
    except KeyError:
        return jsonify({"error": f"Unknown repository: {repo_id}"}), 400

    query = request.args.get("q", "").lower()
    if query:
        pkgs = [p for p in pkgs if query in p.name.lower()]

    return jsonify([dataclasses.asdict(p) for p in pkgs])


if __name__ == "__main__":
    logo()
    app.run(host="0.0.0.0", port=8081)
