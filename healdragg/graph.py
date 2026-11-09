"""Граф связей цели: люди ↔ чаты ↔ собеседники.

Данные берутся только из локальной базы (``messages`` и ``edges``), поэтому
граф можно строить офлайн — это же используется и в APK-мониторе.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Store
from .util import now_ts

WEB_DIR = Path(__file__).parent / "web"
GRAPH_JS = WEB_DIR / "graph.js"


def _person_name(row: dict[str, Any]) -> str:
    full = " ".join(p for p in (row.get("first_name"), row.get("last_name")) if p).strip()
    if full:
        return full
    if row.get("username"):
        return f"@{row['username']}"
    return f"ID {row.get('tg_id')}"


def build_graph(
    store: Store,
    target_pk: int,
    *,
    max_peers: int = 200,
    max_chats: int = 400,
) -> dict[str, Any]:
    target = store.query_one("SELECT * FROM targets WHERE id = ?", (target_pk,))
    if not target:
        raise KeyError(target_pk)

    groups = store.all_target_groups(target_pk)[:max_chats]
    counts = store.chat_activity(target_pk)
    edges = store.target_edges(target_pk, limit=max_peers)
    stats = store.activity_stats(target_pk)

    nodes: list[dict[str, Any]] = [
        {
            "id": "target",
            "type": "target",
            "label": _person_name(target),
            "tg_id": int(target["tg_user_id"]),
            "weight": max(1, stats.get("messages", 0)),
            "meta": {
                "username": target.get("username"),
                "chats": stats.get("chats", 0),
                "messages": stats.get("messages", 0),
                "peers": stats.get("peers", 0),
                "replies": stats.get("replies", 0),
                "last": stats.get("last_date"),
            },
        }
    ]
    links: list[dict[str, Any]] = []

    for group in groups:
        group_pk = int(group.get("group_id") or group.get("id"))
        node_id = f"g{group_pk}"
        count = int(counts.get(group_pk, 0))
        status = group.get("status") or "member"
        nodes.append(
            {
                "id": node_id,
                "type": "chat",
                "label": group.get("title") or "чат",
                "tg_id": int(group["tg_id"]),
                "weight": max(1, count),
                "meta": {
                    "kind": group.get("kind"),
                    "status": status,
                    "private": not bool(group.get("is_public")),
                    "members": group.get("members_count"),
                    "messages": group.get("messages_total"),
                    "last": group.get("last_message_at"),
                    "link": group.get("link"),
                    "my_messages": count,
                },
            }
        )
        links.append(
            {
                "source": "target",
                "target": node_id,
                "kind": "chat",
                "weight": max(1, count),
            }
        )

    for edge in edges:
        node_id = f"p{edge['peer_id']}"
        nodes.append(
            {
                "id": node_id,
                "type": "peer",
                "label": _person_name(edge),
                "tg_id": int(edge["tg_id"]),
                "weight": max(1, int(edge.get("weight") or 1)),
                "meta": {
                    "username": edge.get("username"),
                    "replies": edge.get("replies", 0),
                    "mentions": edge.get("mentions", 0),
                    "last": edge.get("last_seen"),
                    "weight": edge.get("weight", 0),
                },
            }
        )
        links.append(
            {
                "source": "target",
                "target": node_id,
                "kind": "peer",
                "weight": max(1, int(edge.get("weight") or 1)),
                "replies": edge.get("replies", 0),
                "mentions": edge.get("mentions", 0),
            }
        )

    return {
        "target": {
            "tg_id": int(target["tg_user_id"]),
            "username": target.get("username"),
            "name": _person_name(target),
        },
        "stats": stats,
        "nodes": nodes,
        "edges": links,
        "generated_at": now_ts(),
    }


def top_peers(store: Store, target_pk: int, *, limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for edge in store.target_edges(target_pk, limit=limit):
        out.append(
            {
                "name": _person_name(edge),
                "tg_id": int(edge["tg_id"]),
                "username": edge.get("username"),
                "weight": int(edge.get("weight") or 0),
                "replies": int(edge.get("replies") or 0),
                "mentions": int(edge.get("mentions") or 0),
                "last_seen": edge.get("last_seen"),
            }
        )
    return out


def render_html(graph: dict[str, Any], *, title: str = "Healdragg · сеть связей") -> str:
    """Автономная HTML-страница: JSON внутри, без CDN и интернета."""
    script = GRAPH_JS.read_text(encoding="utf-8") if GRAPH_JS.exists() else ""
    data = json.dumps(graph, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; background: #07090f; color: #e8f0ff;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  #stage {{ position: fixed; inset: 0; }}
  canvas {{ width: 100%; height: 100%; display: block; cursor: grab; }}
  .hud {{ position: fixed; z-index: 5; padding: 10px 14px; border: 1px solid #1d2b44;
    border-radius: 14px; background: rgba(10, 15, 26, .82); backdrop-filter: blur(8px); }}
  .hud.top {{ top: 14px; left: 14px; }}
  .hud.stats {{ bottom: 14px; left: 14px; font-size: 12px; color: #8fa6c8; }}
  .hud h1 {{ margin: 0 0 4px; font-size: 15px; letter-spacing: .06em; text-transform: uppercase;
    color: #00f5d4; }}
  .hud .sub {{ font-size: 12px; color: #8fa6c8; }}
  .legend {{ display: flex; gap: 12px; margin-top: 8px; font-size: 12px; }}
  .dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; }}
  #panel {{ position: fixed; top: 14px; right: 14px; width: 320px; max-height: calc(100% - 28px);
    overflow: auto; padding: 16px; border: 1px solid #1d2b44; border-radius: 16px;
    background: rgba(10, 15, 26, .92); backdrop-filter: blur(10px); display: none; z-index: 6; }}
  #panel.open {{ display: block; }}
  #panel h2 {{ margin: 0 0 6px; font-size: 16px; }}
  #panel .tag {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px;
    border: 1px solid #27405f; color: #9db7d8; margin: 0 6px 8px 0; }}
  #panel dl {{ display: grid; grid-template-columns: auto 1fr; gap: 6px 10px; font-size: 12px; }}
  #panel dt {{ color: #7c93b3; }}
  #panel dd {{ margin: 0; }}
  #panel a {{ color: #00f5d4; }}
  #panel .close {{ position: absolute; top: 10px; right: 12px; cursor: pointer; color: #7c93b3; }}
  .hint {{ position: fixed; bottom: 14px; right: 14px; font-size: 11px; color: #5c7392; z-index: 5; }}
</style>
</head>
<body>
<div id="stage"><canvas id="graph"></canvas></div>
<div class="hud top">
  <h1>{title}</h1>
  <div class="sub">{graph["target"]["name"]} · <span id="stats"></span></div>
  <div class="legend">
    <span><i class="dot" style="background:#ff2e63"></i>цель</span>
    <span><i class="dot" style="background:#00f5d4"></i>чат</span>
    <span><i class="dot" style="background:#7b61ff"></i>приватный</span>
    <span><i class="dot" style="background:#fee440"></i>собеседник</span>
  </div>
</div>
<div id="panel"><span class="close">✕</span><h2></h2><div class="body"></div></div>
<div class="hud stats" id="counters"></div>
<div class="hint">drag · zoom · click</div>
<script>window.__HEALDRAGG_GRAPH__ = {data};</script>
<script>{script}</script>
<script>
  const g = new HealdraggGraph(document.getElementById('graph'), window.__HEALDRAGG_GRAPH__);
  g.mount();
</script>
</body>
</html>
"""
