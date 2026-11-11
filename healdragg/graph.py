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
    # Контекст общения: только реальные ответы и упоминания в сообщениях,
    # а не предположение, что участники общего чата общаются между собой.
    peer_chats: dict[int, dict[int, dict[str, Any]]] = {}
    messages = store.query(
        """SELECT m.reply_to_peer, m.mentions, m.date, g.id AS group_id,
                  g.tg_id, g.title, g.link
             FROM messages m JOIN groups g ON g.id = m.group_id
            WHERE m.target_id = ? AND (m.reply_to_peer IS NOT NULL OR m.mentions IS NOT NULL)""",
        (target_pk,),
    )
    for message in messages:
        ids = set()
        if message.get("reply_to_peer"):
            ids.add(int(message["reply_to_peer"]))
        for part in (message.get("mentions") or "").split(","):
            if part.isdigit():
                ids.add(int(part))
        for peer_id in ids:
            chats = peer_chats.setdefault(peer_id, {})
            group_id = int(message["group_id"])
            row = chats.setdefault(group_id, {
                "tg_id": message["tg_id"], "title": message.get("title") or "Чат",
                "link": message.get("link"), "interactions": 0, "last": 0,
            })
            row["interactions"] += 1
            row["last"] = max(row["last"], int(message.get("date") or 0))

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
                    "chats": sorted(
                        peer_chats.get(int(edge["tg_id"]), {}).values(),
                        key=lambda row: (-row["interactions"], -row["last"]),
                    ),
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

    # Связи подарков основаны на сохранённых наблюдениях профилей. NFT могли
    # менять владельца: это не доказательство прямой передачи или текущего владения.
    by_tg = {int(node["tg_id"]): node for node in nodes if node["type"] == "peer"}
    for relation in store.profile_gift_relations(int(target["tg_user_id"]), limit=max_peers):
        peer_id = int(relation["peer_id"])
        peer = by_tg.get(peer_id)
        if peer is None:
            peer = {
                "id": f"gift-user-{peer_id}", "type": "peer",
                "label": relation.get("name") or (f"@{relation['username']}" if relation.get("username") else f"ID {peer_id}"),
                "tg_id": peer_id, "weight": 1,
                "meta": {"username": relation.get("username")},
            }
            nodes.append(peer)
            by_tg[peer_id] = peer
        received, sent = int(relation["received"] or 0), int(relation["sent"] or 0)
        peer["meta"]["gifts_received"] = received
        peer["meta"]["gifts_sent"] = sent
        peer["weight"] = max(peer["weight"], received + sent)
        if received:
            links.append({"source": peer["id"], "target": "target", "kind": "gift_in", "weight": received})
        if sent:
            links.append({"source": "target", "target": peer["id"], "kind": "gift_out", "weight": sent})

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


