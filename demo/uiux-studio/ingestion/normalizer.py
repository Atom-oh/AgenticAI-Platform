"""Normalize a Figma file JSON (GET /v1/files/{key}) into design tokens + components.

Seed-file conventions: page 'Design Tokens' holds RECTANGLE 'color/<name>',
TEXT 'type/<name>', FRAME 'space/<name>' (width=px); page 'Components' holds
COMPONENT nodes.
"""


def _hex(c: dict) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(c.get("r", 0) * 255), round(c.get("g", 0) * 255), round(c.get("b", 0) * 255))


def _walk(node: dict):
    yield node
    for child in node.get("children", []) or []:
        yield from _walk(child)


def normalize_figma_file(file_json: dict) -> dict:
    tokens = {"color": {}, "type": {}, "space": {}}
    components = []
    for page in file_json.get("document", {}).get("children", []) or []:
        if page.get("name") == "Design Tokens":
            for n in _walk(page):
                name = n.get("name", "")
                kind, _, key = name.partition("/")
                if not key:
                    continue
                if kind == "color" and n.get("type") == "RECTANGLE":
                    solid = next((f for f in n.get("fills", []) if f.get("type") == "SOLID"), None)
                    if solid:
                        tokens["color"][key] = _hex(solid["color"])
                elif kind == "type" and n.get("type") == "TEXT":
                    s = n.get("style", {})
                    tokens["type"][key] = {
                        "fontFamily": s.get("fontFamily"),
                        "fontSize": round(s.get("fontSize", 0)),
                        "fontWeight": s.get("fontWeight"),
                    }
                elif kind == "space" and n.get("type") == "FRAME":
                    tokens["space"][key] = round(n.get("absoluteBoundingBox", {}).get("width", 0))
        elif page.get("name") == "Components":
            for n in _walk(page):
                if n.get("type") == "COMPONENT":
                    components.append({
                        "name": n.get("name"), "node_id": n.get("id"),
                        "description": n.get("description", "")})
    return {"tokens": tokens, "components": components}
