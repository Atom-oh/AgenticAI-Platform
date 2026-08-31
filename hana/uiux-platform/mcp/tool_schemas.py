def _tool(name, description, properties=None, required=None):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": required or []}}


TOOL_SCHEMAS = [
    _tool("list_design_tokens", "Return the Hana design tokens (color/type/space) and component index."),
    _tool("search_assets", "Search the design asset registry by name.",
          {"query": {"type": "string", "description": "substring to match asset names"}}, ["query"]),
    _tool("get_component", "Get one component's semantic metadata by Figma node id.",
          {"node_id": {"type": "string"}}, ["node_id"]),
    _tool("get_brand_guideline", "Return Hana brand palette, font, and draft-generation rules."),
    _tool("list_skills", "List org-shared skills in the skill registry."),
    _tool("get_skill", "Fetch a skill's SKILL.md content (latest version).",
          {"name": {"type": "string"}}, ["name"]),
]
