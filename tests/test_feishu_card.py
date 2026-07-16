from __future__ import annotations

import json

from takopi_transport_feishu.card import build_card, card_message_content


def test_simple_text_card() -> None:
    card = build_card("Hello, World!")
    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is False
    elements = card["body"]["elements"]
    assert len(elements) == 1
    assert elements[0]["tag"] == "markdown"
    assert elements[0]["content"] == "Hello, World!"


def test_code_block_becomes_collapsible_panel() -> None:
    text = "Before\n\n```python\nprint('hi')\n```\n\nAfter"
    card = build_card(text)
    elements = card["body"]["elements"]
    # text before -> markdown, code -> collapsible_panel, text after -> markdown
    assert len(elements) == 3
    assert elements[0]["tag"] == "markdown"
    assert "Before" in elements[0]["content"]
    assert elements[1]["tag"] == "collapsible_panel"
    assert elements[1]["expanded"] is True
    panel_md = elements[1]["elements"][0]["content"]
    assert "print('hi')" in panel_md
    assert elements[2]["tag"] == "markdown"
    assert "After" in elements[2]["content"]


def test_formula_converted_to_latex_code_block() -> None:
    text = "欧拉公式：$$e^{i\\pi} + 1 = 0$$\n\n连接了五个重要常数。"
    card = build_card(text)
    elements = card["body"]["elements"]
    # text before -> markdown, latex formula -> collapsible_panel, text after -> markdown
    assert len(elements) == 3
    assert elements[0]["tag"] == "markdown"
    assert "欧拉公式" in elements[0]["content"]
    assert elements[1]["tag"] == "collapsible_panel"
    panel_md = elements[1]["elements"][0]["content"]
    assert "e^{i\\pi}" in panel_md
    assert "```latex" in panel_md
    assert elements[2]["tag"] == "markdown"
    assert "连接了五个重要常数" in elements[2]["content"]


def test_quad_formula_converted() -> None:
    text = "能量方程：$$$$\nE = mc^2\n$$$$"
    card = build_card(text)
    elements = card["body"]["elements"]
    # text before -> markdown, formula -> collapsible_panel
    assert len(elements) == 2
    assert elements[0]["tag"] == "markdown"
    assert "能量方程" in elements[0]["content"]
    assert elements[1]["tag"] == "collapsible_panel"
    panel_md = elements[1]["elements"][0]["content"]
    assert "E = mc^2" in panel_md


def test_summary_extraction() -> None:
    card = build_card("Done · engine · 15s\n\nbody text")
    assert card["config"]["summary"]["content"] == "Done · engine · 15s"


def test_long_text_split() -> None:
    text = "line\n" * 3000
    card = build_card(text)
    elements = card["body"]["elements"]
    assert len(elements) > 1
    for el in elements:
        assert el["tag"] == "markdown"


def test_empty_text() -> None:
    card = build_card("")
    assert card["schema"] == "2.0"
    elements = card["body"]["elements"]
    assert len(elements) == 1
    assert elements[0]["content"] == " "


def test_card_message_content_serialization() -> None:
    card = build_card("test")
    content = card_message_content(card)
    parsed = json.loads(content)
    assert parsed["schema"] == "2.0"
    assert parsed["body"]["elements"][0]["content"] == "test"


def test_streaming_mode() -> None:
    card = build_card("thinking...", streaming=True)
    assert card["config"]["streaming_mode"] is True


def test_explicit_summary() -> None:
    card = build_card("body", summary="Custom Summary")
    assert card["config"]["summary"]["content"] == "Custom Summary"


def test_stop_button() -> None:
    card = build_card("running...", show_stop_button=True)
    elements = card["body"]["elements"]
    button = elements[-1]
    assert button["tag"] == "button"
    assert button["type"] == "danger"
    assert button["behaviors"][0]["value"]["cmd"] == "stop"


def test_no_stop_button_by_default() -> None:
    card = build_card("done")
    elements = card["body"]["elements"]
    for el in elements:
        assert el["tag"] != "button"
