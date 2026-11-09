/* Healdragg · force-directed graph. Без зависимостей и CDN. */
(function () {
  "use strict";

  const COLORS = {
    target: "#ff4d6d",
    chat: "#22d3ee",
    private: "#7c5cff",
    peer: "#6ee7a8",
  };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function shortNumber(value) {
    const n = Number(value || 0);
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(".0", "") + "M";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(".0", "") + "k";
    return String(n);
  }

  function formatDate(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleDateString("ru-RU", {
        day: "2-digit", month: "short", year: "2-digit",
      });
    } catch (err) {
      return "—";
    }
  }

  function nodeColor(node) {
    if (node.type === "target") return COLORS.target;
    if (node.type === "peer") return COLORS.peer;
    return node.meta && node.meta.private ? COLORS.private : COLORS.chat;
  }

  function nodeRadius(node) {
    const weight = Math.max(1, Number(node.weight || 1));
    if (node.type === "target") return 16;
    return 5 + Math.min(11, Math.sqrt(weight) * 1.6);
  }

  class HealdraggGraph {
    constructor(canvas, data) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.data = data || { nodes: [], edges: [] };
      this.nodes = [];
      this.links = [];
      this.byId = {};
      this.camera = { x: 0, y: 0, zoom: 1 };
      this.dragNode = null;
      this.panning = null;
      this.selected = null;
      this.hover = null;
      this.running = true;
      this.dpr = Math.min(2, window.devicePixelRatio || 1);
    }

    mount() {
      this.build();
      this.resize();
      this.bind();
      this.panel = document.querySelector("#panel");
      if (this.panel) {
        const close = this.panel.querySelector(".close");
        if (close) close.addEventListener("click", () => this.select(null));
      }
      const stats = document.getElementById("stats");
      if (stats && this.data.stats) {
        stats.textContent =
          this.data.stats.chats + " чатов · " + this.data.stats.messages + " сообщений · " +
          this.data.stats.peers + " собеседников";
      }
      const counters = document.getElementById("counters");
      if (counters) {
        counters.textContent = "узлов " + this.nodes.length + " · связей " + this.links.length +
          " · сгенерировано " + formatDate(this.data.generated_at);
      }
      requestAnimationFrame(() => this.loop());
    }

    build() {
      const width = this.canvas.clientWidth || window.innerWidth;
      const height = this.canvas.clientHeight || window.innerHeight;
      const center = { x: width / 2, y: height / 2 };
      const nodes = this.data.nodes || [];
      this.nodes = nodes.map((node, index) => {
        const angle = (index / Math.max(1, nodes.length)) * Math.PI * 2;
        const radius = node.type === "target" ? 0 : 120 + Math.random() * 180;
        return {
          ...node,
          x: center.x + Math.cos(angle) * radius,
          y: center.y + Math.sin(angle) * radius,
          vx: 0,
          vy: 0,
          r: nodeRadius(node),
          color: nodeColor(node),
          degree: 0,
        };
      });
      this.byId = {};
      this.nodes.forEach((node) => { this.byId[node.id] = node; });
      this.links = (this.data.edges || []).map((edge) => ({
        ...edge,
        s: this.byId[edge.source],
        t: this.byId[edge.target],
      })).filter((edge) => edge.s && edge.t);
      this.links.forEach((edge) => {
        edge.s.degree += edge.weight || 1;
        edge.t.degree += edge.weight || 1;
      });
      const target = this.byId["target"];
      if (target) { target.x = center.x; target.y = center.y; }
    }

    resize() {
      const width = this.canvas.clientWidth || window.innerWidth;
      const height = this.canvas.clientHeight || window.innerHeight;
      this.canvas.width = Math.floor(width * this.dpr);
      this.canvas.height = Math.floor(height * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.width = width;
      this.height = height;
    }

    bind() {
      const canvas = this.canvas;
      window.addEventListener("resize", () => this.resize());
      canvas.addEventListener("pointerdown", (event) => this.onDown(event));
      window.addEventListener("pointermove", (event) => this.onMove(event));
      window.addEventListener("pointerup", () => this.onUp());
      canvas.addEventListener("wheel", (event) => this.onWheel(event), { passive: false });
      canvas.addEventListener("dblclick", () => this.select(null));
    }

    toWorld(event) {
      const rect = this.canvas.getBoundingClientRect();
      const sx = event.clientX - rect.left;
      const sy = event.clientY - rect.top;
      return {
        x: (sx - this.camera.x) / this.camera.zoom,
        y: (sy - this.camera.y) / this.camera.zoom,
      };
    }

    pick(event) {
      const point = this.toWorld(event);
      let best = null;
      let bestDist = Infinity;
      this.nodes.forEach((node) => {
        const dx = node.x - point.x;
        const dy = node.y - point.y;
        const dist = Math.hypot(dx, dy);
        if (dist < node.r + 6 && dist < bestDist) { best = node; bestDist = dist; }
      });
      return best;
    }

    onDown(event) {
      const node = this.pick(event);
      if (node) {
        this.dragNode = node;
        const point = this.toWorld(event);
        this.dragOffset = { x: node.x - point.x, y: node.y - point.y };
        this.select(node);
      } else {
        this.panning = { x: event.clientX, y: event.clientY, cx: this.camera.x, cy: this.camera.y };
      }
    }

    onMove(event) {
      if (this.dragNode) {
        const point = this.toWorld(event);
        this.dragNode.x = point.x + this.dragOffset.x;
        this.dragNode.y = point.y + this.dragOffset.y;
        this.dragNode.vx = 0;
        this.dragNode.vy = 0;
        return;
      }
      if (this.panning) {
        this.camera.x = this.panning.cx + (event.clientX - this.panning.x);
        this.camera.y = this.panning.cy + (event.clientY - this.panning.y);
        return;
      }
      this.hover = this.pick(event);
      this.canvas.style.cursor = this.hover ? "pointer" : "grab";
    }

    onUp() {
      this.dragNode = null;
      this.panning = null;
    }

    onWheel(event) {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const sx = event.clientX - rect.left;
      const sy = event.clientY - rect.top;
      const factor = event.deltaY < 0 ? 1.12 : 0.89;
      const zoom = Math.min(3.5, Math.max(0.25, this.camera.zoom * factor));
      const scale = zoom / this.camera.zoom;
      this.camera.x = sx - (sx - this.camera.x) * scale;
      this.camera.y = sy - (sy - this.camera.y) * scale;
      this.camera.zoom = zoom;
    }

    select(node) {
      this.selected = node;
      const panel = this.panel || document.querySelector("#panel");
      if (!panel) return;
      if (!node) { panel.classList.remove("open"); return; }
      panel.classList.add("open");
      const title = panel.querySelector("h2");
      if (title) title.textContent = node.label;
      const body = panel.querySelector(".body");
      if (!body) return;
      const meta = node.meta || {};
      const tags = [];
      if (node.type === "target") tags.push('<span class="tag">цель</span>');
      if (node.type === "chat") {
        tags.push('<span class="tag">' + esc(meta.kind || "чат") + "</span>");
        tags.push('<span class="tag">' + (meta.private ? "🔒 приватный" : "публичный") + "</span>");
      }
      if (node.type === "peer") tags.push('<span class="tag">собеседник</span>');
      if (meta.status === "left") tags.push('<span class="tag">✖️ вышел</span>');
      if (meta.status === "admin" || meta.status === "creator") tags.push('<span class="tag">👮🏻‍♂️ админ</span>');
      const rows = [];
      rows.push(["ID", esc(node.tg_id)]);
      if (meta.username) rows.push(["username", "@" + esc(meta.username)]);
      if (meta.members) rows.push(["участников", shortNumber(meta.members)]);
      if (meta.messages) rows.push(["сообщений в чате", shortNumber(meta.messages)]);
      if (meta.my_messages) rows.push(["сообщений цели", shortNumber(meta.my_messages)]);
      if (meta.replies) rows.push(["ответов", shortNumber(meta.replies)]);
      if (meta.mentions) rows.push(["упоминаний", shortNumber(meta.mentions)]);
      if (meta.weight) rows.push(["сила связи", shortNumber(meta.weight)]);
      if (meta.chats) rows.push(["чатов", shortNumber(meta.chats)]);
      if (meta.peers) rows.push(["собеседников", shortNumber(meta.peers)]);
      if (meta.last) rows.push(["активность", formatDate(meta.last)]);
      if (meta.link) {
        rows.push(["ссылка", '<a href="' + esc(meta.link) + '" target="_blank" rel="noopener">открыть ↗</a>']);
      }
      body.innerHTML = tags.join("") +
        '<dl>' + rows.map((row) => "<dt>" + row[0] + "</dt><dd>" + row[1] + "</dd>").join("") + "</dl>";
    }

    physics() {
      const nodes = this.nodes;
      const cx = this.width / 2;
      const cy = this.height / 2;
      for (let i = 0; i < nodes.length; i += 1) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j += 1) {
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist2 = dx * dx + dy * dy;
          if (dist2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; dist2 = 1; }
          const min = (a.r + b.r + 18);
          if (dist2 < min * min) {
            const dist = Math.sqrt(dist2);
            const push = (min - dist) / dist * 0.06;
            a.x += dx * push; a.y += dy * push;
            b.x -= dx * push; b.y -= dy * push;
          } else if (dist2 < 90000) {
            const force = 140 / dist2;
            a.vx += dx * force; a.vy += dy * force;
            b.vx -= dx * force; b.vy -= dy * force;
          }
        }
        const target = this.byId["target"];
        if (a !== target && target) {
          const dx = a.x - target.x;
          const dy = a.y - target.y;
          const dist = Math.max(30, Math.hypot(dx, dy));
          const pull = (dist - 210) * 0.0009;
          a.vx -= dx * pull; a.vy -= dy * pull;
        }
      }
      this.links.forEach((link) => {
        const dx = link.t.x - link.s.x;
        const dy = link.t.y - link.s.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const ideal = 150 + 40 / Math.max(1, link.weight);
        const force = (dist - ideal) * 0.0016;
        const fx = dx * force;
        const fy = dy * force;
        if (link.s !== this.byId["target"]) { link.s.vx += fx; link.s.vy += fy; }
        if (link.t !== this.byId["target"]) { link.t.vx -= fx; link.t.vy -= fy; }
      });
      nodes.forEach((node) => {
        if (node === this.dragNode) return;
        if (node === this.byId["target"]) { node.vx = 0; node.vy = 0; return; }
        node.vx += (cx - node.x) * 0.0004;
        node.vy += (cy - node.y) * 0.0004;
        node.vx *= 0.86;
        node.vy *= 0.86;
        node.x += node.vx;
        node.y += node.vy;
      });
    }

    draw() {
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.translate(this.camera.x, this.camera.y);
      ctx.scale(this.camera.zoom, this.camera.zoom);

      const active = this.selected || this.hover;
      const near = new Set();
      if (active) {
        near.add(active.id);
        this.links.forEach((link) => {
          if (link.s === active) near.add(link.t.id);
          if (link.t === active) near.add(link.s.id);
        });
      }

      this.links.forEach((link) => {
        const dim = active && !(near.has(link.s.id) && near.has(link.t.id));
        const width = Math.min(3.2, 0.6 + Math.sqrt(link.weight || 1) * 0.45);
        ctx.strokeStyle = link.kind === "peer"
          ? "rgba(110,231,168," + (dim ? 0.06 : 0.3) + ")"
          : "rgba(34,211,238," + (dim ? 0.05 : 0.22) + ")";
        ctx.lineWidth = width;
        ctx.beginPath();
        ctx.moveTo(link.s.x, link.s.y);
        ctx.lineTo(link.t.x, link.t.y);
        ctx.stroke();
      });

      this.nodes.forEach((node) => {
        const dim = active && !near.has(node.id);
        ctx.globalAlpha = dim ? 0.28 : 1;
        const glow = node === this.selected ? 22 : 12;
        ctx.shadowColor = node.color;
        ctx.shadowBlur = glow;
        ctx.fillStyle = node.color;
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        if (node === this.selected || node === this.byId["target"] || node.r > 9 || this.camera.zoom > 1.35) {
          ctx.fillStyle = "rgba(232,240,255,.92)";
          ctx.font = (node.type === "target" ? "600 13px" : "11px") + " ui-monospace, monospace";
          const label = node.label.length > 26 ? node.label.slice(0, 25) + "…" : node.label;
          const width = ctx.measureText(label).width;
          ctx.fillText(label, node.x - width / 2, node.y + node.r + 13);
        }
        ctx.globalAlpha = 1;
      });
      ctx.restore();
    }

    loop() {
      if (!this.running) return;
      this.physics();
      this.draw();
      requestAnimationFrame(() => this.loop());
    }

    destroy() { this.running = false; }
  }

  window.HealdraggGraph = HealdraggGraph;
})();
