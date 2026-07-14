from lab_ai_assistant.doc_fetcher import DocFetcher


def test_specific_topic_beats_generic_product_name(tmp_path) -> None:
    fetcher = DocFetcher(tmp_path)

    assert fetcher._resolve_topic("MicroCloud networking and OVN") == ("microcloud-networking")
    assert fetcher._resolve_topic("MicroCloud storage and Ceph") == "microcloud-storage"
    assert fetcher._resolve_topic("MicroCeph cluster") == "microceph"


def test_direct_url_policy_blocks_unapproved_sources(tmp_path) -> None:
    fetcher = DocFetcher(tmp_path)

    assert fetcher.fetch_by_url("http://canonical.com/microcloud/docs/latest/")["error"]
    assert fetcher.fetch_by_url("https://example.com/private")["error"]


def test_main_content_extraction_removes_navigation(tmp_path) -> None:
    fetcher = DocFetcher(tmp_path)
    html = """
    <html><nav>Navigation noise</nav>
    <article role="main"><h1>Requirements</h1><p>Three members.</p></article>
    </html>
    """

    text = fetcher._extract_text(html)

    assert text == "Requirements Three members."
    assert "Navigation" not in text
