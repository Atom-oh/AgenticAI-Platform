"""NEXUS platform tools — AgentCore Gateway Lambda target.

Exposed as central MCP tools (inbound auth: Cognito JWT via Gateway):
  search_agents(query)   — approved-agent catalog search
  get_wiki_page(slug)    — AI wiki page fetch
  query_ontology()       — organization entity/relation graph
"""
import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

ddb = boto3.resource("dynamodb", region_name="ap-northeast-2").Table(
    os.environ.get("REGISTRY_TABLE", "agentic-book-demo-registry"))


def _plain(o):
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    return o


def _list(pk):
    return [_plain(i) for i in ddb.query(
        KeyConditionExpression=Key("pk").eq(pk)).get("Items", [])]


def handler(event, context):
    tool = ""
    cc = getattr(context, "client_context", None)
    if cc and getattr(cc, "custom", None):
        tool = cc.custom.get("bedrockAgentCoreToolName", "")
    tool = tool.split("___")[-1]
    args = event if isinstance(event, dict) else {}

    if tool == "search_agents":
        q = (args.get("query") or "").lower()
        rows = [{"id": a["sk"], "name": a["name"],
                 "description": a.get("description", ""), "team": a.get("team", "")}
                for a in _list("AGENT") if a.get("status", "APPROVED") == "APPROVED"
                and (not q or q in a["name"].lower()
                     or q in a.get("description", "").lower())]
        return {"agents": rows[:10]}

    if tool == "get_wiki_page":
        slug = args.get("slug") or ""
        it = ddb.get_item(Key={"pk": "WIKI", "sk": slug}).get("Item")
        if not it:
            pages = [{"slug": w["sk"], "title": w["title"]} for w in _list("WIKI")]
            return {"error": "page not found", "available": pages[:20]}
        it = _plain(it)
        return {"slug": slug, "title": it["title"], "markdown": it["markdown"][:8000]}

    if tool == "query_ontology":
        types = {t["sk"]: t["name"] for t in _list("ONT_TYPE")}
        ents = _list("ONT_ENT")
        emap = {e["sk"]: e["name"] for e in ents}
        return {"entities": [{"type": types.get(e.get("typeId"), "?"),
                              "name": e["name"], "attrs": e.get("attrs", {})}
                             for e in ents[:60]],
                "relations": [{"from": emap.get(r.get("fromId"), "?"),
                               "relation": r.get("relation", ""),
                               "to": emap.get(r.get("toId"), "?")}
                              for r in _list("ONT_REL")[:80]]}

    return {"error": f"unknown tool: {tool}"}
