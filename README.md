# W.O.R.F. — Wolfi Offline Release Framework

An experimental platform for building, storing, and distributing Wolfi-OS container images without Docker layer builds. Images are defined as declarative YAML files, built entirely by APKO inside a secure local environment, and pushed to a self-hosted registry.

The goal is a system that works in air-gapped or restricted environments and can eventually run inside Kubernetes — rebuilding images automatically when upstream Wolfi packages are updated.

---

## Quick start

**Requirements:** Docker, Docker Compose, `make`

```sh
# 1. Start all services
make start

# 2. Open the image builder UI
open http://localhost:8081

# 3. Build an image via the API (or use the UI)
curl -X POST \
  -F "image_name=my-image" \
  -F "image_tag=latest" \
  -F "file=@infra/images/python_3.12_python.yaml" \
  http://localhost:8081/build

# 4. Poll for build status (replace <job_id> with the ID from step 3)
curl http://localhost:8081/status/<job_id>

# 5. List built images in the registry
curl http://localhost:5000/v2/_catalog

# 6. Stop everything
make down
```

| Service | URL | What it is |
|---|---|---|
| Image builder UI + API | http://localhost:8081 | Flask app — submit builds, browse packages |
| Local registry | http://localhost:5000 | Built images are pushed here |
| Wolfi package proxy | http://localhost:8080 | nginx reverse proxy to packages.wolfi.dev |

---

## What is this, exactly?

Standard container builds layer changes on top of a base image using `docker build`. WORF takes a different approach: every image is described as a list of Wolfi packages in an APKO YAML file. APKO assembles the image from those packages directly — no intermediate layers, no Dockerfile, no Docker daemon required.

This means:
- Images contain only what you explicitly declare
- Every build is reproducible from the YAML alone
- You can audit the full contents via the generated SBOM
- The build process never touches the host Docker socket

