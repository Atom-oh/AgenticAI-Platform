from ingestion.normalizer import normalize_figma_file

SAMPLE = {
    "name": "Hana Design System",
    "document": {
        "children": [
            {
                "name": "Design Tokens",
                "type": "CANVAS",
                "children": [
                    {"type": "RECTANGLE", "name": "color/primary",
                     "fills": [{"type": "SOLID", "color": {"r": 0.0, "g": 0.5176, "b": 0.5215}}]},
                    {"type": "TEXT", "name": "type/heading",
                     "style": {"fontFamily": "Noto Sans KR", "fontSize": 22.0, "fontWeight": 700}},
                    {"type": "FRAME", "name": "space/md",
                     "absoluteBoundingBox": {"width": 16.0, "height": 8.0}, "children": []},
                ],
            },
            {
                "name": "Components",
                "type": "CANVAS",
                "children": [
                    {"type": "COMPONENT", "id": "12:34", "name": "Button/Primary",
                     "description": "Primary CTA. 56px height, radius 14, bg color/primary."},
                    {"type": "FRAME", "id": "12:99", "name": "scratch", "children": []},
                ],
            },
        ]
    },
}


def test_extracts_color_tokens_as_hex():
    out = normalize_figma_file(SAMPLE)
    assert out["tokens"]["color"]["primary"] == "#008485"


def test_extracts_type_and_space_tokens():
    out = normalize_figma_file(SAMPLE)
    assert out["tokens"]["type"]["heading"] == {
        "fontFamily": "Noto Sans KR", "fontSize": 22, "fontWeight": 700}
    assert out["tokens"]["space"]["md"] == 16


def test_extracts_components_only():
    out = normalize_figma_file(SAMPLE)
    assert out["components"] == [{
        "name": "Button/Primary", "node_id": "12:34",
        "description": "Primary CTA. 56px height, radius 14, bg color/primary."}]


def test_missing_pages_yield_empty():
    out = normalize_figma_file({"document": {"children": []}})
    assert out == {"tokens": {"color": {}, "type": {}, "space": {}}, "components": []}
