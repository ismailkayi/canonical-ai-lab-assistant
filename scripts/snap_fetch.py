#!/usr/bin/env python3
"""Download snaps (and their assertions) from the Snap Store, the patient way.

    snap_fetch.py <directory> <name>:<channel> [<name>:<channel> ...]

Writes <name>.snap and <name>.assert, exactly what "snap download" produces,
so a machine can install them offline with:
    snap ack <name>.assert && snap install <name>.snap

Why not just "snap download"? It gives up after a short timeout and restarts
a broken download from zero. On a flaky connection (timeouts, "connection
reset by peer") that means it never finishes. This script waits longer,
retries every request, and resumes an interrupted download where it stopped.
The file is checked against the store's SHA3-384 before it is used.

Python standard library only. Honours HTTPS_PROXY like other tools.
"""
import base64
import hashlib
import http.client
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STORE = os.environ.get("SNAP_FETCH_STORE", "https://api.snapcraft.io")  # override for testing
ATTEMPTS = 12          # per request
TIMEOUT = 60           # seconds without data before a request is retried
CHUNK = 1 << 20


def arch() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armhf",
            "ppc64le": "ppc64el", "s390x": "s390x", "riscv64": "riscv64"}.get(machine, machine)


def log(msg: str) -> None:
    print(msg, flush=True)


def retrying(what: str, func):
    """Run func() until it succeeds, waiting longer after each failure."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return func()
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403, 404):
                raise RuntimeError(f"{what}: HTTP {exc.code} {exc.read().decode(errors='replace')[:300]}")
            if attempt == ATTEMPTS:
                raise RuntimeError(f"{what}: giving up after {ATTEMPTS} attempts ({exc})")
            wait = min(5 * attempt, 30)
            log(f"{what}: {exc} — retrying in {wait}s (attempt {attempt}/{ATTEMPTS})")
            time.sleep(wait)


def store_info(name: str, channel: str) -> dict:
    """Ask the store which revision a channel holds, and where to download it."""
    body = json.dumps({
        "context": [],
        "actions": [{"action": "download", "instance-key": name, "name": name, "channel": channel}],
        "fields": ["download", "revision", "snap-id"],
    }).encode()

    def call():
        req = urllib.request.Request(f"{STORE}/v2/snaps/refresh", data=body, headers={
            "Content-Type": "application/json",
            "Snap-Device-Series": "16",
            "Snap-Device-Architecture": arch(),
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)

    data = retrying(f"{name}: store lookup", call)
    result = (data.get("results") or [{}])[0]
    if result.get("result") == "error" or "snap" not in result:
        raise RuntimeError(f"{name} ({channel}): store said {result.get('error') or data}")
    return result["snap"]


def download(name: str, url: str, size: int, target: Path) -> None:
    """Download url to target, resuming a partial file after any failure."""
    part = target.with_suffix(".part")

    def fetch():
        have = part.stat().st_size if part.exists() else 0
        if size and have >= size:
            return
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if have and resp.status != 206:      # server ignored the resume request
                have = 0
            with open(part, "ab" if have else "wb") as out:
                done, shown = have, -1
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    pct = done * 100 // size if size else 0
                    if pct // 10 != shown:
                        shown = pct // 10
                        log(f"{name}: {done / 2**20:.0f} / {size / 2**20:.0f} MiB ({pct}%)")
        if size and part.stat().st_size < size:
            raise OSError("download ended early")

    retrying(f"{name}: download", fetch)
    part.replace(target)


def assertion(path: str) -> str:
    def call():
        req = urllib.request.Request(f"{STORE}/v2/assertions/{path}",
                                     headers={"Accept": "application/x.ubuntu.assertion"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode()
    return retrying(f"assertion {path.split('/')[0]}", call).strip() + "\n"


def headers(text: str) -> dict:
    out = {}
    for line in text.split("\n\n", 1)[0].splitlines():
        if ": " in line and not line.startswith(" "):
            key, value = line.split(": ", 1)
            out[key] = value
    return out


def fetch(directory: Path, name: str, channel: str) -> None:
    snap_file, assert_file = directory / f"{name}.snap", directory / f"{name}.assert"
    channel_file = directory / f"{name}.channel"
    if (snap_file.exists() and assert_file.exists() and channel_file.exists()
            and channel_file.read_text().strip() == channel):
        log(f"{name} ({channel}): already downloaded")
        return

    info = store_info(name, channel)
    dl = info["download"]
    log(f"{name} ({channel}): revision {info['revision']}, {dl.get('size', 0) / 2**20:.0f} MiB")
    tmp = directory / f"{name}.download"
    download(name, dl["url"], dl.get("size", 0), tmp)

    digest = hashlib.sha3_384()
    with open(tmp, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    if digest.hexdigest() != dl["sha3-384"]:
        tmp.unlink()
        raise RuntimeError(f"{name}: checksum mismatch, the download was corrupted (deleted, run again)")

    # The same assertions "snap download" saves: who signed the snap, its
    # declaration, and the revision matching this exact file.
    key = base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=")
    revision = assertion(f"snap-revision/{key}")
    declaration = assertion(f"snap-declaration/16/{info['snap-id']}")
    sign_keys = dict.fromkeys(headers(a)["sign-key-sha3-384"] for a in (declaration, revision))
    parts = [assertion(f"account-key/{k}") for k in sign_keys]
    parts.append(assertion(f"account/{headers(declaration)['publisher-id']}"))
    parts += [declaration, revision]

    assert_file.write_text("\n".join(parts))
    tmp.replace(snap_file)
    channel_file.write_text(channel + "\n")
    log(f"{name} ({channel}): done")


def main() -> int:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    failed = []
    for spec in sys.argv[2:]:
        name, _, channel = spec.partition(":")
        try:
            fetch(directory, name, channel or "latest/stable")
        except Exception as exc:  # report and carry on with the others
            log(f"✘ {exc}")
            failed.append(name)
    if failed:
        log(f"Could not download: {' '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
