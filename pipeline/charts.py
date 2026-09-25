"""Geometry for /analytics' drawn charts: the flow and the curves.

Pure, like `trace.py`: numbers in, coordinates out, no database and no
template knowledge. The template draws exactly what this returns.

Both charts draw their MARKS in an SVG stretched to the container
(`preserveAspectRatio="none"`) and their WORDS in HTML laid over it at the
same percentages. Text inside a stretched SVG would stretch with it, and text
inside a fixed-aspect one shrinks to nothing on a phone; marks have no aspect
to protect, and a line keeps its width through `vector-effect:
non-scaling-stroke`.
"""

from __future__ import annotations

import math
from collections import defaultdict

# ------------------------------------------------------------------- curves


def _clean_top(v: float) -> float:
    """The y axis' top: the next tenth above the data, never below 10%."""
    return max(0.1, math.ceil(v * 10 - 1e-9) / 10)


def curve(points, x_max: float, *, step: bool = True, marks=()) -> dict:
    """A step line (and its 10% wash) through `points` [(t, share)] in a
    0..100 box with y pointing down, on an x axis 0..x_max days. The last
    value carries on to x_max: a Kaplan-Meier curve is flat after its last
    event, not undefined. `marks` are named x positions [(name, t)] for the
    template to rule and label; a None t is dropped."""
    pts = [(t, v) for t, v in points if t <= x_max]
    if not pts:
        pts = [(0.0, 0.0)]
    top = _clean_top(max(v for _, v in pts))

    def X(t):
        return 100.0 * max(0.0, min(t, x_max)) / x_max

    def Y(v):
        return 100.0 - 100.0 * v / top
    t0, v0 = pts[0]
    d = [f"M{X(t0):.2f},{Y(v0):.2f}"]
    for t, v in pts[1:]:
        if step:
            d.append(f"H{X(t):.2f}V{Y(v):.2f}")
        else:
            d.append(f"L{X(t):.2f},{Y(v):.2f}")
    d.append("H100")
    line = "".join(d)
    step_x = 7 if x_max <= 70 else 14
    return {
        "line": line,
        "area": f"{line}V100H{X(t0):.2f}Z",
        "top": round(top * 100),
        "xticks": [{"x": f"{X(t):.2f}%", "label": str(t)}
                   for t in range(0, int(x_max) + 1, step_x)],
        "marks": [{"name": n, "t": t, "x": f"{X(t):.2f}%"} for n, t in marks if t is not None],
    }


# --------------------------------------------------------------------- flow

FLOW_W = 1000                   # viewBox width; x positions below are in it
FLOW_H = 340                    # px — the SVG's own height, so y units are px
FLOW_COLS = (150, 520, 800)     # each column's node left edge
FLOW_NODE = 10                  # node width
FLOW_GAP = 12                   # px between stacked nodes
LABEL_GAP = 19                  # px between label centres: one line of type


def _spread(wanted: list[float], gap: float, lo: float, hi: float) -> list[float]:
    """Label centres as close to `wanted` as they can be while `gap` apart and
    inside lo..hi — pushed down past their neighbours, then back up off the
    bottom edge. `wanted` must already be in order."""
    out = []
    for w in wanted:
        out.append(max(w, lo, out[-1] + gap if out else lo))
    for i in range(len(out) - 1, -1, -1):
        ceiling = hi if i == len(out) - 1 else out[i + 1] - gap
        out[i] = min(out[i], ceiling)
    return out


def flow(nodes: list[dict], links: list[dict], height: int = FLOW_H) -> dict | None:
    """Lay out a three-column flow. Nodes are {id, col, value, ...} in the
    order they should stack; a column-2 node names its `parent` and sits
    inside it, so its band runs straight. Columns 0 and 1 share one scale, so
    a band's thickness means the same number everywhere. Links are
    {src, dst, value, ...}; every extra key on a node or link is passed
    through for the template."""
    if not nodes:
        return None
    nodes = [dict(n) for n in nodes]
    links = [dict(l) for l in links]
    by_id = {n["id"]: n for n in nodes}
    cols = defaultdict(list)
    for n in nodes:
        cols[n["col"]].append(n)

    k = min((height - FLOW_GAP * (len(cols[c]) - 1)) / sum(n["value"] for n in cols[c])
            for c in (0, 1) if cols[c])

    y = 0.0
    for n in cols[1]:
        n["y"], n["h"] = y, k * n["value"]
        y += n["h"] + FLOW_GAP
    bottom = y - FLOW_GAP if cols[1] else height
    # Column 0 spreads to column 1's full height, so the bands fan out
    # rather than all bending one way.
    total0 = sum(k * n["value"] for n in cols[0])
    m0 = len(cols[0])
    gap0 = (bottom - total0) / (m0 - 1) if m0 > 1 else 0.0
    y = 0.0 if m0 > 1 else (bottom - total0) / 2
    for n in cols[0]:
        n["y"], n["h"] = y, k * n["value"]
        y += n["h"] + gap0
    filled = defaultdict(float)
    for n in cols[2]:
        p = by_id[n["parent"]]
        n["y"], n["h"] = p["y"] + filled[p["id"]], k * n["value"]
        filled[p["id"]] += n["h"]

    for n in nodes:
        n["x"] = FLOW_COLS[n["col"]]
        n["mid"] = n["y"] + n["h"] / 2

    # Bands leave a node in the order their targets stack and arrive in the
    # order their sources stack, so no two bands cross at either end.
    out_of, into = defaultdict(list), defaultdict(list)
    for l in links:
        l["h"] = k * l["value"]
        out_of[l["src"]].append(l)
        into[l["dst"]].append(l)
    for src, ls in out_of.items():
        y = by_id[src]["y"]
        for l in sorted(ls, key=lambda l: by_id[l["dst"]]["y"]):
            l["y0"], y = y, y + l["h"]
    for dst, ls in into.items():
        y = by_id[dst]["y"]
        for l in sorted(ls, key=lambda l: by_id[l["src"]]["y"]):
            l["y1"], y = y, y + l["h"]
    for l in links:
        x0 = by_id[l["src"]]["x"] + FLOW_NODE
        x1 = by_id[l["dst"]]["x"]
        xm = (x0 + x1) / 2
        y0, y1, h = l["y0"], l["y1"], l["h"]
        l["d"] = (f"M{x0},{y0:.2f}C{xm},{y0:.2f} {xm},{y1:.2f} {x1},{y1:.2f}"
                  f"L{x1},{y1 + h:.2f}C{xm},{y1 + h:.2f} {xm},{y0 + h:.2f} {x0},{y0 + h:.2f}Z")

    for c, ns in cols.items():
        ns_sorted = sorted(ns, key=lambda n: n["mid"])
        for n, ly in zip(ns_sorted, _spread([n["mid"] for n in ns_sorted],
                                            LABEL_GAP, 8, height - 8)):
            n["label_y"] = round(ly, 1)
    for n in nodes:
        # The rect as drawn: a child is inset 1px top and bottom, so siblings
        # stacked with no gap still read as separate (a surface gap, not a
        # stroke), and a sliver stays visible.
        inset = 1.0 if n["col"] == 2 and n["h"] > 3 else 0.0
        n["ry"], n["rh"] = n["y"] + inset, max(n["h"] - 2 * inset, 1.0)
    return {"w": FLOW_W, "h": height, "node_w": FLOW_NODE,
            "nodes": nodes, "links": links}
