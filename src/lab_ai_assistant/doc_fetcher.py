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
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known documentation sources
# ---------------------------------------------------------------------------

DOC_SOURCES: dict[str, str] = {
    # MicroCloud
    "microcloud": "https://canonical.com/microcloud/docs/latest/",
    "microcloud-install": "https://canonical.com/microcloud/docs/latest/how-to/install/",
    "microcloud-init": "https://canonical.com/microcloud/docs/latest/how-to/initialise/",
    "microcloud-networking": "https://canonical.com/microcloud/docs/latest/explanation/networking/",
    "microcloud-storage": "https://canonical.com/microcloud/docs/latest/explanation/storage/",
    "microcloud-preseed": "https://canonical.com/microcloud/docs/latest/how-to/preseed/",
    "microcloud-faq": "https://canonical.com/microcloud/docs/latest/reference/faq/",
    "microcloud-requirements": "https://canonical.com/microcloud/docs/latest/reference/requirements/",
    # LXD
    "lxd": "https://documentation.ubuntu.com/lxd/latest/",
    "lxd-install": "https://documentation.ubuntu.com/lxd/latest/installing/",
    "lxd-networks": "https://documentation.ubuntu.com/lxd/latest/explanation/networks/",
    "lxd-storage": "https://documentation.ubuntu.com/lxd/latest/explanation/storage/",
    # MicroCeph
    "microceph": "https://canonical-microceph.readthedocs.io/en/latest/",
    "microceph-install": "https://canonical-microceph.readthedocs.io/en/latest/how-to/install/",
    # Inference snaps
    "inference-snaps": "https://documentation.ubuntu.com/inference-snaps/",
    "gemma4": "https://documentation.ubuntu.com/inference-snaps/reference/snaps/",
}

# Last-resort fallback sources. The primary docs site is normally reachable
# (returns 200); these GitHub-raw mirrors are only tried if the live page is
# entirely unavailable (network error or non-2xx).
DOC_FALLBACK_SOURCES: dict[str, list[str]] = {
    "microcloud": ["https://raw.githubusercontent.com/canonical/microcloud/main/README.md"],
    "microcloud-install": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/how-to/install.md"
    ],
    "microcloud-init": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/how-to/initialize.md"
    ],
    "microcloud-networking": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/explanation/networking.md"
    ],
    "microcloud-storage": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/explanation/storage.md"
    ],
    "microcloud-preseed": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/how-to/initialize.md"
    ],
    "microcloud-faq": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/reference/faq.md"
    ],
    "microcloud-requirements": [
        "https://raw.githubusercontent.com/canonical/microcloud/main/doc/reference/requirements.md"
    ],
}

# Keyword → doc key mapping for fuzzy lookups
_KEYWORD_MAP: list[tuple[list[str], str]] = [
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
    (["microcloud", "mc", "cluster"], "microcloud"),
]

