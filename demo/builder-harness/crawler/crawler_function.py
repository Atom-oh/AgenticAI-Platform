"""Market-news crawler — external RSS -> platform datasource.

Design (wiki: news-crawler-design):
- Sources: public RSS feeds (Google News query feeds). Only metadata is
  stored — headline, short snippet, link, timestamp — never full articles
  (copyright posture).
- Schedule: EventBridge every 6h + on-demand via admin API.
- Sink: DynamoDB DS item with fixed id (upsert), capped at 18,000 chars,
  marked source="crawler" so the UI labels it 자동 수집.
- Every run writes an audit event (actor=crawler@platform).
"""
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

TABLE = os.environ.get("REGISTRY_TABLE", "agentic-book-demo-registry")
DS_ID = "feed0a01"                    # fixed hex id for the managed datasource
MAX_CHARS = 18000
KST = timezone(timedelta(hours=9))

FEEDS = [
    ("증시·시장", "https://news.google.com/rss/search?q=%EC%A6%9D%EC%8B%9C%20OR%20%EC%BD%94%EC%8A%A4%ED%94%BC&hl=ko&gl=KR&ceid=KR:ko"),
    ("금융정책", "https://news.google.com/rss/search?q=%EA%B8%88%EC%9C%B5%EC%9C%84%20OR%20%ED%95%9C%EA%B5%AD%EC%9D%80%ED%96%89&hl=ko&gl=KR&ceid=KR:ko"),
]

ddb = boto3.resource("dynamodb", region_name="ap-northeast-2").Table(TABLE)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", html.unescape(s or "")).strip()


def fetch_feed(url, limit=8):
    req = urllib.request.Request(url, headers={"User-Agent": "agentic-platform-crawler/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        root = ET.fromstring(r.read())
    out = []
    for item in root.iter("item"):
        title = strip_tags(item.findtext("title", ""))[:120]
        link = (item.findtext("link", "") or "").strip()
        pub = (item.findtext("pubDate", "") or "").strip()[:25]
        src = strip_tags(item.findtext("source", ""))[:30]
        if title:
            out.append({"title": title, "link": link, "pub": pub, "src": src})
        if len(out) >= limit:
            break
    return out


def handler(event, context):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    sections, total_items = [], 0
    for name, url in FEEDS:
        try:
            items = fetch_feed(url)
        except Exception as exc:
            print(f"feed failed ({name}): {exc}")
            sections.append(f"### {name}\n- (수집 실패 — 다음 주기에 재시도)")
            continue
        total_items += len(items)
        lines = [f"- {i['title']}" +
                 (f" ({i['src']})" if i['src'] else "") +
                 (f" — {i['pub']}" if i['pub'] else "")
                 for i in items]
        sections.append(f"### {name}\n" + "\n".join(lines))

    content = (
        f"# 시장 뉴스 브리핑 (자동 수집)\n\n"
        f"최근 수집: {now} · 항목 {total_items}건 · 출처: Google News RSS\n"
        f"저작권 고려로 헤드라인·출처·시각 메타데이터만 저장한다. 본문은 원 기사 참조.\n\n"
        + "\n\n".join(sections)
    )[:MAX_CHARS]

    ddb.put_item(Item={
        "pk": "DS", "sk": DS_ID,
        "name": "시장 뉴스 브리핑 (자동 수집)",
        "content": content,
        "ownerEmail": "crawler@platform", "team": "platform",
        "source": "crawler",
        "createdAt": Decimal(int(time.time())),
        "crawledAt": Decimal(int(time.time())),
    })
    ddb.put_item(Item={
        "pk": "AUDIT", "sk": f"{int(time.time()*1000):015d}-crawl",
        "actor": "crawler@platform", "action": "datasource.crawl",
        "target": DS_ID, "detail": f"{total_items} items @ {now}",
        "at": Decimal(int(time.time())),
    })
    print(f"crawled {total_items} items")
    return {"ok": True, "items": total_items}
