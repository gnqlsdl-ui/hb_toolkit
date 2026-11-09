"""Mesh edge-path helpers shared by features that place bones on a mesh (DRY).

Everything works on the object's *base* mesh (``obj.data``) in world space,
so results do not depend on the current pose or on modifiers.
"""

import heapq

import bmesh
from mathutils import Vector, kdtree


class MeshGraph:
    """Vertex positions (world), edge adjacency and boundary flags."""

    def __init__(self, obj):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        mw = obj.matrix_world
        self.co = [mw @ v.co for v in bm.verts]
        self.neighbors = [
            [(e.other_vert(v).index, e.is_boundary) for e in v.link_edges]
            for v in bm.verts
        ]
        self.boundary = [v.is_boundary for v in bm.verts]
        bm.free()
        self._kd = kdtree.KDTree(len(self.co))
        for i, p in enumerate(self.co):
            self._kd.insert(p, i)
        self._kd.balance()

    def nearest(self, point):
        """Index of the vertex closest to ``point`` (world space)."""
        return self._kd.find(point)[1]

    def shortest_path(self, a, b, boundary_only=False):
        """Vertex indices from ``a`` to ``b`` along edges (Dijkstra)."""
        co, nb = self.co, self.neighbors
        dist = {a: 0.0}
        prev = {}
        heap = [(0.0, a)]
        while heap:
            d, u = heapq.heappop(heap)
            if u == b:
                break
            if d > dist[u]:
                continue
            for w, is_bnd in nb[u]:
                if boundary_only and not is_bnd:
                    continue
                nd = d + (co[u] - co[w]).length
                if nd < dist.get(w, float("inf")):
                    dist[w] = nd
                    prev[w] = u
                    heapq.heappush(heap, (nd, w))
        if b not in dist:
            raise ValueError(f"No edge path between vertex {a} and {b}")
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        return path[::-1]

    def trace(self, a, mid, b):
        """Path ``a -> mid -> b``.

        When all three vertices lie on a mesh boundary (an eye hole, for
        example) only boundary edges are used, so the path follows the rim.
        Returns ``(path, mid_index, on_boundary)``.
        """
        on_bnd = self.boundary[a] and self.boundary[mid] and self.boundary[b]
        first = self.shortest_path(a, mid, on_bnd)
        second = self.shortest_path(mid, b, on_bnd)
        return first + second[1:], len(first) - 1, on_bnd

    def boundary_loops(self):
        """List of vertex-index lists, one per connected boundary rim."""
        adj = {}
        for u, links in enumerate(self.neighbors):
            for w, is_bnd in links:
                if is_bnd:
                    adj.setdefault(u, []).append(w)
        seen = set()
        loops = []
        for start in adj:
            if start in seen:
                continue
            comp, stack = [], [start]
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x)
                comp.append(x)
                stack.extend(adj[x])
            loops.append(comp)
        return loops


def edge_loop(obj, edge_indices):
    """Order the vertices of a registered edge selection.

    Returns ``(vertex_indices, closed)``. Raises ``ValueError`` when the
    edges branch (a vertex with more than two selected edges) or split into
    several pieces.
    """
    edges = obj.data.edges
    adj = {}
    for ei in edge_indices:
        if ei >= len(edges):
            raise ValueError("Stored edges no longer match the mesh")
        a, b = edges[ei].vertices
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    if not adj:
        raise ValueError("No edges registered")
    if any(len(n) > 2 for n in adj.values()):
        raise ValueError("Edges branch: register a single loop or line")
    ends = [v for v, n in adj.items() if len(n) == 1]
    start = ends[0] if ends else next(iter(adj))
    order, prev, cur = [start], None, start
    while True:
        nxt = [w for w in adj[cur] if w != prev]
        if not nxt or nxt[0] == start:
            break
        prev, cur = cur, nxt[0]
        order.append(cur)
    if len(order) != len(adj):
        raise ValueError("Edges are not one connected loop or line")
    return order, not ends


def split_loop(loop, a, b):
    """Split a closed vertex loop into the two arcs running from ``a`` to ``b``."""
    i, j = loop.index(a), loop.index(b)
    n = len(loop)
    fwd = [loop[(i + k) % n] for k in range((j - i) % n + 1)]
    back = [loop[(i - k) % n] for k in range((i - j) % n + 1)]
    return fwd, back


def resample(points, count):
    """``count`` points evenly spaced by arc length along a polyline.

    Both end points are included (``count >= 2``).
    """
    if count < 2 or len(points) < 2:
        return [p.copy() for p in points[:count]]
    seg = [(points[i + 1] - points[i]).length for i in range(len(points) - 1)]
    total = sum(seg)
    if total <= 0.0:
        return [points[0].copy() for _ in range(count)]
    out = []
    i, acc = 0, 0.0
    for k in range(count):
        target = total * k / (count - 1)
        while i < len(seg) - 1 and acc + seg[i] < target:
            acc += seg[i]
            i += 1
        t = 0.0 if seg[i] == 0.0 else min(max((target - acc) / seg[i], 0.0), 1.0)
        out.append(points[i].lerp(points[i + 1], t))
    return out


def centroid(points):
    return sum(points, Vector()) / max(len(points), 1)
