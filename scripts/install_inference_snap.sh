#!/usr/bin/env bash
set -euo pipefail

ENGINE="gemma4"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine=*)
            ENGINE="${1#*=}"
            ;;
        --engine)
            ENGINE="${2:-gemma4}"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
 done

if snap list "$ENGINE" >/dev/null 2>&1; then
    echo "${ENGINE} is already installed."
else
    echo "Installing ${ENGINE}..."
    sudo snap install "$ENGINE"
fi

echo "Inference snap ready: ${ENGINE}"
echo "Use 'lab-ai check' to verify the local service endpoint."
