#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

RUN_BOOTSTRAP=false
RUN_CHECK=false
RUN_CHAT=false
OPEN_SHELL=false
FORCE_REINSTALL=false
CLEAN_VENV=false
RUN_DIAGNOSE=false

usage() {
  echo "Usage:"
  echo "  ./dev.sh"
  echo "  ./dev.sh --bootstrap"
  echo "  ./dev.sh --check"
  echo "  ./dev.sh --chat"
  echo "  ./dev.sh --diagnose"
  echo "  ./dev.sh --shell"
  echo "  ./dev.sh --force-reinstall"
  echo "  ./dev.sh --clean"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bootstrap) RUN_BOOTSTRAP=true ;;
    --check) RUN_CHECK=true ;;
    --chat) RUN_CHAT=true ;;
    --diagnose) RUN_DIAGNOSE=true ;;
    --shell) OPEN_SHELL=true ;;
    --force-reinstall) FORCE_REINSTALL=true ;;
    --clean) CLEAN_VENV=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
  shift
done

cd "${ROOT_DIR}"

if [[ "${CLEAN_VENV}" == "true" ]]; then
  rm -rf "${VENV_DIR}"
  echo "[info] Removed .venv"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[info] Creating virtual environment..."
  python3 -m venv "${VENV_DIR}"
fi

PY="${VENV_DIR}/bin/python"
PIP="${VENV_DIR}/bin/pip"
LABAI="${VENV_DIR}/bin/lab-ai"

# Repair partially-created venvs (common after a failed first run)
if [[ ! -x "${PIP}" ]]; then
  echo "[warn] pip missing in .venv, recreating virtual environment..."
  rm -rf "${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
  PY="${VENV_DIR}/bin/python"
  PIP="${VENV_DIR}/bin/pip"
  LABAI="${VENV_DIR}/bin/lab-ai"
fi

echo "[info] Updating pip/setuptools/wheel..."
"${PY}" -m pip install --upgrade pip setuptools wheel >/dev/null

if [[ "${FORCE_REINSTALL}" == "true" ]]; then
  echo "[info] Force reinstall requested..."
  "${PIP}" uninstall -y canonical-ai-lab-assistant >/dev/null 2>&1 || true
fi

echo "[info] Installing project in editable mode..."
"${PIP}" install -e . >/dev/null

echo "[info] Running quick syntax check..."
"${PY}" -m compileall -q src

if [[ "${RUN_BOOTSTRAP}" == "true" ]]; then
  echo "[info] Requesting sudo authentication for host bootstrap..."
  if ! sudo -v; then
    echo "[error] Sudo authentication failed."
    echo "        Please retry with the correct password:"
    echo "        sudo -v"
    echo "        ./dev.sh --bootstrap"
    exit 1
  fi
  echo "[info] Running host bootstrap..."
  "${LABAI}" bootstrap
fi

if [[ "${RUN_CHECK}" == "true" ]]; then
  echo "[info] Running inference health check..."
  "${LABAI}" check || true
fi

if [[ "${RUN_DIAGNOSE}" == "true" ]]; then
  echo "[info] Running comprehensive inference diagnostics..."
  bash "${ROOT_DIR}/scripts/diagnose_inference.sh" || true
fi

if [[ "${RUN_CHAT}" == "true" ]]; then
  echo "[info] Starting lab-ai chat..."
  exec "${LABAI}" chat
fi

if [[ "${OPEN_SHELL}" == "true" ]]; then
  echo "[info] Opening shell with venv activated..."
  export VIRTUAL_ENV="${VENV_DIR}"
  export PATH="${VENV_DIR}/bin:${PATH}"
  exec "${SHELL:-/bin/bash}" -i
fi

echo ""
echo "[ok] Dev environment is ready."
echo "You can run the exact same user CLI now:"
echo "  ${LABAI} chat"
echo "or after activation:"
echo "  source .venv/bin/activate && lab-ai chat"
