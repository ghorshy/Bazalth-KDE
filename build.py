#!/usr/bin/env python3
"""Builds the Kvantum theme and the KDE color scheme from palette.toml.

Text templates (templates/*.kvconfig, templates/*.colors) get `{{ expression }}`
placeholders filled from the palette. The Kvantum SVG is the Graphite one with
every framed widget (buttons, fields, menus, tabs, ...) removed and redrawn here
from the palette; the remaining icons (arrows, MDI buttons, grips) are recolored.
"""

import math
import re
import shutil
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
NAMESPACES = {
    "": SVG,
    "xlink": XLINK,
    "inkscape": "http://www.inkscape.org/namespaces/inkscape",
    "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
}
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)


# ─── palette ─────────────────────────────────────────────────────────────────


def rgb_tuple(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def mix(fg: str, bg: str, amount: float) -> str:
    """fg drawn over bg at the given opacity, as an opaque #rrggbb."""
    f, b = rgb_tuple(fg), rgb_tuple(bg)
    return "#%02x%02x%02x" % tuple(round(x * amount + y * (1 - amount)) for x, y in zip(f, b))


def rgb(color: str) -> str:
    """#rrggbb as KDE's "r,g,b"."""
    return ",".join(map(str, rgb_tuple(color)))


def load_palette() -> SimpleNamespace:
    data = tomllib.loads((ROOT / "palette.toml").read_text())
    return SimpleNamespace(
        name=data["name"],
        c=SimpleNamespace(**{k: "#" + v.lower() for k, v in data["colors"].items()}),
        opacity=SimpleNamespace(**data["opacity"]),
        ui=SimpleNamespace(**data["ui"]),
    )


def sizes(p: SimpleNamespace) -> SimpleNamespace:
    """Sizes derived from the palette, shared by the SVG and the kvconfig (as `size`)."""
    return SimpleNamespace(
        frame=p.ui.radius,  # frames are as wide as the corner radius, so a corner fits its piece
        popup=max(p.ui.radius, p.ui.border + p.ui.padding // 2),  # menus, tooltips: border + padding
        groove=3,  # half the height of slider and progress grooves: 6px pills, like the OSD bar
        thumb=p.ui.padding - 2,  # half the width of a scrollbar thumb
        focus=2,  # Kvantum draws focus rectangles at most 2px wide
        indicator=16,  # check boxes, radio buttons and slider handles
    )


def render(template: str, p: SimpleNamespace) -> str:
    scope = {**vars(p.c), "opacity": p.opacity, "ui": p.ui, "size": sizes(p), "name": p.name,
             "mix": mix, "rgb": rgb}
    return re.sub(r"\{\{(.*?)\}\}", lambda m: str(eval(m.group(1), {"__builtins__": {}}, scope)), template)


# ─── frame geometry ──────────────────────────────────────────────────────────
#
# Kvantum draws a framed widget from nine SVG elements: <element>-<state> for the
# middle and <element>-<state>-<side> for the corners and edges. Each frame is drawn
# as concentric rounded rectangles (border, fill) sharing one corner centre, then cut
# into pieces, so pieces join seamlessly at any size.
#
# Popups have no shadow: Kvantum draws it inside the popup window and then has to
# guess the offset when placing menus, which goes wrong on Wayland.

EDGE = 8  # length of edge and middle pieces; Kvantum stretches them anyway
ARC_STEPS = 12

Paint = tuple[str, float]  # color, opacity
Point = tuple[float, float]


@dataclass
class Look:
    fill: Paint | None = None
    border: Paint | None = None


@dataclass
class Frame:
    element: str
    width: int  # on every side; the kvconfig section uses the same size.* value
    radius: int
    looks: dict[str, Look]  # by state; "" for elements Kvantum names without one


def corner(o: float, r: float, far: float) -> list[Point]:
    """Top-left quarter of a rounded rectangle inset by o, as a convex polygon reaching to far."""
    assert o + r <= far + 1e-9, f"radius {r} at inset {o} does not fit in {far}px"
    if r <= 0:
        return [(o, far), (o, o), (far, o), (far, far)]
    arc = [(o + r - r * math.cos(t), o + r - r * math.sin(t))
           for t in (math.pi / 2 * k / ARC_STEPS for k in range(ARC_STEPS + 1))]
    return [(o, far), *arc, (far, o), (far, far)]


def clip(poly: list[Point], x0: float, y0: float, x1: float, y1: float) -> list[Point]:
    """Sutherland-Hodgman: a convex polygon cut to a box."""
    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)

    edges = [(lambda q: q[0] >= x0, at_x(x0)), (lambda q: q[0] <= x1, at_x(x1)),
             (lambda q: q[1] >= y0, at_y(y0)), (lambda q: q[1] <= y1, at_y(y1))]
    for inside, cross in edges:
        out = []
        for i, cur in enumerate(poly):
            prev = poly[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
        poly = out
        if not poly:
            break
    return poly


def rect(x0: float, y0: float, x1: float, y1: float) -> list[Point]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class Canvas:
    """Packs generated elements into rows below the template's own drawing."""

    def __init__(self, top: float, width: float = 440):
        self.group = ET.Element(f"{{{SVG}}}g", id="generated")
        self.x, self.y, self.row, self.width = 4.0, top, 0.0, width

    def place(self, w: float, h: float) -> tuple[float, float]:
        if self.x + w > self.width:
            self.x, self.y, self.row = 4.0, self.y + self.row + 4, 0.0
        at = (self.x, self.y)
        self.x += w + 4
        self.row = max(self.row, h)
        return at

    def element(self, id: str, at: tuple[float, float], size: tuple[float, float]) -> ET.Element:
        g = ET.SubElement(self.group, f"{{{SVG}}}g", id=id)
        # Invisible box so Kvantum sees the full piece even where nothing is painted.
        ET.SubElement(g, f"{{{SVG}}}rect", x=str(at[0]), y=str(at[1]), width=str(size[0]),
                      height=str(size[1]), fill="#000000", opacity="0.001")
        return g

    @property
    def bottom(self) -> float:
        return self.y + self.row


def paint(parent: ET.Element, d: str, color: Paint, **extra) -> None:
    if color[1] <= 0:
        return
    ET.SubElement(parent, f"{{{SVG}}}path", d=d, fill=color[0], **{"fill-opacity": f"{color[1]:.4g}"}, **extra)


def path(points: list[Point], at: tuple[float, float]) -> str:
    return "M" + " L".join(f"{round(x + at[0], 3)},{round(y + at[1], 3)}" for x, y in points) + " Z"


Layer = tuple[Paint, float, float, float | None, float]  # paint, outer inset/radius, inner inset/radius


def layers(frame: Frame, look: Look, p: SimpleNamespace) -> list[Layer]:
    """From the outside in, with insets measured from the outer edge."""
    r, b = frame.radius, p.ui.border
    out = []
    inset, radius = 0, r
    if look.border:
        out.append((look.border, 0, r, b, max(r - b, 0)))
        inset, radius = b, max(r - b, 0)
    if look.fill:
        out.append((look.fill, inset, radius, None, 0))
    return out


def draw_piece(canvas: Canvas, id: str, stack: list[Layer], region, box: tuple[float, float, float, float],
               mirror_x: bool = False, mirror_y: bool = False) -> None:
    """Cut every layer to box (in the top-left corner piece) and mirror it into place."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    at = canvas.place(w, h)
    g = canvas.element(id, at, (w, h))

    def local(poly):
        return [(w - (x - x0) if mirror_x else x - x0, h - (y - y0) if mirror_y else y - y0)
                for x, y in clip(poly, *box)]

    for color, o, r, io, ir in stack:
        outer = local(region(o, r))
        if len(outer) < 3:
            continue
        d = path(outer, at)
        inner = local(region(io, ir)) if io is not None else []
        if len(inner) >= 3:
            d += " " + path(inner, at)
        paint(g, d, color, **{"fill-rule": "evenodd"})


def draw_frame(canvas: Canvas, frame: Frame, p: SimpleNamespace) -> None:
    f, e = frame.width, EDGE
    corners = {"topleft": (False, False), "topright": (True, False),
               "bottomleft": (False, True), "bottomright": (True, True)}

    def corner_region(o, r):
        return corner(o, r, f)

    def horizontal(o, r):  # top edge; bottom is its mirror
        return rect(0, o, e, f)

    def vertical(o, r):  # left edge; right is its mirror
        return rect(o, 0, f, e)

    for state, look in frame.looks.items():
        base = f"{frame.element}-{state}" if state else frame.element
        stack = layers(frame, look, p)
        for side, (mx, my) in corners.items():
            draw_piece(canvas, f"{base}-{side}", stack, corner_region, (0, 0, f, f), mx, my)
        draw_piece(canvas, f"{base}-top", stack, horizontal, (0, 0, e, f))
        draw_piece(canvas, f"{base}-bottom", stack, horizontal, (0, 0, e, f), mirror_y=True)
        draw_piece(canvas, f"{base}-left", stack, vertical, (0, 0, f, e))
        draw_piece(canvas, f"{base}-right", stack, vertical, (0, 0, f, e), mirror_x=True)
        at = canvas.place(e, e)
        g = canvas.element(base, at, (e, e))
        if look.fill:
            paint(g, path(rect(0, 0, e, e), at), look.fill)


def draw_rect(canvas: Canvas, id: str, size: tuple[float, float], area: tuple[float, float, float, float],
              color: Paint | None) -> None:
    at = canvas.place(*size)
    g = canvas.element(id, at, size)
    if color:
        paint(g, path(rect(*area), at), color)


# ─── indicators ──────────────────────────────────────────────────────────────

def draw_indicator(canvas: Canvas, id: str, round_shape: bool, box: Look, mark: str | None,
                   p: SimpleNamespace) -> None:
    """A check box, radio button or slider handle: a box or circle, optionally with a check, dash or dot."""
    s = sizes(p).indicator
    at = canvas.place(s, s)
    g = canvas.element(id, at, (s, s))
    x, y = at
    b = p.ui.border
    r = s / 2 if round_shape else min(p.ui.radius, s / 2)

    def shape(inset: float) -> str:
        ri = max(r - inset, 0)
        w = s - 2 * inset
        return (f"M{x + inset + ri},{y + inset} h{w - 2 * ri} a{ri},{ri} 0 0 1 {ri},{ri} v{w - 2 * ri} "
                f"a{ri},{ri} 0 0 1 {-ri},{ri} h{-(w - 2 * ri)} a{ri},{ri} 0 0 1 {-ri},{-ri} v{-(w - 2 * ri)} "
                f"a{ri},{ri} 0 0 1 {ri},{-ri} Z")

    if box.border:
        paint(g, shape(0) + " " + shape(b), box.border, **{"fill-rule": "evenodd"})
    if box.fill:
        paint(g, shape(b if box.border else 0), box.fill)
    # Marks are cut out of the light box in the base color, like the Quickshell switch knob.
    stroke = {"fill": "none", "stroke": p.c.base, "stroke-width": "2",
              "stroke-linecap": "round", "stroke-linejoin": "round"}
    if mark == "check":
        ET.SubElement(g, f"{{{SVG}}}path", d=f"M{x + 4.5},{y + 8.5} l2.5,2.5 l4.5,-5.5", **stroke)
    elif mark == "dash":
        ET.SubElement(g, f"{{{SVG}}}path", d=f"M{x + 5},{y + 8} h6", **stroke)
    elif mark == "dot":
        ET.SubElement(g, f"{{{SVG}}}circle", cx=str(x + s / 2), cy=str(y + s / 2), r="3.5", fill=p.c.base)


def draw_indicators(canvas: Canvas, p: SimpleNamespace) -> None:
    c, o = p.c, p.opacity
    off = {
        "normal": Look(fill=(c.surface, o.muted), border=(c.border, 1)),
        "focused": Look(fill=(c.surface, 1), border=(c.textDim, 1)),
        "pressed": Look(fill=(c.overlay, 1), border=(c.textDim, 1)),
        "disabled": Look(fill=(c.surface, o.muted / 2), border=(c.border, o.muted)),
    }
    on = {
        "normal": Look(fill=(c.text, 1)),
        "focused": Look(fill=(c.text, 1)),
        "pressed": Look(fill=(c.textDim, 1)),
        "disabled": Look(fill=(c.textFaint, o.muted)),
    }
    for prefix in ("", "menu-"):
        for state in off:
            draw_indicator(canvas, f"{prefix}checkbox-{state}", False, off[state], None, p)
            draw_indicator(canvas, f"{prefix}checkbox-checked-{state}", False, on[state], "check", p)
            draw_indicator(canvas, f"{prefix}checkbox-tristate-{state}", False, on[state], "dash", p)
            draw_indicator(canvas, f"{prefix}radio-{state}", True, off[state], None, p)
            draw_indicator(canvas, f"{prefix}radio-checked-{state}", True, on[state], "dot", p)
    # Slider handle: a plain knob, like the Quickshell switch.
    knob = {
        "normal": Look(fill=(c.text, 1)),
        "focused": Look(fill=(c.text, 1), border=(c.borderActive, 1)),
        "pressed": Look(fill=(c.textDim, 1)),
        "disabled": Look(fill=(c.textFaint, 1)),
    }
    for element in ("slidercursor", "slidercursor-tickless"):
        for state, look in knob.items():
            draw_indicator(canvas, f"{element}-{state}", True, look, None, p)


# ─── widget catalogue ────────────────────────────────────────────────────────


def frames(p: SimpleNamespace) -> list[Frame]:
    c, o, size = p.c, p.opacity, sizes(p)
    r, f = p.ui.radius, size.frame
    clear = Look()
    solid = Look(fill=(c.base, 1))
    hover = Look(fill=(c.surface, o.hover))

    def disabled(look: Look) -> Look:
        dim = lambda paint: paint and (paint[0], paint[1] * o.muted)
        return Look(fill=dim(look.fill), border=dim(look.border))

    # Buttons and combo boxes: notification action buttons.
    button = {
        "normal": Look(fill=(c.surface, o.muted)),
        "focused": Look(fill=(c.surface, 1)),
        "pressed": Look(fill=(c.overlay, 1)),
        "toggled": Look(fill=(c.overlay, 1)),
    }
    button["disabled"] = disabled(button["normal"])
    # Keyboard focus on a button (focusFrame=true): a ring on its own edge.
    button["focus"] = Look(border=(c.borderActive, o.muted))
    # Text fields: the network popup's password field.
    field = {
        "normal": Look(fill=(c.surface, o.muted), border=(c.border, 1)),
        "focused": Look(fill=(c.surface, o.muted), border=(c.borderActive, 1)),
    }
    field["disabled"] = disabled(field["normal"])
    field["focus"] = Look()  # the focused border already shows it
    # Flat buttons (tool buttons, menu bar items, tabs): bar workspaces.
    flat = {
        "normal": clear,
        "focused": hover,
        "pressed": Look(fill=(c.surface, 1)),
        "toggled": Look(fill=(c.surface, o.active)),
        "disabled": clear,
    }
    # Rows in menus and lists: popup rows; selection like rofi's selected entry.
    row = {"normal": clear, "focused": hover, "pressed": Look(fill=(c.overlay, 1)),
           "toggled": Look(fill=(c.overlay, 1)), "disabled": clear}
    # Kvantum highlights menu rows with the pressed/toggled look; a solid surface
    # reads better than the popup hover tint over a whole menu.
    menu_row = {**row, "pressed": Look(fill=(c.surface, 1)), "toggled": Look(fill=(c.surface, 1))}
    # Menus and tooltips: Quickshell popup surface.
    popup = {"normal": Look(fill=(c.base, o.popup), border=(c.border, o.popup))}

    groove, thumb = size.groove, size.thumb
    return [
        Frame("button", f, r, button),
        Frame("combo", f, r, button),
        Frame("lineedit", f, r, field),
        Frame("tbutton", f, r, flat),
        Frame("menubaritem", f, r, flat),
        Frame("tab", f, r, flat),
        Frame("itemview", f, r, row),
        Frame("menuitem", f, r, menu_row),
        Frame("menu", size.popup, r, popup),
        Frame("tooltip", size.popup, r, popup),
        Frame("tabframe", f, r, {"normal": Look(border=(c.surface, o.muted))}),
        # Focus rectangle around text (check boxes, list rows); Kvantum names its
        # pieces focus-<side>, without a state.
        Frame("focus", size.focus, size.focus, {"": Look(border=(c.borderActive, o.muted))}),
        # Plain backgrounds: square, and for most only the middle is ever drawn.
        Frame("common", 1, 0, {"normal": clear, "focused": clear}),
        Frame("dock", 1, 0, {"normal": solid, "focused": solid}),
        Frame("window", 1, 0, {"normal": solid, "normal-inactive": solid}),
        Frame("menubar", 1, 0, {"normal": clear, "normal-inactive": clear}),
        Frame("tabBarFrame", 1, 0, {"normal": clear, "toggled": clear}),
        Frame("scrollbarslider", thumb, thumb, {
            "normal": Look(fill=(c.textFaint, o.muted)),
            "focused": Look(fill=(c.textDim, 1)),
            "pressed": Look(fill=(c.text, 1)),
            "disabled": Look(fill=(c.textFaint, o.hover)),
        }),
        Frame("progress", groove, groove, {
            "normal": Look(fill=(c.surface, o.muted)),
            "disabled": Look(fill=(c.surface, o.hover)),
        }),
        Frame("progress-pattern", groove, groove, {
            "normal": Look(fill=(c.text, 1)),
            "disabled": Look(fill=(c.textFaint, o.muted)),
        }),
        Frame("slider", groove, groove, {
            "normal": Look(fill=(c.surface, 1)),
            "focused": Look(fill=(c.surface, 1)),
            "disabled": Look(fill=(c.surface, o.muted)),
            "toggled": Look(fill=(c.text, 1)),
        }),
    ]


def draw_header(canvas: Canvas, p: SimpleNamespace) -> None:
    """Table headers: flat, one separator line along the bottom, one between sections."""
    c, o = p.c, p.opacity
    line = (c.surface, o.muted)
    looks = {"normal": None, "focused": (c.surface, o.hover), "pressed": (c.surface, o.muted),
             "toggled": (c.surface, o.hover)}
    for state, fill in looks.items():
        draw_rect(canvas, f"header-{state}", (EDGE, EDGE), (0, 0, EDGE, EDGE), fill)
        draw_rect(canvas, f"header-{state}-bottom", (EDGE, 1), (0, 0, EDGE, 1), line)
    draw_rect(canvas, "header-separator", (1, 16), (0, 3, 1, 13), line)
    # Menu separator: the popups' 1px line, centred in a 5px gap.
    draw_rect(canvas, "menuitem-separator", (EDGE, 5), (0, 2, EDGE, 3), line)


# ─── Graphite template ───────────────────────────────────────────────────────

SIDE = r"(-(top|bottom|left|right|topleft|topright|bottomleft|bottomright)(-(left|right)junct)?)?"
STATE = r"-(normal|focused|pressed|toggled|disabled)(-inactive)?"
REDRAWN = re.compile(
    r"^("
    r"(button|combo|common|dock|header|itemview|lineedit|menubaritem|menubar|menuitem|menu|tooltip"
    r"|progress|progress-pattern|scrollbarslider|slider|slidercursor|slidercursor-tickless|tab|tabframe"
    r"|tabBarFrame|tbutton|window|(menu-)?(checkbox|checkbox-checked|checkbox-tristate|radio|radio-checked))"
    + STATE + SIDE
    + r"|focus" + SIDE
    + r"|header-separator"
    + r"|(menu|tooltip)-shadow(-hint)?" + SIDE
    + r")$"
)

# Graphite colors left in the kept icons, mapped to palette tokens.
RECOLOR = {
    "#ffffff": "text", "#fff": "text", "#f2f2f2": "text", "#e0e0e0": "text", "#dfdfdf": "text",
    "#c1c1c1": "textDim", "#b6b6b6": "textDim", "#989898": "textFaint", "#acb1bc": "textFaint",
    "#2c2c2c": "base", "#333333": "base", "#333": "base", "#343031": "base", "#26272a": "base",
    "#1e1e1e": "base", "#1f1f1f": "base", "#3c3c3c": "surface", "#505050": "surface", "#525252": "surface",
    "#5a5a5a": "overlay", "#5a616e": "overlay", "#646464": "overlay", "#666666": "overlay",
    "#1a1a1a": "shadow", "#212121": "base",
    "#4285f4": "text", "#b74aff": "text", "#f04a50": "urgent",
}


def recolor(value: str, p: SimpleNamespace) -> str:
    def swap(m: re.Match) -> str:
        token = RECOLOR.get(m.group(0).lower())
        return getattr(p.c, token) if token else m.group(0)
    return re.sub(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", swap, value)


def build_svg(p: SimpleNamespace) -> str:
    tree = ET.parse(TEMPLATES / "kvantum.svg")
    root = tree.getroot()
    parents = {child: parent for parent in root.iter() for child in parent}

    removed = [e for e in root.iter() if REDRAWN.match(e.get("id") or "")]
    removed_ids = {d.get("id") for e in removed for d in e.iter() if d.get("id")}
    for e in removed:
        if e in parents and e in list(parents[e]):
            parents[e].remove(e)
    dangling = {e.get(f"{{{XLINK}}}href", "")[1:] for e in root.iter()} & removed_ids
    if dangling:
        sys.exit(f"kept elements still reference removed ones: {sorted(dangling)}")

    for e in root.iter():
        for attr in ("style", "fill", "stroke", "stop-color", "color"):
            if e.get(attr):
                e.set(attr, recolor(e.get(attr), p))

    template_height = float(root.get("height"))
    canvas = Canvas(top=template_height + 20)
    for frame in frames(p):
        draw_frame(canvas, frame, p)
    draw_header(canvas, p)
    draw_indicators(canvas, p)
    root.append(canvas.group)
    root.set("height", str(math.ceil(canvas.bottom + 10)))
    root.set("{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}docname", f"{p.name}.svg")

    ET.indent(tree, space=" ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


# ─── main ────────────────────────────────────────────────────────────────────


def main() -> None:
    p = load_palette()
    kvantum = ROOT / "Kvantum" / p.name
    schemes = ROOT / "color-schemes"
    # Output is fully generated; drop whatever an earlier name left behind.
    shutil.rmtree(ROOT / "Kvantum", ignore_errors=True)
    shutil.rmtree(schemes, ignore_errors=True)
    kvantum.mkdir(parents=True, exist_ok=True)
    schemes.mkdir(exist_ok=True)

    (kvantum / f"{p.name}.kvconfig").write_text(render((TEMPLATES / "kvantum.kvconfig").read_text(), p))
    (kvantum / f"{p.name}.svg").write_text(build_svg(p))
    (schemes / f"{p.name}.colors").write_text(render((TEMPLATES / "color-scheme.colors").read_text(), p))
    print(f"built {p.name}: {kvantum.relative_to(ROOT)}/, {schemes.relative_to(ROOT)}/{p.name}.colors")


if __name__ == "__main__":
    main()
