"""Draws a stored `Diagram` as inline SVG, on the server. Pure Python: no database, no JavaScript, no library.

Three layouts, one per shape: `parts` (one coordinating box above a row of boxes), `steps` (boxes top to
bottom with arrows) and `loop` (chips with arrows and a return arrow). All sizes are user units in a 320 wide
`viewBox`, so the picture scales to the card. Colours come only from CSS classes in `app.css`, so light and dark
themes follow the page. Every text goes through `escape`, and no layout can fail on any 2 to 8 node graph.
"""

from markupsafe import Markup, escape

from research_pipeline.summary_models import Diagram, DiagramNode

WIDTH = 320
PAD = 8
GAP = 8
BOX_H = 40
CHAR_W = 6.6  # average width of one character at the 12 unit font, used to wrap labels
ARROW = 5


def _wrap(label: str, width: float) -> list[str]:
    """Split a label into at most two lines by word; the second line ends in an ellipsis if it still overflows."""
    per_line = max(4, int((width - 10) / CHAR_W))
    lines: list[str] = []
    current = ""
    for word in label.split():
        trial = f"{current} {word}".strip()
        if len(trial) <= per_line or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return [ln if len(ln) <= per_line else ln[: per_line - 1] + "…" for ln in lines]


def _box(node: DiagramNode, x: float, y: float, w: float, h: float, css: str) -> str:
    lines = _wrap(node.label, w)
    cx = x + w / 2
    first = y + h / 2 - (len(lines) - 1) * 7 + 4.5
    spans = "".join(f'<tspan x="{cx:.1f}" dy="{0 if i == 0 else 14}">{escape(ln)}</tspan>' for i, ln in enumerate(lines))
    return (
        f'<g class="d-node {css}"><title>{escape(node.label)}</title><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="10"/>'
        f'<text x="{cx:.1f}" y="{first:.1f}" text-anchor="middle">{spans}</text></g>'
    )


def _line(x1: float, y1: float, x2: float, y2: float) -> str:
    return f'<line class="d-line" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'


def _head_down(x: float, y: float) -> str:
    return f'<polygon class="d-head" points="{x - ARROW:.1f},{y - ARROW * 1.6:.1f} {x + ARROW:.1f},{y - ARROW * 1.6:.1f} {x:.1f},{y:.1f}"/>'


def _head_left(x: float, y: float) -> str:
    return f'<polygon class="d-head" points="{x + ARROW * 1.6:.1f},{y - ARROW:.1f} {x + ARROW * 1.6:.1f},{y + ARROW:.1f} {x:.1f},{y:.1f}"/>'


def _parts(d: Diagram) -> tuple[list[str], float]:
    outgoing = {n.id: 0 for n in d.nodes}
    for e in d.edges:
        if e.source in outgoing:
            outgoing[e.source] += 1
    top = max(d.nodes, key=lambda n: outgoing[n.id])  # max keeps the first on ties
    rest = [n for n in d.nodes if n is not top]
    per_row = len(rest) if len(rest) <= 4 else -(-len(rest) // 2)
    rows = [rest[i : i + per_row] for i in range(0, len(rest), per_row)]
    lines: list[str] = []  # every connector is emitted before any box, so no line paints over a label
    boxes: list[str] = []
    top_w = 150.0
    top_x = (WIDTH - top_w) / 2
    y = PAD
    boxes.append(_box(top, top_x, y, top_w, BOX_H, "d-top"))
    y += BOX_H + 26
    for row in rows:
        w = (WIDTH - 2 * PAD - (len(row) - 1) * GAP) / len(row)
        bar_y = y - 13
        centres = [PAD + i * (w + GAP) + w / 2 for i in range(len(row))]
        # Connectors: a stem down from the coordinating box, a bar across, and a drop to each box.
        lines.append(_line(WIDTH / 2, PAD + BOX_H, WIDTH / 2, bar_y))
        lines.append(_line(min(centres + [WIDTH / 2]), bar_y, max(centres + [WIDTH / 2]), bar_y))
        for n, cx in zip(row, centres, strict=True):
            lines.append(_line(cx, bar_y, cx, y))
            boxes.append(_box(n, cx - w / 2, y, w, BOX_H, ""))
        y += BOX_H + 26
    return lines + boxes, y - 26 + PAD


def _step_order(d: Diagram) -> list[DiagramNode]:
    by_id = {n.id: n for n in d.nodes}
    targets = {e.target for e in d.edges}
    starts = [n for n in d.nodes if n.id not in targets] or d.nodes[:1]
    seen: set[str] = set()
    order: list[DiagramNode] = []

    def visit(n: DiagramNode) -> None:
        stack = [n]
        while stack:  # iterative walk: a cycle cannot loop, a deep chain cannot overflow
            cur = stack.pop()
            if cur.id in seen:
                continue
            seen.add(cur.id)
            order.append(cur)
            nxt = [by_id[e.target] for e in d.edges if e.source == cur.id and e.target in by_id]
            stack.extend(reversed(nxt))

    for n in starts:
        visit(n)
    order += [n for n in d.nodes if n.id not in seen]  # nodes no edge reaches go last
    return order


def _steps(d: Diagram) -> tuple[list[str], float]:
    out: list[str] = []
    w, x, y = WIDTH - 2 * PAD - 40, PAD + 20, PAD
    for i, n in enumerate(_step_order(d)):
        if i:
            out.append(_line(WIDTH / 2, y - 22, WIDTH / 2, y - 1))
            out.append(_head_down(WIDTH / 2, y))
        out.append(_box(n, x, y, w, BOX_H, "d-top" if i == 0 else ""))
        y += BOX_H + 22
    return out, y - 22 + PAD


def _loop(d: Diagram) -> tuple[list[str], float]:
    out: list[str] = []
    w, x, y = 220.0, PAD + 20, float(PAD)
    mids: list[float] = []
    for i, n in enumerate(d.nodes):
        if i:
            out.append(_line(x + w / 2, y - 18, x + w / 2, y - 1))
            out.append(_head_down(x + w / 2, y))
        out.append(_box(n, x, y, w, BOX_H, "d-chip"))
        mids.append(y + BOX_H / 2)
        y += BOX_H + 18
    right, edge = x + w, WIDTH - PAD - 16
    out.append(
        f'<polyline class="d-line d-return" points="{right:.1f},{mids[-1]:.1f} {edge:.1f},{mids[-1]:.1f} '
        f'{edge:.1f},{mids[0]:.1f} {right + ARROW * 1.6:.1f},{mids[0]:.1f}"/>'
    )
    out.append(_head_left(right, mids[0]))
    return out, y - 18 + PAD


_LAYOUTS = {"parts": _parts, "steps": _steps, "loop": _loop}


def render(d: Diagram) -> Markup:
    """The diagram as one `<figure class="diagram">`: inline SVG plus the caption. All text is escaped."""
    parts, height = _LAYOUTS[d.shape](d)
    caption = escape(d.caption)
    svg = (
        f'<svg class="diagram-svg" viewBox="0 0 {WIDTH} {height:.0f}" role="img" aria-label="{caption}" '
        f'xmlns="http://www.w3.org/2000/svg" font-size="12">{"".join(parts)}</svg>'
    )
    return Markup(f'<figure class="diagram diagram-{d.shape}">{svg}<figcaption>{caption}</figcaption></figure>')
