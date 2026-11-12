"""Статичное SVG-превью паутины (для README), без браузера и JS."""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from healdragg.db import Store  # noqa: E402
from healdragg.demo import DEMO_TARGET, seed_demo  # noqa: E402
from healdragg.graph import build_graph  # noqa: E402

OUT = ROOT / "docs" / "network.svg"
W, H = 1200, 760
CX, CY = 600, 372


def esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def clip(text: str, limit: int = 22) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_svg(graph: dict) -> str:
    nodes = graph["nodes"]
    chats = [n for n in nodes if n["type"] == "chat"]
    peers = [n for n in nodes if n["type"] == "peer"]
    chats.sort(key=lambda n: -n["weight"])
    peers.sort(key=lambda n: -n["weight"])

    positions: dict[str, tuple[float, float]] = {"target": (CX, CY)}
    chat_ring = 236
    for index, node in enumerate(chats[:26]):
        angle = -math.pi / 2 + index * (2 * math.pi / max(1, min(len(chats), 26)))
        wobble = 0.82 + 0.18 * ((index * 37) % 11) / 10
        positions[node["id"]] = (
            CX + math.cos(angle) * chat_ring * wobble,
            CY + math.sin(angle) * chat_ring * 0.78 * wobble,
        )
    peer_ring = 336
    for index, node in enumerate(peers[:18]):
        angle = -math.pi / 2 + (index + 0.5) * (2 * math.pi / max(1, len(peers[:18])))
        positions[node["id"]] = (
            CX + math.cos(angle) * peer_ring,
            CY + math.sin(angle) * peer_ring * 0.86,
        )

    def color(node: dict) -> str:
        if node["type"] == "target":
            return "#ff2e63"
        if node["type"] == "peer":
            return "#fee440"
        return "#7b61ff" if node["meta"].get("private") else "#00f5d4"

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">',
        "<defs>",
        '<radialGradient id="bg" cx="50%" cy="42%" r="75%">'
        '<stop offset="0%" stop-color="#0d1526"/><stop offset="100%" stop-color="#05070c"/>'
        "</radialGradient>",
        '<filter id="glow" x="-60%" y="-60%" width="220%" height="220%">'
        '<feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/>'
        '<feMergeNode in="SourceGraphic"/></feMerge></filter>',
        '<filter id="soft"><feGaussianBlur stdDeviation="1.2"/></filter>',
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
    ]
    for x in range(40, W, 40):
        for y in range(40, H, 40):
            parts.append(f'<circle cx="{x}" cy="{y}" r="0.7" fill="#152238"/>')

    for edge in graph["edges"]:
        src = positions.get(edge["source"])
        dst = positions.get(edge["target"])
        if not src or not dst:
            continue
        stroke = "rgba(254,228,64,.30)" if edge["kind"] == "peer" else "rgba(0,245,212,.20)"
        width = min(2.6, 0.5 + math.sqrt(edge["weight"]) * 0.35)
        parts.append(
            f'<line x1="{src[0]:.1f}" y1="{src[1]:.1f}" x2="{dst[0]:.1f}" y2="{dst[1]:.1f}" '
            f'stroke="{stroke}" stroke-width="{width:.2f}"/>'
        )

    for node in nodes:
        point = positions.get(node["id"])
        if not point:
            continue
        radius = {"target": 19.0, "chat": 6.0, "peer": 7.0}[node["type"]]
        radius += min(6.0, math.sqrt(max(1, node["weight"])) * 0.9)
        fill = color(node)
        parts.append(
            f'<circle cx="{point[0]:.1f}" cy="{point[1]:.1f}" r="{radius:.1f}" fill="{fill}" '
            f'fill-opacity="0.92" filter="url(#glow)"/>'
        )
        if node["type"] == "target":
            parts.append(
                f'<text x="{point[0]:.1f}" y="{point[1] + radius + 24:.1f}" text-anchor="middle" '
                f'fill="#e8f0ff" font-size="15" font-weight="700">{esc(clip(node["label"], 30))}</text>'
            )
            parts.append(
                f'<text x="{point[0]:.1f}" y="{point[1] + radius + 42:.1f}" text-anchor="middle" '
                f'fill="#8fa6c8" font-size="11">цель · {node["meta"].get("messages", 0)} сообщений · '
                f'{node["meta"].get("peers", 0)} собеседников</text>'
            )
        else:
            label = clip(node["label"], 18)
            dy = -radius - 7 if node["type"] == "chat" else radius + 14
            parts.append(
                f'<text x="{point[0]:.1f}" y="{point[1] + dy:.1f}" text-anchor="middle" '
                f'fill="#c9d8ef" font-size="10" fill-opacity="0.9">{esc(label)}</text>'
            )

    # HUD
    parts.append(
        '<text x="28" y="46" fill="#00f5d4" font-size="16" font-weight="700" letter-spacing="1.6">'
        "HEALDRAGG · ПАУТИНА СВЯЗЕЙ</text>"
    )
    parts.append(
        f'<text x="28" y="68" fill="#8fa6c8" font-size="11">цель → чаты → собеседники · '
        f'{len(chats)} чатов · {len(peers)} человек · {graph["stats"]["messages"]} сообщений</text>'
    )
    legend = [("#ff2e63", "цель"), ("#00f5d4", "чат"), ("#7b61ff", "приватный"), ("#fee440", "собеседник")]
    for index, (dot, name) in enumerate(legend):
        x = 28 + index * 118
        parts.append(f'<circle cx="{x}" cy="{H - 34}" r="5" fill="{dot}"/>')
        parts.append(f'<text x="{x + 12}" y="{H - 30}" fill="#9db7d8" font-size="11">{name}</text>')
    parts.append(
        f'<text x="{W - 28}" y="{H - 30}" text-anchor="end" fill="#5c7392" font-size="10">'
        "healdragg graph --html</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="healdragg-net-"))
    store = Store(tmp / "demo.db")
    try:
        seed_demo(store, total=40)
        target = store.get_target(DEMO_TARGET)
        assert target
        graph = build_graph(store, int(target["id"]))
    finally:
        store.close()
    OUT.write_text(build_svg(graph), encoding="utf-8")
    print(f"✓ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
