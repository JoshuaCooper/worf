#!/usr/bin/env bash
# Manual end-to-end test for the /build endpoint.
# Usage: ./manual_build_test.sh [yaml_file] [image_name] [image_tag]

set -euo pipefail

YAML_FILE="${1:-$(dirname "$0")/../../infra/images/nginx_latest_nginxexample.yaml}"
IMAGE_NAME="${2:-test-image}"
IMAGE_TAG="${3:-manual-test}"
BASE_URL="http://localhost:8081"
POLL_INTERVAL=2
TIMEOUT=120

echo "=== WORF Manual Build Test ==="
echo "YAML:  $YAML_FILE"
echo "Image: $IMAGE_NAME:$IMAGE_TAG"
echo ""

# Submit the build
echo "--- Submitting build ---"
RESPONSE=$(curl -s -X POST "$BASE_URL/build" \
  -F "image_name=$IMAGE_NAME" \
  -F "image_tag=$IMAGE_TAG" \
  -F "file=@$YAML_FILE")

echo "Response: $RESPONSE"
JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Job ID: $JOB_ID"
echo ""

# Poll for completion
echo "--- Polling /status/$JOB_ID ---"
ELAPSED=0
while true; do
  STATUS_RESP=$(curl -s "$BASE_URL/status/$JOB_ID")
  STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "[${ELAPSED}s] status=$STATUS"

  if [[ "$STATUS" == "success" || "$STATUS" == "failed" ]]; then
    echo ""
    echo "--- Final result ---"
    echo "$STATUS_RESP" | python3 -m json.tool
    break
  fi

  if (( ELAPSED >= TIMEOUT )); then
    echo "Timed out after ${TIMEOUT}s"
    exit 1
  fi

  sleep "$POLL_INTERVAL"
  ELAPSED=$(( ELAPSED + POLL_INTERVAL ))
done

# Pull container logs
echo ""
echo "--- Container logs (apko-flask-server) ---"
docker logs --tail 60 apko-flask-server