def build_global_graph(
    store: Store,
    *,
    max_targets: int = 60,
    max_groups: int = 120,
    max_peers: int = 200,
) -> dict[str, Any]:
    """Общая карта: цели, их чаты и собеседники — всё в одном графе.

    Нужна, чтобы видеть картину целиком: кто с кем в одних чатах, кто кому
    отвечает, где пересекаются разные цели. Строится только по локальной базе.
    """
    targets = store.query(
        """
        SELECT t.*, coalesce(s.messages, 0) AS msg_count, s.last_date
          FROM targets t
          LEFT JOIN (
              SELECT target_id, COUNT(*) AS messages, MAX(date) AS last_date
                FROM messages GROUP BY target_id
          ) s ON s.target_id = t.id
         ORDER BY coalesce(s.messages, 0) DESC, coalesce(s.last_date, 0) DESC
         LIMIT ?
        """,
        (max(1, int(max_targets)),),
    )
    target_pks = [int(row["id"]) for row in targets]
    if not target_pks:
        return {"nodes": [], "links": [], "stats": {"targets": 0, "chats": 0, "peers": 0, "links": 0}}

    marks = ",".join("?" for _ in target_pks)
    groups = store.query(
        f"""
        SELECT m.group_id, COUNT(*) AS n, COUNT(DISTINCT m.target_id) AS targets,
               g.tg_id, g.title, g.kind, g.username, g.members_count
          FROM messages m JOIN groups g ON g.id = m.group_id
         WHERE m.target_id IN ({marks})
         GROUP BY m.group_id
         ORDER BY n DESC, targets DESC
         LIMIT ?
        """,
        (*target_pks, max(1, int(max_groups))),
    )
    group_pks = [int(row["group_id"]) for row in groups]

    peers: list[dict[str, Any]] = []
    if group_pks:
        peers = store.query(
            f"""
            SELECT e.peer_id, SUM(e.weight) AS weight, SUM(e.replies) AS replies,
                   SUM(e.mentions) AS mentions, max(p.tg_id) AS tg_id,
                   max(p.username) AS username, max(p.first_name) AS first_name,
                   max(p.last_name) AS last_name
              FROM edges e JOIN peers p ON p.id = e.peer_id
             WHERE e.target_id IN ({marks})
             GROUP BY e.peer_id
             ORDER BY weight DESC
             LIMIT ?
            """,
            (*target_pks, max(1, int(max_peers))),
        )

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    for row in targets:
        node_id = f"t{int(row['id'])}"
        nodes.append(
            {
                "id": node_id,
                "type": "target",
                "label": _person_name(row),
                "tg_id": int(row["tg_user_id"]),
                "weight": max(1, int(row.get("msg_count") or 0)),
                "meta": {
                    "username": row.get("username"),
                    "messages": int(row.get("msg_count") or 0),
                    "last": row.get("last_date"),
                },
            }
        )

    group_index: dict[int, str] = {}
    for row in groups:
        node_id = f"g{int(row['group_id'])}"
        group_index[int(row["group_id"])] = node_id
        nodes.append(
            {
                "id": node_id,
                "type": "chat",
                "label": row.get("title") or "чат",
                "tg_id": int(row["tg_id"] or 0),
                "weight": max(1, int(row["n"] or 0)),
                "meta": {
                    "kind": row.get("kind"),
                    "username": row.get("username"),
                    "members": row.get("members_count"),
                    "targets": int(row["targets"] or 0),
                },
            }
        )

    peer_index: dict[int, str] = {}
    for row in peers:
        node_id = f"p{int(row['peer_id'])}"
        peer_index[int(row["peer_id"])] = node_id
        nodes.append(
            {
                "id": node_id,
                "type": "peer",
                "label": _person_name(row),
                "tg_id": int(row["tg_id"] or 0),
                "weight": max(1, int(row.get("weight") or 0)),
                "meta": {
                    "username": row.get("username"),
                    "replies": int(row.get("replies") or 0),
                    "mentions": int(row.get("mentions") or 0),
                },
            }
        )

    # цель → чат (сколько сообщений) и цель → собеседник (вес связи)
    for row in store.query(
        f"""
        SELECT target_id, group_id, COUNT(*) AS n, MAX(date) AS last
          FROM messages WHERE target_id IN ({marks})
         GROUP BY target_id, group_id
        """,
        tuple(target_pks),
    ):
        group_node = group_index.get(int(row["group_id"]))
        if not group_node:
            continue
        links.append(
            {
                "source": f"t{int(row['target_id'])}",
                "target": group_node,
                "kind": "writes",
                "weight": max(1, int(row["n"] or 0)),
                "last": row.get("last"),
            }
        )

    for row in store.query(
        f"""
        SELECT target_id, peer_id, weight, replies, mentions, last_seen
          FROM edges WHERE target_id IN ({marks})
        """,
        tuple(target_pks),
    ):
        peer_node = peer_index.get(int(row["peer_id"]))
        if not peer_node:
            continue
        links.append(
            {
                "source": f"t{int(row['target_id'])}",
                "target": peer_node,
                "kind": "talks",
                "weight": max(1, int(row["weight"] or 0)),
                "replies": int(row["replies"] or 0),
                "mentions": int(row["mentions"] or 0),
                "last": row.get("last_seen"),
            }
        )

    if group_pks:
        group_marks = ",".join("?" for _ in group_pks)
        for row in store.query(
            f"""
            SELECT group_id, reply_to_peer AS peer_id, COUNT(*) AS n
              FROM messages
             WHERE target_id IN ({marks}) AND group_id IN ({group_marks})
               AND reply_to_peer IS NOT NULL
             GROUP BY group_id, reply_to_peer
            """,
            (*target_pks, *group_pks),
        ):
            group_node = group_index.get(int(row["group_id"]))
            peer_node = peer_index.get(int(row["peer_id"]))
            if not group_node or not peer_node:
                continue
            links.append(
                {
                    "source": group_node,
                    "target": peer_node,
                    "kind": "in_chat",
                    "weight": max(1, int(row["n"] or 0)),
                }
            )

    return {
        "nodes": nodes,
        "links": links,
        "stats": {
            "targets": len(targets),
            "chats": len(groups),
            "peers": len(peers),
            "links": len(links),
            "built_at": now_ts(),
        },
    }
