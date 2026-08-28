#!/usr/bin/env bash
set -euo pipefail

# Install the local inference snap and make sure it can actually answer a
# request before we call the host ready.
#
# The snap package itself is tiny (tens of MB); the model is fetched lazily as a
# snap component the first time it is needed. Without the wait below, bootstrap
# would report success in seconds and the user would then hit a multi-GB
# download on their first chat message, with no indication of what is happening.

ENGINE="gemma4"
MODEL=""
READY_TIMEOUT_SEC="${INFERENCE_READY_TIMEOUT_SEC:-1800}"

usage() {
    cat <<'USAGE'
Usage: install_inference_snap.sh [--engine <snap>] [--model <name>]

  --engine <snap>   Inference snap to install (default: gemma4)
  --model <name>    Model variant to select before warm-up (for example e2b)

Smaller models download faster and need less RAM. For the gemma4 snap:
  e2b  ~2.9 GB   small hosts, CPU-only, constrained cloud VMs
  e4b  ~5.0 GB   default; good balance for laptops and workstations
  26b  ~15.8 GB  only worth it on large-memory hosts
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine=*) ENGINE="${1#*=}" ;;
        --engine) ENGINE="${2:-gemma4}"; shift ;;
        --model=*) MODEL="${1#*=}" ;;
        --model) MODEL="${2:-}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
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

if ! command -v "$ENGINE" >/dev/null 2>&1; then
    echo "Warning: '${ENGINE}' is installed but not on PATH; skipping model warm-up." >&2
    echo "Run 'lab-ai check' once the command is available." >&2
    exit 0
fi

if [[ -n "$MODEL" ]]; then
    echo "Selecting model: ${MODEL}"
    # Downloads the model component if it is missing, then restarts the service.
    "$ENGINE" use-model "$MODEL" --assume-yes
fi

# Show the user what is about to be downloaded so a long wait is never a mystery.
if "$ENGINE" list-models >/dev/null 2>&1; then
    echo ""
    echo "Available models for ${ENGINE} (the selected one is marked '*'):"
    "$ENGINE" list-models || true
    echo ""
fi

resolve_endpoint() {
    "$ENGINE" status --format json 2>/dev/null |
        python3 -c 'import json,sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
endpoints = data.get("endpoints") or {}
print(endpoints.get("openai") or "")' 2>/dev/null || true
}

echo "Preparing the local model. The first run downloads several GB and can take"
echo "a long time on a slow connection; later runs start immediately."

deadline=$(( SECONDS + READY_TIMEOUT_SEC ))
endpoint=""
ready=false

while (( SECONDS < deadline )); do
    [[ -z "$endpoint" ]] && endpoint="$(resolve_endpoint)"

    if [[ -n "$endpoint" ]] && curl -fsS --max-time 10 "${endpoint%/}/models" >/dev/null 2>&1; then
        ready=true
        break
    fi

    printf '.'
    sleep 5
done
printf '\n'

if [[ "$ready" != true ]]; then
    echo "" >&2
    echo "Warning: ${ENGINE} did not become ready within ${READY_TIMEOUT_SEC}s." >&2
    echo "The model may still be downloading. Check progress with:" >&2
    echo "  snap changes" >&2
    echo "  snap services ${ENGINE}" >&2
    echo "  ${ENGINE} status" >&2
    echo "Then run 'lab-ai check' to confirm." >&2
    exit 1
fi

echo "Inference snap ready: ${ENGINE}"
echo "Endpoint: ${endpoint}"
echo "Use 'lab-ai check' to verify the local service endpoint."
