from __future__ import annotations

from scripts.template_text_smoke import iter_public_templates, scan_text


def test_smart_quote_search_notice_is_flagged() -> None:
    bad = "Showing wallets matching “{{ query_text }}”."
    findings = scan_text(bad, source="wallets.html")
    assert any(f.kind == "smart-quote" for f in findings)


def test_normalized_ascii_template_notice_passes() -> None:
    good = 'Showing wallets matching "{{ query_text }}".'
    assert scan_text(good, source="wallets.html") == []


def test_rendered_ascii_notice_passes() -> None:
    rendered = 'Showing wallets matching "alice".'
    assert scan_text(rendered, source="rendered", allow_jinja=False) == []


def test_raw_placeholder_leak_in_rendered_output_is_flagged() -> None:
    leaked = "Showing accepted work matching {{ query }}."
    findings = scan_text(leaked, source="rendered", allow_jinja=False)
    assert any(f.kind == "raw-placeholder" for f in findings)


def test_raw_placeholder_allowed_in_template_source() -> None:
    template = 'Showing accepted work matching "{{ query }}".'
    assert scan_text(template, allow_jinja=True) == []


def test_replacement_character_is_flagged() -> None:
    broken = "Showing wallets matching �Main�."
    findings = scan_text(broken)
    assert any(f.kind == "replacement-char" for f in findings)


def test_mojibake_sequence_is_flagged() -> None:
    moji = "Showing wallets matching â€œMainâ€."
    findings = scan_text(moji)
    assert any(f.kind == "mojibake" for f in findings)


def test_allow_marker_suppresses_finding() -> None:
    line = "intentional “curly” example {# template-text-smoke: allow #}"
    assert scan_text(line) == []


def test_line_number_is_reported() -> None:
    text = "ok line\nbad “quote” line\n"
    findings = scan_text(text)
    assert findings
    assert findings[0].line == 2


def test_public_templates_are_discoverable() -> None:
    templates = iter_public_templates()
    assert any(p.name == "wallets.html" for p in templates)
    assert all(p.suffix == ".html" for p in templates)