_GENERIC_KEYWORDS = {"microcloud", "mc", "cluster", "auto", "question", "common"}
_ALLOWED_DOC_HOSTS = {
    "canonical.com",
    "documentation.ubuntu.com",
    "canonical-microceph.readthedocs.io",
    "raw.githubusercontent.com",
}


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
        primary_url = DOC_SOURCES.get(doc_key, DOC_SOURCES["microcloud"])
        candidates = [primary_url, *DOC_FALLBACK_SOURCES.get(doc_key, [])]

        last_result: dict[str, Any] | None = None
        for url in candidates:
            result = self._fetch_url(url, doc_key)
            last_result = result
            if not result.get("error"):
                if url != primary_url:
                    result["title"] = result.get("title") or f"{doc_key} (fallback source)"
                    result["content"] = (
                        "[Using fallback documentation source due to access restrictions on documentation.ubuntu.com]\n\n"
                        + result.get("content", "")
                    )
                return result

        return last_result or self._fetch_url(primary_url, doc_key)

    def fetch_by_url(self, url: str) -> dict[str, Any]:
        """Fetch a specific URL directly."""
        if not self._is_allowed_url(url):
            return {
                "url": url,
                "key": urlparse(url).path,
                "title": "",
                "content": "[Documentation URL rejected by source policy]",
                "fetched_at": None,
                "error": "Only HTTPS URLs from approved Canonical documentation hosts are allowed",
            }
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

        # Score all keyword groups so a specific phrase such as "MicroCloud
        # networking" wins over the generic product name.
        scores: dict[str, int] = {}
        for keywords, key in _KEYWORD_MAP:
            for keyword in keywords:
                if not self._keyword_matches(topic_lower, keyword):
                    continue
                score = 1 if keyword in _GENERIC_KEYWORDS else len(keyword) + 10
                scores[key] = scores.get(key, 0) + score

        if scores:
            return max(scores, key=lambda candidate: scores[candidate])

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
                    cached = json.load(f)
                    if isinstance(cached, dict):
                        return cached
            except Exception:
                pass

        logger.info(f"Fetching documentation: {url}")
        try:
            resp = requests.get(
                url,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            if not self._is_allowed_url(resp.url):
                raise requests.RequestException(
                    f"Documentation redirect left the approved host set: {resp.url}"
                )
            body = resp.text
            content = self._extract_text(body)
            title = self._extract_title(body)
            if not title:
                title = self._extract_markdown_title(body)
            result = {
                "url": resp.url,
                "key": key,
                "title": title,
                "content": content[:6000],  # keep context manageable
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": None,
            }
        except requests.RequestException as exc:
            logger.warning(f"Failed to fetch {url}: {exc}")
            fallback_url = self._docs_url_to_github_raw(url)
            if fallback_url and fallback_url != url:
                logger.info(f"Trying fallback documentation source: {fallback_url}")
                try:
                    resp = requests.get(
                        fallback_url,
                        timeout=15,
                        headers={"User-Agent": "canonical-ai-lab-assistant/0.1"},
                    )
                    resp.raise_for_status()
                    body = resp.text
                    title = self._extract_markdown_title(body) or self._extract_title(body)
                    result = {
                        "url": fallback_url,
                        "key": key,
                        "title": title,
                        "content": self._extract_text(body)[:6000],
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "error": None,
                    }
                except requests.RequestException:
                    result = {
                        "url": url,
                        "key": key,
                        "title": "",
                        "content": f"[Documentation unavailable: {exc}]",
                        "fetched_at": None,
                        "error": str(exc),
                    }
            else:
                result = {
                    "url": url,
                    "key": key,
                    "title": "",
                    "content": f"[Documentation unavailable: {exc}]",
                    "fetched_at": None,
                    "error": str(exc),
                }

        if not result.get("error"):
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
        """Lightweight HTML → plain text conversion.

        Isolates the primary content region first (Sphinx/Furo themes wrap the
        real documentation in <article role="main">). This strips the sidebar,
        header, and footer navigation that otherwise drowns out the signal.
        """
        main = DocFetcher._extract_main_content(html)
        # Remove scripts and styles
        main = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", main, flags=re.S | re.I)
        # Remove all tags
        text = re.sub(r"<[^>]+>", " ", main)
        # Normalise whitespace
        text = re.sub(r"\s+", " ", text)
        # Collapse runs of blank lines
        text = re.sub(r"(\n\s*){3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _extract_main_content(html: str) -> str:
        """Return the primary content region of a doc page, if identifiable.

        Tries the most specific markers first and falls back to the full
        document so non-Sphinx pages (e.g. raw markdown) still work.
        """
        patterns = (
            r'<article[^>]*\brole=["\']main["\'][^>]*>(.*?)</article>',
            r"<main[^>]*>(.*?)</main>",
            r'<div[^>]*\brole=["\']main["\'][^>]*>(.*?)</div>',
        )
        for pattern in patterns:
            match = re.search(pattern, html, re.S | re.I)
            if match and match.group(1).strip():
                return match.group(1)
        return html

    @staticmethod
    def _extract_markdown_title(text: str) -> str:
        """Extract title from markdown content when HTML title tag is missing."""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return ""

    @staticmethod
    def _keyword_matches(text: str, keyword: str) -> bool:
        if " " in keyword:
            return keyword in text
        return bool(re.search(rf"\b{re.escape(keyword)}\b", text))

    @staticmethod
    def _is_allowed_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and (parsed.hostname or "") in _ALLOWED_DOC_HOSTS

    @staticmethod
    def _docs_url_to_github_raw(url: str) -> str:
        """Translate docs.ubuntu.com MicroCloud URLs to raw GitHub markdown URLs."""
        parsed = urlparse(url)
        if "documentation.ubuntu.com" not in parsed.netloc:
            return ""

        path = parsed.path.strip("/")
        if not path.startswith("microcloud"):
            return ""

        suffix = path[len("microcloud") :].strip("/")
        if not suffix:
            return "https://raw.githubusercontent.com/canonical/microcloud/main/README.md"

        for prefix in ("en/latest/", "latest/"):
            if suffix.startswith(prefix):
                suffix = suffix[len(prefix) :]
                break

        if suffix.endswith(".md"):
            return f"https://raw.githubusercontent.com/canonical/microcloud/main/doc/{suffix}"

        return f"https://raw.githubusercontent.com/canonical/microcloud/main/doc/{suffix}.md"
