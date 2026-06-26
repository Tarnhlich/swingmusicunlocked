#!/usr/bin/env bash
# =============================================================================
# Push SwingMusic image to a Gitea OCI registry.
#
# Usage:
#   ./scripts/push-registry.sh <gitea-host> <owner> [tag]
# =============================================================================

set -euo pipefail

GITEA_HOST="${1:?Usage: $0 <gitea-host> <owner> [tag]}"
OWNER="$(echo "${2:?Usage: $0 <gitea-host> <owner> [tag]}" | tr '[:upper:]' '[:lower:]')"
IMAGE_TAG="${3:-latest}"

LOCAL_IMAGE="swingmusic:${IMAGE_TAG}"
REMOTE_IMAGE="${GITEA_HOST}/${OWNER}/swingmusic:${IMAGE_TAG}"

REGISTRY_SECRET="${REGISTRY_PASSWORD:-${GITEA_TOKEN:-}}"

command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker not found."
    exit 1
}

if ! docker image inspect "${LOCAL_IMAGE}" >/dev/null 2>&1; then
    echo "Image '${LOCAL_IMAGE}' not found."
    echo "Run:"
    echo "  ./scripts/build-prod.sh ${IMAGE_TAG}"
    exit 1
fi

if [[ -n "${REGISTRY_USER:-}" && -n "${REGISTRY_SECRET:-}" ]]; then
    echo "==> Logging into ${GITEA_HOST}"
    printf '%s' "${REGISTRY_SECRET}" | docker login \
        "${GITEA_HOST}" \
        --username "${REGISTRY_USER}" \
        --password-stdin
fi

echo "================================================================"
echo " Registry : ${GITEA_HOST}"
echo " Owner    : ${OWNER}"
echo " Tag      : ${IMAGE_TAG}"
echo "================================================================"

docker tag "${LOCAL_IMAGE}" "${REMOTE_IMAGE}"

docker push "${REMOTE_IMAGE}"

echo ""
echo "================================================================"
echo " Push complete"
echo ""
echo " Image:"
echo "   ${REMOTE_IMAGE}"
echo "================================================================"