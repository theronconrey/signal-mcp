from hollerback.signal_lint import detect_structural_markdown


def test_plain_prose_passes():
    assert detect_structural_markdown("Hey, running late — see you at 7.") is None


def test_long_plain_prose_passes():
    text = " ".join(["Lorem ipsum dolor sit amet."] * 200)
    assert detect_structural_markdown(text) is None


def test_url_with_underscore_passes():
    text = "Check https://example.com/some_path_here for details."
    assert detect_structural_markdown(text) is None


def test_stray_asterisk_passes():
    assert detect_structural_markdown("That was a *great* idea.") is None


def test_single_dash_line_passes():
    assert detect_structural_markdown("- one thing only") is None


def test_hash_mid_line_passes():
    assert detect_structural_markdown("I'll do 3 things: # step-numbering style") is None


def test_heading_rejected():
    result = detect_structural_markdown("# Summary\n\nSome content.")
    assert result is not None
    assert "heading" in result.lower()


def test_nested_heading_rejected():
    result = detect_structural_markdown("Intro.\n\n### Details\n\nMore.")
    assert result is not None
    assert "heading" in result.lower()


def test_code_fence_rejected():
    result = detect_structural_markdown("Run this:\n```\necho hi\n```")
    assert result is not None
    assert "fence" in result.lower()


def test_bulleted_list_rejected():
    result = detect_structural_markdown("Todo:\n- eggs\n- milk\n- bread")
    assert result is not None
    assert "list" in result.lower()


def test_asterisk_bullet_list_rejected():
    result = detect_structural_markdown("* first\n* second")
    assert result is not None
    assert "list" in result.lower()


def test_link_syntax_rejected():
    result = detect_structural_markdown("See [the docs](https://example.com).")
    assert result is not None
    assert "link" in result.lower()


def test_multiple_problems_reported_together():
    text = "# Title\n\nSee [link](https://x.com)."
    result = detect_structural_markdown(text)
    assert result is not None
    assert "heading" in result.lower()
    assert "link" in result.lower()
