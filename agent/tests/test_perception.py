from navigator.perception import Observation


def test_render_text_lists_indexed_elements():
    obs = Observation(
        url="https://example.com",
        title="Example",
        scroll_y=0,
        scroll_height=2000,
        viewport_height=800,
        elements=[
            {"index": 0, "role": "link", "label": "Home", "value": "", "disabled": False},
            {"index": 1, "role": "text", "label": "Search", "value": "hello", "disabled": False},
        ],
    )
    text = obs.render_text()
    assert "https://example.com" in text
    assert '[0] link "Home"' in text
    assert '[1] text "Search" = "hello"' in text
    # Page is taller than viewport -> should hint that more content exists.
    assert "more content" in text


def test_render_text_truncates_long_lists():
    elements = [
        {"index": i, "role": "button", "label": f"b{i}", "value": "", "disabled": False}
        for i in range(200)
    ]
    obs = Observation("u", "t", 0, 100, 100, elements)
    text = obs.render_text(max_elements=50)
    assert "150 more elements" in text
