#!/usr/bin/env bash
# deploy_microcloud.sh — scenario-aware MicroCloud deployment wrapper
# All parameters are passed as --key=value by the Python orchestrator.
set -euo pipefail

# -----------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------
SCENARIO="standard"
NODES=3
SIZING_TIER="small"
NETWORK_INTERFACE=""
OVN_UPLINK_INTERFACE=""
STORAGE_DISK=""
CEPH_OSD_DISK=""
STORAGE_SIZE=""
IPV4_GATEWAY=""
IPV4_RANGE=""
PRESEED_FILE=""
AUTO_APPROVE=false

# -----------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------
for arg in "$@"; do
    case "${arg}" in
        --scenario=*)           SCENARIO="${arg#*=}" ;;
        --nodes=*)              NODES="${arg#*=}" ;;
        --sizing-tier=*)        SIZING_TIER="${arg#*=}" ;;
        --network-interface=*)  NETWORK_INTERFACE="${arg#*=}" ;;
        --ovn-uplink-interface=*) OVN_UPLINK_INTERFACE="${arg#*=}" ;;
        --storage-disk=*)       STORAGE_DISK="${arg#*=}" ;;
        --ceph-osd-disk=*)      CEPH_OSD_DISK="${arg#*=}" ;;
        --storage-size=*)       STORAGE_SIZE="${arg#*=}" ;;
        --ipv4-gateway=*)       IPV4_GATEWAY="${arg#*=}" ;;
        --ipv4-range=*)         IPV4_RANGE="${arg#*=}" ;;
        --preseed-file=*)       PRESEED_FILE="${arg#*=}" ;;
        --auto-approve)         AUTO_APPROVE=true ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

# -----------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------
if [[ -z "${NETWORK_INTERFACE}" ]]; then
    echo "ERROR: --network-interface is required" >&2
    exit 1
fi

if [[ -z "${STORAGE_DISK}" ]]; then
    echo "ERROR: --storage-disk is required" >&2
    exit 1
fi

if [[ "${SCENARIO}" == "ha" && -z "${CEPH_OSD_DISK}" ]]; then
    echo "ERROR: --ceph-osd-disk is required for the 'ha' scenario" >&2
    exit 1
fi

if [[ "${SCENARIO}" =~ ^(standard|ha)$ && -z "${OVN_UPLINK_INTERFACE}" ]]; then
    echo "ERROR: --ovn-uplink-interface is required for the '${SCENARIO}' scenario" >&2
    exit 1
fi

# -----------------------------------------------------------------------
# Deployment plan summary
# -----------------------------------------------------------------------
echo "============================================================"
echo " MicroCloud deployment plan"
echo "============================================================"
echo "  Scenario          : ${SCENARIO}"
echo "  Nodes             : ${NODES}"
echo "  Sizing tier       : ${SIZING_TIER}"
echo "  Network interface : ${NETWORK_INTERFACE}"
echo "  OVN uplink NIC    : ${OVN_UPLINK_INTERFACE:-N/A}"
echo "  Storage disk      : ${STORAGE_DISK}"
echo "  Ceph OSD disk     : ${CEPH_OSD_DISK:-N/A}"
echo "  Storage size      : ${STORAGE_SIZE:-auto}"
echo "  IPv4 gateway      : ${IPV4_GATEWAY:-not set}"
echo "  IPv4 range        : ${IPV4_RANGE:-not set}"
echo "  Preseed file      : ${PRESEED_FILE:-none}"
echo "============================================================"

# -----------------------------------------------------------------------
# Execute by scenario
# TODO: wire each scenario to real terraform/ansible/preseed logic
# -----------------------------------------------------------------------
case "${SCENARIO}" in
    minimal)
        echo "[minimal] 3-node LVM cluster, no OVN"
        echo "Placeholder: real deployment will call terraform + ansible here."
        ;;
    standard)
        echo "[standard] 3-node OVN + LVM cluster"
        echo "Placeholder: real deployment will call terraform + ansible here."
        ;;
    ha)
        echo "[ha] 5-node Ceph + OVN cluster"
        echo "Placeholder: real deployment will call terraform + ansible here."
        ;;
    custom)
        echo "[custom] User-defined topology"
        echo "Placeholder: real deployment will call terraform + ansible here."
        ;;
    *)
        echo "ERROR: Unknown scenario '${SCENARIO}'" >&2
        exit 1
        ;;
esac

echo ""
echo "Deployment scaffold complete. Full deployment wiring is the next phase."
