#!/usr/bin/env bash
set -euo pipefail

INSTALL_INFERENCE=true
INSTALL_MICROCLOUD_PREREQS=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-inference=*)
            INSTALL_INFERENCE="${1#*=}"
            ;;
        --install-microcloud-prereqs=*)
            INSTALL_MICROCLOUD_PREREQS="${1#*=}"
            ;;
        --install-inference)
            INSTALL_INFERENCE="${2:-true}"
            shift
            ;;
        --install-microcloud-prereqs)
            INSTALL_MICROCLOUD_PREREQS="${2:-true}"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
 done

if [[ "${INSTALL_MICROCLOUD_PREREQS}" == "true" ]]; then
    echo "Preparing host packages for MicroCloud..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y curl jq python3-venv python3-pip snapd
    else
        echo "apt-get is not available on this system." >&2
        exit 1
    fi
fi

if [[ "${INSTALL_INFERENCE}" == "true" ]]; then
    echo "Installing the Canonical inference snap..."
    bash "$(dirname "$0")/install_inference_snap.sh"
fi

echo "Host preparation completed."
