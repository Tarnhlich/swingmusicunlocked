#!/usr/bin/env bash
# =============================================================================
# Build SwingMusic production image
#
# Usage:
#   ./scripts/build-prod.sh [version]
#
# Examples:
#   ./scripts/build-prod.sh
#   ./scripts/build-prod.sh v2.4.0
# =============================================================================

set -euo pipefail

IMAGE_TAG="${1:-latest}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker not found."
    exit 1
}

echo "================================================================"
echo " Building SwingMusic"
echo " Version : ${IMAGE_TAG}"
echo "================================================================"

DOCKER_BUILDKIT=1 docker build \
    --no-cache \
    --load \
    --build-arg app_version="${IMAGE_TAG}" \
    --tag "swingmusic:${IMAGE_TAG}" \
    .

echo ""
echo "================================================================"
echo " Build complete"
echo ""
echo " Image:"
echo "   swingmusic:${IMAGE_TAG}"
echo "================================================================"