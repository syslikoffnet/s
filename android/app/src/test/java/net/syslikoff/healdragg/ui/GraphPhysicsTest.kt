package net.syslikoff.healdragg.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GraphPhysicsTest {
    @Test fun deterministicAndBounded() {
        val ids = listOf("target", "a", "b")
        val links = listOf(GraphEdge("target", "a", 3, "peer"), GraphEdge("a", "b", 2, "gift"))
        val first = GraphPhysics(ids, links)
        val second = GraphPhysics(ids, links)
        repeat(360) { first.step(); second.step() }
        assertEquals(first.positions(), second.positions())
        assertEquals(0f to 0f, first.positions()["target"])
        assertTrue(first.positions().values.all { (x, y) -> x.isFinite() && y.isFinite() && kotlin.math.abs(x) <= 1000 && kotlin.math.abs(y) <= 1000 })
        assertTrue(!first.step())
    }

    @Test fun movingNodeRestartsSimulationAndKeepsAnchorFixed() {
        val graph = GraphPhysics(listOf("target", "a"), listOf(GraphEdge("target", "a", 1, "chat")))
        repeat(361) { graph.step() }
        val before = graph.positions().getValue("a")
        graph.move("a", 20.0, -10.0)
        assertEquals(before.first + 20f, graph.positions().getValue("a").first, 0.001f)
        graph.move("target", 50.0, 50.0)
        assertEquals(0f to 0f, graph.positions()["target"])
        assertTrue(graph.step())
    }
}
