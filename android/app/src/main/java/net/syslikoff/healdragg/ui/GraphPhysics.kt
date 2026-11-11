package net.syslikoff.healdragg.ui

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** A bounded deterministic force simulation. Coordinates are logical, not pixels. */
internal class GraphPhysics(ids: List<String>, links: List<GraphEdge>, private val anchor: String = "target") {
    private data class Body(var x: Double, var y: Double, var vx: Double = 0.0, var vy: Double = 0.0)
    private val bodies = linkedMapOf<String, Body>()
    private val edges = links.filter { it.source != it.target && it.source in ids && it.target in ids }
    private var ticks = 0

    init {
        val others = ids.filter { it != anchor }
        if (anchor in ids) bodies[anchor] = Body(0.0, 0.0)
        others.forEachIndexed { index, id ->
            val angle = index * 2.399963229728653
            val r = 50.0 + 17.0 * sqrt((index + 1).toDouble())
            bodies[id] = Body(cos(angle) * r, sin(angle) * r)
        }
    }

    fun move(id: String, dx: Double, dy: Double) {
        if (id == anchor) return
        bodies[id]?.let { it.x += dx; it.y += dy; it.vx = 0.0; it.vy = 0.0 }
        ticks = 0
    }

    fun positions(): Map<String, Pair<Float, Float>> = bodies.mapValues { (_, b) -> b.x.toFloat() to b.y.toFloat() }

    /** Returns whether additional frames are useful. */
    fun step(): Boolean {
        if (ticks++ >= 360) return false
        val list = bodies.values.toList()
        val fx = DoubleArray(list.size)
        val fy = DoubleArray(list.size)
        val index = bodies.keys.withIndex().associate { it.value to it.index }
        for (i in list.indices) for (j in i + 1 until list.size) {
            var dx = list[i].x - list[j].x
            var dy = list[i].y - list[j].y
            var d2 = dx * dx + dy * dy
            if (d2 < 0.01) { dx = cos((i + 1) * PI / 7); dy = sin((j + 1) * PI / 7); d2 = dx * dx + dy * dy }
            val d = sqrt(d2)
            // Softened repulsion prevents violent jumps when nodes overlap.
            val force = 1400.0 / (d2 + 80.0) + if (d < 30.0) (30.0 - d) * 0.20 else 0.0
            val x = dx / d * force; val y = dy / d * force
            fx[i] += x; fy[i] += y; fx[j] -= x; fy[j] -= y
        }
        edges.forEach { edge ->
            val a = index[edge.source] ?: return@forEach
            val b = index[edge.target] ?: return@forEach
            val dx = list[b].x - list[a].x; val dy = list[b].y - list[a].y
            val distance = sqrt(dx * dx + dy * dy).coerceAtLeast(0.01)
            val strength = 0.008 + kotlin.math.ln(1.0 + edge.weight.coerceAtLeast(1)) * 0.002
            val rest = if (edge.kind == "context") 85.0 else 120.0
            val force = (distance - rest) * strength
            val x = dx / distance * force; val y = dy / distance * force
            fx[a] += x; fy[a] += y; fx[b] -= x; fy[b] -= y
        }
        bodies.entries.forEachIndexed { i, (id, b) ->
            if (id == anchor) return@forEachIndexed
            b.vx = ((b.vx + fx[i] - b.x * 0.002) * 0.82).coerceIn(-12.0, 12.0)
            b.vy = ((b.vy + fy[i] - b.y * 0.002) * 0.82).coerceIn(-12.0, 12.0)
            b.x = (b.x + b.vx).coerceIn(-1000.0, 1000.0)
            b.y = (b.y + b.vy).coerceIn(-1000.0, 1000.0)
        }
        return true
    }
}
