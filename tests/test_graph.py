from __future__ import annotations

from pathlib import Path

from healdragg.db import Store
from healdragg.demo import DEMO_TARGET, seed_demo
from healdragg.graph import build_graph, render_html, top_peers


def _demo(tmp_path: Path) -> tuple[Store, dict]:
    store = Store(tmp_path / "graph.db")
    seed_demo(store, total=15)
    target = store.get_target(DEMO_TARGET)
    assert target
    return store, target


def test_demo_graph_has_all_node_types(tmp_path: Path) -> None:
    store, target = _demo(tmp_path)
    try:
        graph = build_graph(store, int(target["id"]))
        kinds = {node["type"] for node in graph["nodes"]}
        assert {"target", "chat", "peer"} <= kinds
        assert graph["stats"]["messages"] > 0
        assert any(edge["kind"] == "peer" for edge in graph["edges"])
        peer_nodes = [n for n in graph["nodes"] if n["type"] == "peer"]
        assert peer_nodes and peer_nodes[0]["label"]
        chat_nodes = [n for n in graph["nodes"] if n["type"] == "chat"]
        assert any(n["meta"].get("link", "").startswith("https://t.me/") for n in chat_nodes)
    finally:
        store.close()


def test_top_peers_sorted(tmp_path: Path) -> None:
    store, target = _demo(tmp_path)
    try:
        peers = top_peers(store, int(target["id"]), limit=5)
        assert 0 < len(peers) <= 5
        weights = [p["weight"] for p in peers]
        assert weights == sorted(weights, reverse=True)
    finally:
        store.close()


def test_render_html_is_standalone(tmp_path: Path) -> None:
    store, target = _demo(tmp_path)
    try:
        html = render_html(build_graph(store, int(target["id"])))
        assert "HealdraggGraph" in html
        assert "https://t.me/" in html
        assert "<script src=" not in html
    finally:
        store.close()


def test_peer_has_only_chats_with_actual_interactions(tmp_path: Path) -> None:
    store = Store(tmp_path / "peer.db")
    target = store.upsert_target(42, first_name="Цель")
    peer = store.upsert_peer(99, first_name="Аня")
    first = store.upsert_group(100, title="Первый")
    second = store.upsert_group(101, title="Без общения")
    store.add_message(target["id"], first, 1, date=100, reply_to_peer=99, text="ответ")
    store.add_message(target["id"], first, 2, date=101, mentions="99,777", text="привет")
    store.add_message(target["id"], second, 3, date=102, text="другой чат")
    store.bump_edge(target["id"], peer, replies=1, mentions=1, ts=101)
    graph = build_graph(store, target["id"])
    node = next(n for n in graph["nodes"] if n["type"] == "peer")
    assert node["label"] == "Аня"
    assert [(g["title"], g["interactions"]) for g in node["meta"]["chats"]] == [("Первый", 2)]
    store.close()


def test_graph_includes_directional_gift_observations_without_hidden_senders(tmp_path: Path) -> None:
    store = Store(tmp_path / "gift-graph.db")
    target = store.upsert_target(42, first_name="Alice")
    peer = store.upsert_peer(99, first_name="Bob")
    store.bump_edge(target["id"], peer, replies=1, ts=100)
    store.add_gift(42, saved_id=1, gift_id=1, title="Gift", from_tg_id=99, from_name="Bob")
    store.add_gift(99, saved_id=2, gift_id=1, title="Gift", from_tg_id=42, owner_name="Bob")
    store.add_gift(42, saved_id=3, gift_id=1, title="Hidden", from_tg_id=123, name_hidden=True)
    graph = build_graph(store, target["id"])
    peers = [n for n in graph["nodes"] if n["type"] == "peer"]
    assert [n["tg_id"] for n in peers] == [99]
    assert peers[0]["meta"]["gifts_received"] == 1
    assert peers[0]["meta"]["gifts_sent"] == 1
    edges = {e["kind"]: e for e in graph["edges"]}
    assert edges["gift_in"]["source"] == peers[0]["id"]
    assert edges["gift_in"]["target"] == "target"
    assert edges["gift_out"]["source"] == "target"
    assert edges["gift_out"]["target"] == peers[0]["id"]
    store.close()
