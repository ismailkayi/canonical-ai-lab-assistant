#!/usr/bin/env bash
set -euo pipefail

NODES=3
NETWORK_INTERFACE=""
STORAGE_TYPE="lvm"
STORAGE_SIZE=""
PRESEED_FILE=""
AUTO_APPROVE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --nodes=*)
            NODES="${1#*=}"
            ;;
        --network_interface=*)
            NETWORK_INTERFACE="${1#*=}"
            ;;
        --network-interface=*)
            NETWORK_INTERFACE="${1#*=}"
            ;;
        --storage_type=*)
            STORAGE_TYPE="${1#*=}"
            ;;
        --storage-type=*)
            STORAGE_TYPE="${1#*=}"
            ;;
        --storage_size=*)
            STORAGE_SIZE="${1#*=}"
            ;;
        --storage-size=*)
            STORAGE_SIZE="${1#*=}"
            ;;
        --preseed_file=*)
            PRESEED_FILE="${1#*=}"
            ;;
        --preseed-file=*)
            PRESEED_FILE="${1#*=}"
            ;;
        --auto-approve)
            AUTO_APPROVE=true
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
    shift
done

if [[ -z "${NETWORK_INTERFACE}" ]]; then
    echo "network interface is required for MicroCloud deployment." >&2
    exit 1
fi

cat <<EOF
MicroCloud deployment plan
- nodes: ${NODES}
- network interface: ${NETWORK_INTERFACE}
- storage type: ${STORAGE_TYPE}
- storage size: ${STORAGE_SIZE:-not set}
- preseed file: ${PRESEED_FILE:-not set}
- auto approve: ${AUTO_APPROVE}

This repository currently provides the MicroCloud-first bootstrap and plan layer.
The actual deployment workflow will be wired in the next iteration.
EOF
