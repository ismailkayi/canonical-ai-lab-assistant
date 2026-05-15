"""
Documentation fetcher for MicroCloud and related Canonical products.

The AI agent calls this module when it needs to answer questions that
require information from official documentation rather than its training
data.  Fetched content is cached in the session state directory to avoid
redundant network requests.
"""

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known documentation sources
# ---------------------------------------------------------------------------

DOC_SOURCES: dict[str, str] = {
    # MicroCloud
    "microcloud": "https://documentation.ubuntu.com/microcloud/",
    "microcloud-install": "https://documentation.ubuntu.com/microcloud/en/latest/how-to/install/",
    "microcloud-init": "https://documentation.ubuntu.com/microcloud/en/latest/how-to/initialise/",
    "microcloud-networking": "https://documentation.ubuntu.com/microcloud/en/latest/explanation/networking/",
    "microcloud-storage": "https://documentation.ubuntu.com/microcloud/en/latest/explanation/storage/",
    "microcloud-preseed": "https://documentation.ubuntu.com/microcloud/en/latest/how-to/preseed/",
    "microcloud-faq": "https://documentation.ubuntu.com/microcloud/en/latest/reference/faq/",
    "microcloud-requirements": "https://documentation.ubuntu.com/microcloud/en/latest/reference/requirements/",
    # LXD
    "lxd": "https://documentation.ubuntu.com/lxd/en/latest/",
    "lxd-install": "https://documentation.ubuntu.com/lxd/en/latest/installing/",
    "lxd-networks": "https://documentation.ubuntu.com/lxd/en/latest/explanation/networks/",
    "lxd-storage": "https://documentation.ubuntu.com/lxd/en/latest/explanation/storage/",
    # MicroCeph
    "microceph": "https://canonical-microceph.readthedocs.io/en/latest/",
    "microceph-install": "https://canonical-microceph.readthedocs.io/en/latest/how-to/install/",
    # Inference snaps
    "inference-snaps": "https://documentation.ubuntu.com/inference-snaps/",
    "gemma4": "https://documentation.ubuntu.com/inference-snaps/reference/snaps/",
}

# Keyword → doc key mapping for fuzzy lookups
_KEYWORD_MAP: list[tuple[list[str], str]] = [
    (["microcloud", "mc", "cluster"], "microcloud"),
    (["install", "initialise", "initialize", "setup", "set up"], "microcloud-install"),
    (["init", "bootstrap", "microcloud init"], "microcloud-init"),
    (["network", "ovn", "uplink", "vlan"], "microcloud-networking"),
    (["storage", "disk", "lvm", "ceph", "osd", "pool"], "microcloud-storage"),
    (["preseed", "unattended", "automated", "auto"], "microcloud-preseed"),
    (["requirement", "prereq", "hardware", "minimum"], "microcloud-requirements"),
    (["faq", "question", "common"], "microcloud-faq"),
    (["lxd", "container", "vm", "instance"], "lxd"),
    (["microceph", "distributed storage", "ceph"], "microceph"),
    (["inference", "snap", "gemma4", "llm", "model"], "gemma4"),
]


class DocFetcher:
    """Fetch and cache documentation pages for the AI agent."""

    def __init__(self, cache_dir: Path, cache_ttl_seconds: int = 3600):
        self.cache_dir = cache_dir / "doc_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_by_topic(self, topic: str) -> dict[str, Any]:
        """
        Fetch documentation relevant to the given topic string.

        The topic can be a doc key (e.g. 'microcloud-storage') or any
        natural-language phrase.  Returns a dict with:
            url, title, content (plain text, trimmed to ~3 000 chars)
        """
        doc_key = self._resolve_topic(topic)
        url = DOC_SOURCES.get(doc_key, DOC_SOURCES["microcloud"])
        return self._fetch_url(url, doc_key)

    def fetch_by_url(self, url: str) -> dict[str, Any]:
        """Fetch a specific URL directly."""
        return self._fetch_url(url, key=urlparse(url).path)

    def list_sources(self) -> list[dict[str, str]]:
        """Return the catalog of known documentation sources."""
        return [{"key": k, "url": v} for k, v in DOC_SOURCES.items()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_topic(self, topic: str) -> str:
        """Map a free-text topic to a doc key."""
        topic_lower = topic.lower()

        # Direct key match
        if topic_lower in DOC_SOURCES:
            return topic_lower

        # Keyword-based match (first hit wins)
        for keywords, key in _KEYWORD_MAP:
            if any(kw in topic_lower for kw in keywords):
                return key

        return "microcloud"

    def _cache_path(self, url: str) -> Path:
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        return self.cache_dir / f"{url_hash}.json"

    def _is_cache_valid(self, cache_path: Path) -> bool:
        if not cache_path.exists():
            return False
        age = time.time() - cache_path.stat().st_mtime
        return age < self.cache_ttl

    def _fetch_url(self, url: str, key: str = "") -> dict[str, Any]:
        cache_path = self._cache_path(url)

        if self._is_cache_valid(cache_path):
            try:
                with open(cache_path) as f:
                    logger.debug(f"Doc cache hit: {url}")
                    return json.load(f)
            except Exception:
                pass

        logger.info(f"Fetching documentation: {url}")
        try:
            resp = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "canonical-ai-lab-assistant/0.1"},
            )
            resp.raise_for_status()
            content = self._extract_text(resp.text)
            result = {
                "url": url,
                "key": key,
                "title": self._extract_title(resp.text),
                "content": content[:4000],  # keep context manageable
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": None,
            }
        except requests.RequestException as exc:
            logger.warning(f"Failed to fetch {url}: {exc}")
            result = {
                "url": url,
                "key": key,
                "title": "",
                "content": f"[Documentation unavailable: {exc}]",
                "fetched_at": None,
                "error": str(exc),
            }

        try:
            with open(cache_path, "w") as f:
                json.dump(result, f)
        except Exception:
            pass

        return result

    @staticmethod
    def _extract_title(html: str) -> str:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_text(html: str) -> str:
        """Very lightweight HTML → plain text conversion."""
        # Remove scripts and styles
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        # Remove all tags
        text = re.sub(r"<[^>]+>", " ", html)
        # Normalise whitespace
        text = re.sub(r"\s+", " ", text)
        # Collapse runs of blank lines
        text = re.sub(r"(\n\s*){3,}", "\n\n", text)
        return text.strip()