If you're unfamiliar with the toolchain, the relevant projects are:
- **[Wolfi OS](https://github.com/wolfi-dev/os)** — a minimal, container-focused Linux distribution maintained by Chainguard
- **[APKO](https://github.com/chainguard-dev/apko)** — builds OCI images from a declarative YAML spec, using APK packages as inputs
- **[Melange](https://github.com/chainguard-dev/melange)** — builds APK packages from source (not yet wired into WORF)

---

## System architecture

Three services run together on a shared Docker network (`worf`):

```
 ┌──────────────────────────────────────────────────────┐
 │                   worf network                        │
 │                                                       │
 │  ┌─────────────────┐     ┌──────────────────────┐    │
 │  │   apk-server    │     │  apko-flask-server   │    │
 │  │   :8080         │     │  :8081               │    │
 │  │                 │     │                      │    │
 │  │  nginx proxy    │     │  Build API + UI      │    │
 │  │  → wolfi.dev    │     │  runs apko publish   │    │
 │  └─────────────────┘     └──────────┬───────────┘    │
 │                                     │ push           │
 │                           ┌─────────▼──────────┐     │
 │                           │     registry       │     │
 │                           │     :5000          │     │
 │                           │  docker/registry:2 │     │
 │                           └────────────────────┘     │
 └──────────────────────────────────────────────────────┘
```

### apk-server (port 8080)

An nginx container that reverse-proxies requests to `packages.wolfi.dev`. When APKO inside the flask server builds an image, it fetches packages through this proxy rather than going directly to the internet. This is the foundation of the offline/air-gapped mode — the proxy will eventually cache packages locally so builds work without network access.

**Config:** `infra/nginx/wolfi-proxy.conf`
**Image:** `cgr.dev/chainguard/nginx:latest-dev`

### apko-flask-server (port 8081)

The core of WORF. A Python Flask application that:
- Serves the image builder UI at `GET /`
- Accepts APKO YAML file uploads and triggers builds
- Runs `apko publish` in background threads to build and push images
- Tracks build job state so clients can poll for results
- Serves the Wolfi package index to the UI

This service is itself a Wolfi-native image — it is bootstrapped by running Chainguard's APKO image to build `apko_server.yaml` into a container that has APKO and Python baked in. The Flask application is then layered on top via a standard Dockerfile.

**Source:** `infra/apko_server/`
**APKO base spec:** `infra/init_container/apko_server.yaml`

### registry (port 5000)

A standard Docker Distribution registry (v2) that stores the images WORF builds. Accessible at `registry.localhost:5000` on the host.

**Image:** `registry:2`
**Data volume:** `infra/registry-data/`

---

## Getting started

### Prerequisites

- Docker and Docker Compose
- `make`
- Internet access for the initial bootstrap (the first build fetches Chainguard's APKO image)

### Start WORF

```
make start
```

This does the following in order:

1. Runs Chainguard's public APKO image to build `infra/init_container/apko_server.yaml` into a local tar file (`apko_init.tar`)
2. Loads that tar file into Docker as `apko-server:latest`
3. Starts the registry service
4. Builds and starts the `apko-flask-server` using `apko-server:latest` as its base image
5. Starts the `apk-server` nginx proxy
6. Cleans up the temporary tar and SBOM files from `infra/init_container/`

After startup, open `http://localhost:8081` to reach the image builder UI.

### Stop WORF

```
make down
```

### Build the pre-defined example images

```
make build-images
```

This iterates over every YAML file in `infra/images/` and submits each one as a build job to the flask server. Image names and tags are derived from the filename: `name_tag_description.yaml` → `name:tag`.

---

## The image builder UI

Open `http://localhost:8081` in a browser.

The UI has three panels:

**Packages** (left)
Browse and search the full Wolfi package index. The list is loaded once from the server on page load — all searching and filtering runs in the browser with no additional requests. Matches on package name, description, and origin. Click any package to add it to your profile.

**Image Profile** (center)
Configure the image you want to build:
- Image name and tag (used as the registry reference)
- Entrypoint command (what runs when the container starts)
- Target architectures (amd64 / aarch64)
- The list of packages you've added (click × to remove)

**YAML Preview** (right)
A live preview of the APKO YAML file that will be submitted when you click Build. Updates instantly as you make changes — no server round-trip.

Click **Build image** to submit. The button polls `/status/<job_id>` in the background and shows the result (image reference on success, error on failure) in the status bar at the bottom.

---

## API reference

All endpoints are served by `apko-flask-server` on port 8081.

### `GET /`
Returns the image builder UI (HTML).

### `GET /packages`
Returns the full Wolfi package list as a JSON array.

Each object contains: `name`, `version`, `arch`, `description`, `license`, `origin`, `dependencies`, `provides`, `size`, `installed_size`.

The list is cached for 5 minutes (configurable via `PACKAGE_CACHE_TTL`). The first request after startup or cache expiry will be slower.

**Query params:**
- `q` — filter by package name, case-insensitive (e.g. `?q=curl`)

**Example:**
```sh
curl http://localhost:8081/packages?q=python
```

### `POST /build`
Submit an APKO YAML file for building. Returns immediately with a job ID.

**Form fields:**
- `image_name` — image name (default: `apko-image`)
- `image_tag` — image tag (default: `latest`)
- `file` — the APKO YAML file (multipart upload)

**Response:** `202 Accepted`
```json
{ "job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "pending" }
```

**Example:**
```sh
curl -X POST \
  -F "image_name=my-python" \
  -F "image_tag=3.12" \
  -F "file=@infra/images/python_3.12_python.yaml" \
  http://localhost:8081/build
```

### `GET /status/<job_id>`
Poll the status of a build job.

**Response:** `200 OK`
```json
{ "job_id": "...", "status": "running", "output": null }
```

**Status values:**
| Value | Meaning |
|-------|---------|
| `pending` | Queued, not yet started |
| `running` | `apko publish` is in progress |
| `success` | Built and pushed; `output` contains the full image reference |
| `failed` | Build failed; `output` contains the error from apko |

---

## APKO YAML format

APKO image specs are YAML files placed in `infra/images/`. Each file describes a complete image.

**Filename convention:** `name_tag_description.yaml`
The `make build-images` target parses the filename to determine the image name and tag.

**Minimal example:**
```yaml
contents:
  keyring:
    - https://packages.wolfi.dev/os/wolfi-signing.rsa.pub
  repositories:
    - https://packages.wolfi.dev/os
  packages:
    - wolfi-baselayout
    - python-3.12
    - busybox

accounts:
  groups:
    - groupname: nonroot
      gid: 65532
  users:
    - username: nonroot
      uid: 65532
  run-as: "65532"

entrypoint:
  command: /usr/bin/python

archs:
  - amd64
```

**Key fields:**
- `contents.packages` — the Wolfi packages to include. This is the primary field you configure. Use the image builder UI or `GET /packages` to find available packages.
- `entrypoint.command` — the default command when the container runs
- `archs` — list of target architectures. Supports `amd64` and `aarch64`.
- `accounts` — user and group configuration. `run-as: 65532` runs the container as the `nonroot` user, which is required in most Kubernetes environments.

---

## Code layout

```
worf/
├── Makefile                        # start, stop, build-images targets
├── docker-compose.yml              # service definitions
│
├── infra/
│   ├── apko_server/                # apko-flask-server source
│   │   ├── apko_flask_server.py    # Flask app — routes, job queue, package cache
│   │   ├── package_index.py        # Wolfi APKINDEX fetch and parse
│   │   ├── Dockerfile              # builds on top of the APKO-native base image
│   │   ├── requirements.txt        # flask, pyyaml, pytest
│   │   ├── templates/
│   │   │   └── index.html          # image builder UI
│   │   ├── test_apko_flask_server.py
│   │   └── test_package_index.py
│   │
│   ├── init_container/
│   │   └── apko_server.yaml        # APKO spec for the flask server base image
│   │                               # (built by `make start` using Chainguard's APKO image)
│   │
│   ├── images/                     # example APKO image specs
│   │   ├── python_3.12_python.yaml
│   │   ├── nginx_latest_nginxexample.yaml
│   │   └── examplename_latest_exampleimage.yaml
│   │
│   ├── nginx/
│   │   └── wolfi-proxy.conf        # nginx config for the apk-server proxy
│   │
│   ├── packages/                   # local APK package storage (empty until Melange is wired in)
│   └── registry-data/              # persistent registry storage (bind-mounted into the registry container)
│
└── workload/                       # placeholder for application workload definitions
```

---

## Key design decisions

### Why not use `apk update` to get the package list?

`apk update` works but requires the `apk` binary inside the container and writes files to disk. `package_index.py` fetches and parses the APKINDEX tarball directly in Python using only the standard library (`urllib`, `tarfile`, `io`). This means:
- The package list is available anywhere Python runs — including the future update-watcher
- No extra binary dependency
- The raw HTTP ETag from the download is captured and stored. The watcher will use this to detect when the index changes without re-downloading the full file on every poll

### Why are builds async?

`apko publish` can take 30–60 seconds for a non-trivial image. A synchronous endpoint would hold the HTTP connection open for that entire time, which breaks proxies, browsers, and retry logic. The flask server instead:
1. Accepts the upload, writes the YAML to a temp file, creates a job record, returns `202` with a job ID
2. Runs the actual build in a background thread (via `ThreadPoolExecutor`)
3. Exposes `/status/<job_id>` for polling

The in-memory job store and thread pool are intentionally simple. The comments in the code mark exactly where to swap in Redis and a proper task queue when moving to Kubernetes with multiple replicas.

### Why is the package cache TTL-based rather than ETag-based?

The cache uses a simple time-to-live (default 5 minutes) rather than asking the server "has this changed?" on every request. This is because the `If-None-Match` ETag pattern requires a round-trip to the Wolfi CDN to get a `304 Not Modified` response — which is still a network call with latency. For the UI use case, serving slightly stale package data for 5 minutes is fine and cheaper. The ETag is still captured and stored for the watcher, which has different requirements.

### The bootstrap problem

The flask server needs APKO to build images. But APKO itself needs to be installed somewhere. WORF solves this by using Chainguard's public APKO image (a single-purpose container that just runs `apko`) to build `infra/init_container/apko_server.yaml` into a new image that has both APKO and Python. That image becomes the base for the Dockerfile that adds the Flask application code. This is the sequence `make start` runs, and it means the only external dependency is Chainguard's APKO image — everything else is self-contained.

---

## Running the tests

Tests live alongside the source in `infra/apko_server/`. They require no running services — all network calls and subprocess invocations are mocked.

```sh
cd infra/apko_server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -v
```

**Test files:**

`test_apko_flask_server.py` — tests for the Flask API
- Build endpoint: correct HTTP codes, missing file handling, default field values
- Status endpoint: 404 for unknown jobs, success/failure state transitions, required response fields
- UI endpoint: page renders, HTML structure
- Packages endpoint: JSON shape, search filter, cache behaviour

`test_package_index.py` — tests for the APKINDEX parser
- Parsing: field mapping, list splitting, missing fields, edge cases
- Deduplication: highest version wins, all unique names preserved
- Fetching: URL construction, ETag handling, missing ETag

An important testing pattern used throughout: when testing async behaviour (builds run in background threads), mocks must patch the module-level reference (`apko_flask_server.subprocess.run`) and remain active through the status poll — not just through the initial POST. Closing a mock context before the background thread runs means the real binary gets called instead.

---

## Environment variables

All tuneable values are read from environment variables so nothing needs to change between local and Kubernetes deployments.

| Variable | Default | Description |
|---|---|---|
| `REGISTRY_HOST` | `registry.localhost:5000` | Registry to push built images to. Set to the Kubernetes Service name when deploying to a cluster. |
| `MAX_WORKERS` | `2` | Max concurrent `apko publish` processes. Raise with caution — builds are CPU and I/O heavy. |
| `PACKAGE_CACHE_TTL` | `300` | Seconds before the Wolfi package list is re-fetched. |
| `WOLFI_BASE_URL` | `https://packages.wolfi.dev/os` | Wolfi package repository base URL. Set to `http://apk-server:8080` to route through the local proxy. |
| `GGCR_INSECURE` | `1` (hardcoded) | Allows pushing to registries without TLS. Required for the local registry. |

---

## What's not built yet

The following items are tracked in `TODO.md`:

- **Package customizer interface** — extended UI for browsing package details, viewing dependencies, and constructing more complex APKO profiles
- **apk-server package caching** — the nginx proxy currently forwards requests to `packages.wolfi.dev` without storing anything locally. For true offline mode it needs to cache the full package set to a volume
- **Update watcher** — a scheduled job that polls the Wolfi APKINDEX ETag, detects when packages change, and automatically re-submits the affected image profiles for rebuilding
- **Kubernetes manifests** — Deployments, Services, PVCs, and a CronJob for the watcher. The code is written with this in mind (env-var config, async builds, swap-friendly in-memory stores) but the manifests don't exist yet
- **Melange integration** — building custom APK packages from source and serving them through the apk-server, so locally built packages can be included in APKO images alongside upstream Wolfi packages
- **nonroot enforcement** — `USER nonroot` is currently commented out in the Dockerfile. Needs to be restored before production or Kubernetes use
