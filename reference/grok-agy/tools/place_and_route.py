#!/usr/bin/env python3
"""Place the Z80 Eurocard and route the signal nets.

The schematic is the netlist. This script does not change it. Power stays
on the inner planes. Signals use the front and back copper. There is no
FreeRouting pass.
"""

import heapq
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "Z80_Simple_Computer.kicad_pcb"
NETLIST = Path("/tmp/z80-j5.xml")
FP = ROOT / "libs/kicad-footprints-10.0.7"

# Courtyard sizes measured from the KiCad 10.0.7 footprints, millimetres.
# DIP length is along Y in the library orientation.
W = {
    "U6": 9.8, "U5": 17.4, "U1": 17.4, "U2": 17.4,
    "U3": 17.4, "U7": 9.8, "U8": 9.8, "U4": 17.4,
}
ORDER = ["U6", "U5", "U1", "U2", "U3", "U7", "U8", "U4"]
ICY = 54.0
GAP = 1.2
X0 = 34.0

TRACK = 0.25
CLEAR = 0.20
# The Power net class in the project is wider than the default.
POWER_CLEAR = 0.30
VIA_D = 0.80
VIA_DRILL = 0.40
# Centre of a track must stay this far from the centre of another track.
TRACK_KEEP = TRACK + CLEAR
# A via is wider than a track, so its centre needs a larger keep-out.
VIA_KEEP = VIA_D / 2 + CLEAR + TRACK / 2
# How much further than a track centre a via centre must sit.
# A little past the ideal, so a via cannot sit on the diagonal of the grid
# and miss the keep-out by a fraction of a millimetre.
VIA_EXTRA = 0.40
# Board setup: holes at least 0.25 mm apart, copper 0.30 mm from the edge.
HOLE_GAP = 0.25
STEP = 0.25


def mm(v):
    return pcbnew.FromMM(v)


def xy(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def centers():
    x = X0
    out = {}
    for ref in ORDER:
        w = W[ref]
        out[ref] = (round(x + w / 2, 3), ICY)
        x += w + GAP
    return out


def load_new(lib_dir, name):
    fp = pcbnew.FootprintLoad(str(lib_dir), name)
    if fp is None:
        raise SystemExit(f"could not load {name}")
    return fp


def parse_netlist(path):
    root = ET.parse(path).getroot()
    comps = {}
    for comp in root.findall(".//comp"):
        ref = comp.get("ref")
        fp = comp.findtext("footprint") or ""
        value = comp.findtext("value") or ""
        comps[ref] = (fp, value)
    nets = defaultdict(list)  # name -> [(ref, pin)]
    for net in root.findall(".//net"):
        name = net.get("name")
        for node in net.findall("node"):
            nets[name].append((node.get("ref"), node.get("pin")))
    return comps, nets


def ensure_net(board, name):
    net = board.FindNet(name)
    if net is None or net.GetNetCode() == 0 and name:
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        net = board.FindNet(name)
    return net


def add_edge(board):
    corners = [(0, 0), (160, 0), (160, 100), (0, 100)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1]):
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_SEGMENT)
        shape.SetStart(xy(x1, y1))
        shape.SetEnd(xy(x2, y2))
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetWidth(mm(0.1))
        board.Add(shape)


def add_zone(board, layer, netname):
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    net = ensure_net(board, netname)
    zone.SetNet(net)
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    zone.SetThermalReliefGap(mm(0.3))
    zone.SetThermalReliefSpokeWidth(mm(0.4))
    zone.SetMinThickness(mm(0.25))
    zone.SetLocalClearance(mm(CLEAR))
    zone.SetAssignedPriority(0)
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in ((0.4, 0.4), (159.6, 0.4), (159.6, 99.6), (0.4, 99.6)):
        outline.Append(mm(x), mm(y))
    board.Add(zone)


# Anchor minus courtyard centre, measured from the stock footprints.
OFF = {
    "U1": (7.62, 24.12), "U4": (7.62, 24.12), "U5": (7.62, 24.12),
    "U2": (7.62, 16.51), "U3": (7.62, 16.51),
    "U6": (3.80, 8.89), "U7": (3.80, 7.62), "U8": (3.80, 7.62),
    "Y1": (7.62, -3.81), "Y2": (3.81, -3.81),
    "J1": (-5.54, 6.27), "J2": (0.0, 8.89), "J3": (0.0, 8.89), "J4": (0.0, 8.89),
    "J5": (2.55, -0.30),
    "R1": (5.08, 0.0), "R2": (5.08, 0.0),
    "SW1": (3.25, 2.25),
    "C1": (1.25, 0.0),
    "H1": (0.0, 0.0), "H2": (0.0, 0.0), "H3": (0.0, 0.0), "H4": (0.0, 0.0),
}
for _ref in ("C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11"):
    OFF[_ref] = (2.50, 0.0)
for _ref in ("C20", "C21", "C22", "C23", "C24"):
    OFF[_ref] = (1.25, 0.0)


def court_centers():
    """Where each courtyard should sit. Y is up. The board is 160 by 100."""
    # U6 sits beside the DE9. The other DIPs are shifted less, so U4
    # keeps about 10 mm of board to its right.
    chips = {"U6": (23.0, 50.0)}
    chip_w = [("U5", 17.4), ("U1", 17.4), ("U2", 17.4),
              ("U3", 17.4), ("U7", 9.8), ("U8", 9.8), ("U4", 17.4)]
    x = 36.1
    for ref, w in chip_w:
        chips[ref] = (round(x + w / 2, 3), 50.0)
        x += w + 1.2
    pos = dict(chips)
    pos.update({
        "J2": (118.0, 12.0),
        "J3": (130.0, 12.0),
        "J4": (142.0, 12.0),
        "Y1": (16.0, 78.0),
        "Y2": (chips["U5"][0], 16.0),
        "H1": (5.0, 5.0), "H2": (155.0, 5.0),
        "H3": (5.0, 95.0), "H4": (155.0, 95.0),
        # Reset parts sit under the DE9, clear of Y2 and of the chip row.
        "SW1": (18.0, 16.0),
        "R1": (18.0, 26.0),
        "R2": (18.0, 32.0),
        # C1 is the reset timing cap. It stays with R1 and the switch.
        "C1": (28.0, 18.0),
    })
    return pos


# Pin-1 anchor and rotation in degrees. In this pcbnew, a positive angle
# sends the footprint's +Y toward board +X.
# J1 plug is the footprint's +Y. -90 sends that plug off the left edge.
# The flange is on x=0.
# J5 wire holes are the footprint's +Y face (the model ends at local y=+5.2).
# +90 sends that face off the right edge. The pads stay on the board.
ANCHOR = {
    "J1": (7.78, 55.54, -90),
    "J5": (156.8, 19.08, 90),
}

# Pin-1 anchor and rotation for the capacitors. A disc at rotation 0 has
# pin 2 five millimetres to the right. A radial at -90 has pin 2 below pin 1.
# The 100 nF parts are one global bypass per package. The 1 uF parts C20,
# C21, C23 and C24 are the MAX232 pump and sit on U6's pins. C22 is the only
# capacitor straight across +5V and GND, so it sits by the screw terminal.
CAP_ANCHOR = {
    "C2": (53.28, 77.65, 0),
    "C3": (85.38, 29.97, 0),
    "C4": (105.72, 29.97, 0),
    "C5": (144.00, 77.65, 0),
    "C6": (52.94, 22.36, 0),
    "C7": (22.60, 37.61, 0),
    "C8": (115.00, 38.86, 0),
    "C9": (125.98, 38.86, 0),
    "C10": (5.88, 69.42, 0),
    "C11": (38.49, 7.42, 0),
    "C20": (14.90, 42.40, -90),
    "C21": (14.90, 48.39, -90),
    "C22": (146.60, 16.54, 0),
    # Above the pump caps. The old spot beside R2 is now the chip.
    "C23": (14.90, 36.42, -90),
    "C24": (13.65, 55.63, 0),
}


def place_all(board, comps):
    courts = court_centers()
    pos = {ref: (cx - OFF[ref][0], cy - OFF[ref][1], 0) for ref, (cx, cy) in courts.items()}
    pos.update(ANCHOR)
    pos.update(CAP_ANCHOR)

    new_libs = {
        "MountingHole": FP / "MountingHole.pretty",
        "TerminalBlock_Phoenix": FP / "TerminalBlock_Phoenix.pretty",
    }
    for ref, (fpid, value) in comps.items():
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            lib, name = fpid.split(":", 1)
            fp = load_new(new_libs[lib], name)
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
            board.Add(fp)
            fp.SetReference(ref)
        fp.SetValue(value)
        if ref not in pos:
            raise SystemExit(f"no position for {ref}")
        x, y, deg = pos[ref]
        fp.SetOrientation(pcbnew.EDA_ANGLE(deg, pcbnew.DEGREES_T))
        fp.SetPosition(xy(x, y))
    return pos


# The DE9 plug and the screw-terminal opening are meant to cross the outline.
OFF_BOARD_OK = {"J1", "J5"}


def courtyard_outside(board):
    """Courtyards that leave the 160 by 100 mm outline."""
    hits = []
    for fp in board.GetFootprints():
        if fp.GetReference() in OFF_BOARD_OK:
            continue
        box = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
        x0, y0 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop())
        x1, y1 = pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())
        if x0 < -0.05 or y0 < -0.05 or x1 > 160.05 or y1 > 100.05:
            hits.append((fp.GetReference(), round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)))
    return hits


def pads_outside(board):
    """Pads that are not on the board. The connector bodies may hang off."""
    hits = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            x, y = pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)
            if x < 1.0 or y < 1.0 or x > 159.0 or y > 99.0:
                hits.append((fp.GetReference(), pad.GetNumber(), round(x, 2), round(y, 2)))
    return hits


def courtyard_overlaps(board):
    boxes = []
    for fp in board.GetFootprints():
        box = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
        boxes.append((fp.GetReference(), box))
    hits = []
    for i, (a, ba) in enumerate(boxes):
        for b, bb in boxes[i + 1:]:
            if ba.Intersects(bb):
                hits.append((a, b))
    return hits


def apply_nets(board, nets):
    # A switch can have two pads that share one number. Both need the net.
    pads = defaultdict(list)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            num = pad.GetNumber()
            if not num:
                continue
            pads[(fp.GetReference(), num)].append(pad)
            pad.SetNet(ensure_net(board, ""))
    missing = []
    for name, nodes in nets.items():
        net = ensure_net(board, name)
        for ref, pin in nodes:
            found = pads.get((ref, pin))
            if not found:
                missing.append((name, ref, pin))
                continue
            for pad in found:
                pad.SetNet(net)
    return missing


def _mark_rect(grids, x0, y0, x1, y1, nx, ny):
    ix0 = max(0, int(x0 / STEP))
    iy0 = max(0, int(y0 / STEP))
    ix1 = min(nx - 1, int(x1 / STEP))
    iy1 = min(ny - 1, int(y1 / STEP))
    for grid in grids:
        for iy in range(iy0, iy1 + 1):
            row = iy * nx
            for ix in range(ix0, ix1 + 1):
                grid[row + ix] = 1


def build_grid(board):
    """Per-layer occupancy. Through-hole pads block both signal layers."""
    nx = int(round(160 / STEP)) + 1
    ny = int(round(100 / STEP)) + 1
    base = [bytearray(nx * ny), bytearray(nx * ny)]
    edge = int(round(0.5 / STEP))
    _mark_rect(base, 0, 0, edge * STEP, 100, nx, ny)
    _mark_rect(base, 160 - edge * STEP, 0, 160, 100, nx, ny)
    _mark_rect(base, 0, 0, 160, edge * STEP, nx, ny)
    _mark_rect(base, 0, 100 - edge * STEP, 160, 100, nx, ny)
    pads = []
    no_via = bytearray(nx * ny)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pos = pad.GetPosition()
            size = pad.GetSize()
            cx, cy = pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y)
            gap = POWER_CLEAR if pad.GetNetname() in ("+5V", "GND") else CLEAR
            hw = pcbnew.ToMM(size.x) / 2 + gap + TRACK / 2
            hh = pcbnew.ToMM(size.y) / 2 + gap + TRACK / 2
            pads.append((cx, cy, hw, hh, fp.GetReference(), pad.GetNumber(), pad.GetNetname()))
            # Unnumbered pads (DE9 shell, mounting holes) stay off every net.
            if not pad.GetNetname():
                _mark_rect(base, cx - hw, cy - hh, cx + hw, cy + hh, nx, ny)
            drill = pad.GetDrillSize()
            if drill.x:
                hole_r = max(pcbnew.ToMM(drill.x), pcbnew.ToMM(drill.y)) / 2
                mark_disk_one(no_via, cx, cy, hole_r + VIA_DRILL / 2 + HOLE_GAP, nx, ny)
    return base, pads, nx, ny, no_via


def route_signals(board, base, pads, nx, ny, no_via, limit=300000, only=None):
    by_net = defaultdict(list)
    for cx, cy, hw, hh, ref, num, net in pads:
        if net and net not in ("+5V", "GND"):
            by_net[net].append((cx, cy, hw, hh, ref, num))

    routed = 0
    failed = []
    # Wide nets first. A short net routed earlier will fill the gap
    # between two DIP rows and leave the buses with no way out.
    jobs = []
    for name, group in by_net.items():
        # Unique pad centres. A DIP does not share them.
        pts = []
        for cx, cy, hw, hh, ref, num in group:
            key = (round(cx, 3), round(cy, 3))
            if key not in [(p[0], p[1]) for p in pts]:
                pts.append((cx, cy, hw, hh))
        if len(pts) < 2:
            continue
        if only is not None and name not in only:
            continue
        jobs.append((len(pts), name, pts))
    jobs.sort(key=lambda row: -row[0])
    for _, name, pts in jobs:
        print(f"route {name} pads {len(pts)}", flush=True)
        blocked = [bytearray(base[0]), bytearray(base[1])]
        for cx, cy, hw, hh, ref, num, net in pads:
            if net == name:
                continue
            _mark_rect(blocked, cx - hw, cy - hh, cx + hw, cy + hh, nx, ny)
        # Free this net's own pads so the router can enter them.
        for cx, cy, hw, hh in pts:
            ix0 = max(0, int((cx - hw) / STEP))
            iy0 = max(0, int((cy - hh) / STEP))
            ix1 = min(nx - 1, int((cx + hw) / STEP))
            iy1 = min(ny - 1, int((cy + hh) / STEP))
            for grid in blocked:
                for iy in range(iy0, iy1 + 1):
                    row = iy * nx
                    for ix in range(ix0, ix1 + 1):
                        grid[row + ix] = 0
        # GetTracks() returns a new Python wrapper every call, so id()
        # cannot be used to tell old tracks from new ones. A failed net
        # used to delete every track on the board.
        before = {item.m_Uuid.AsString() for item in board.GetTracks()}
        ok = connect_group(board, name, pts, blocked, nx, ny, no_via, limit)
        if not ok:
            for track in list(board.GetTracks()):
                if track.m_Uuid.AsString() not in before:
                    board.Delete(track)
            failed.append(name)
            continue
        routed += 1
        # Keep the copper on the shared maps so the next net cannot cross it
        # on the same layer. A via blocks both layers.
        for item in board.GetTracks():
            if item.GetNetname() != name:
                continue
            if item.Type() == pcbnew.PCB_VIA_T:
                p = item.GetPosition()
                mark_disk(base, pcbnew.ToMM(p.x), pcbnew.ToMM(p.y),
                          VIA_KEEP, nx, ny, (0, 1))
            else:
                a, b = item.GetStart(), item.GetEnd()
                layer = 0 if item.GetLayer() == pcbnew.F_Cu else 1
                mark_seg(base, pcbnew.ToMM(a.x), pcbnew.ToMM(a.y),
                         pcbnew.ToMM(b.x), pcbnew.ToMM(b.y),
                         TRACK_KEEP, nx, ny, (layer,))
    return routed, failed


def mark_disk(grids, cx, cy, rad, nx, ny, layers):
    ix0 = max(0, int((cx - rad) / STEP))
    iy0 = max(0, int((cy - rad) / STEP))
    ix1 = min(nx - 1, int((cx + rad) / STEP))
    iy1 = min(ny - 1, int((cy + rad) / STEP))
    r2 = rad * rad
    for layer in layers:
        grid = grids[layer]
        for iy in range(iy0, iy1 + 1):
            y = iy * STEP
            row = iy * nx
            for ix in range(ix0, ix1 + 1):
                x = ix * STEP
                if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                    grid[row + ix] = 1


def mark_seg(grids, x1, y1, x2, y2, rad, nx, ny, layers):
    steps = int(max(abs(x2 - x1), abs(y2 - y1)) / STEP) + 1
    if steps <= 0:
        mark_disk(grids, x1, y1, rad, nx, ny, layers)
        return
    for i in range(steps + 1):
        t = i / steps
        mark_disk(grids, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, rad, nx, ny, layers)


def connect_group(board, name, pts, blocked, nx, ny, no_via, limit):
    net = board.FindNet(name)
    linked = [pts[0]]
    rest = pts[1:]
    while rest:
        best = None
        best_path = None
        for a in linked:
            for b in rest:
                path = astar(a, b, blocked, nx, ny, no_via, limit)
                if path is None:
                    continue
                if best is None or len(path) < len(best_path):
                    best = b
                    best_path = path
        if best is None:
            return False
        lay_path(board, net, best_path)
        # Free nothing: the new track is reserved by the caller after success.
        # Inside this net, allow the next search to reuse the same cells.
        for x, y, layer in best_path:
            ix = int(round(x / STEP))
            iy = int(round(y / STEP))
            if 0 <= ix < nx and 0 <= iy < ny:
                blocked[layer][iy * nx + ix] = 0
        linked.append(best)
        rest.remove(best)
    return True


def mark_disk_one(grid, cx, cy, rad, nx, ny):
    ix0 = max(0, int((cx - rad) / STEP))
    iy0 = max(0, int((cy - rad) / STEP))
    ix1 = min(nx - 1, int((cx + rad) / STEP))
    iy1 = min(ny - 1, int((cy + rad) / STEP))
    r2 = rad * rad
    for iy in range(iy0, iy1 + 1):
        y = iy * STEP
        row = iy * nx
        for ix in range(ix0, ix1 + 1):
            x = ix * STEP
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                grid[row + ix] = 1


def via_ok(ix, iy, blocked, no_via, nx, ny):
    """A via needs a clear neighbourhood, and it must not land on a hole."""
    if no_via[iy * nx + ix]:
        return False
    reach = int(VIA_EXTRA / STEP) + 1
    limit2 = (VIA_EXTRA / STEP) ** 2
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            if dx * dx + dy * dy > limit2:
                continue
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < nx and 0 <= jy < ny):
                return False
            cell = jy * nx + jx
            if blocked[0][cell] or blocked[1][cell] or no_via[cell]:
                return False
    return True


def astar(a, b, blocked, nx, ny, no_via, limit):
    start = (int(round(a[0] / STEP)), int(round(a[1] / STEP)), 0)
    goal = (int(round(b[0] / STEP)), int(round(b[1] / STEP)))
    if not (0 <= start[0] < nx and 0 <= start[1] < ny):
        return None
    if not (0 <= goal[0] < nx and 0 <= goal[1] < ny):
        return None

    def h(ix, iy):
        return abs(ix - goal[0]) + abs(iy - goal[1])

    openq = [(h(*start[:2]), 0, start)]
    came = {start: None}
    gscore = {start: 0}
    seen = 0
    while openq:
        _, g, cur = heapq.heappop(openq)
        ix, iy, layer = cur
        if (ix, iy) == goal:
            path = []
            while cur is not None:
                path.append((cur[0] * STEP, cur[1] * STEP, cur[2]))
                cur = came[cur]
            path.reverse()
            return path
        if g != gscore[cur]:
            continue
        seen += 1
        if seen > limit:
            return None
        steps = ((1, 0), (-1, 0), (0, 1), (0, -1))
        for dx, dy in steps:
            nxt = (ix + dx, iy + dy, layer)
            if not (0 <= nxt[0] < nx and 0 <= nxt[1] < ny):
                continue
            if blocked[layer][nxt[1] * nx + nxt[0]]:
                continue
            ng = g + 1
            if ng < gscore.get(nxt, 1e18):
                gscore[nxt] = ng
                came[nxt] = cur
                heapq.heappush(openq, (ng + h(nxt[0], nxt[1]), ng, nxt))
        other_layer = 1 - layer
        other = (ix, iy, other_layer)
        if via_ok(ix, iy, blocked, no_via, nx, ny):
            ng = g + 8
            if ng < gscore.get(other, 1e18):
                gscore[other] = ng
                came[other] = cur
                heapq.heappush(openq, (ng + h(ix, iy), ng, other))
    return None


def lay_path(board, net, path):
    layers = (pcbnew.F_Cu, pcbnew.B_Cu)
    run_layer = path[0][2]
    run_x, run_y = path[0][0], path[0][1]
    prev = path[0]
    for pt in path[1:]:
        if pt[2] != prev[2]:
            # Finish the track on the layer we are leaving, or the via
            # sits at the corner with nothing reaching it.
            if not (run_x == prev[0] and run_y == prev[1]):
                track = pcbnew.PCB_TRACK(board)
                track.SetStart(xy(run_x, run_y))
                track.SetEnd(xy(prev[0], prev[1]))
                track.SetWidth(mm(TRACK))
                track.SetLayer(layers[run_layer])
                track.SetNet(net)
                board.Add(track)
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(xy(prev[0], prev[1]))
            via.SetWidth(mm(VIA_D))
            via.SetDrill(mm(VIA_DRILL))
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            via.SetNet(net)
            board.Add(via)
            run_layer = pt[2]
            run_x, run_y = prev[0], prev[1]
        elif (pt[0] - prev[0]) * (prev[1] - run_y) != (pt[1] - prev[1]) * (prev[0] - run_x):
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(xy(run_x, run_y))
            track.SetEnd(xy(prev[0], prev[1]))
            track.SetWidth(mm(TRACK))
            track.SetLayer(layers[run_layer])
            track.SetNet(net)
            board.Add(track)
            run_x, run_y = prev[0], prev[1]
        prev = pt
    if run_x == prev[0] and run_y == prev[1]:
        return
    track = pcbnew.PCB_TRACK(board)
    track.SetStart(xy(run_x, run_y))
    track.SetEnd(xy(prev[0], prev[1]))
    track.SetWidth(mm(TRACK))
    track.SetLayer(layers[run_layer])
    track.SetNet(net)
    board.Add(track)


def clear_copper(board):
    # Delete, not Remove. Remove unlinks the object and then pcbnew 10
    # segfaults when the script continues.
    for track in list(board.GetTracks()):
        board.Delete(track)
    for zone in list(board.Zones()):
        board.Delete(zone)
    for drawing in list(board.GetDrawings()):
        if drawing.GetLayer() == pcbnew.Edge_Cuts:
            board.Delete(drawing)


def main():
    comps, nets = parse_netlist(NETLIST)
    board = pcbnew.LoadBoard(str(BOARD))
    clear_copper(board)
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().SetBoardThickness(mm(1.6))
    place_all(board, comps)
    missing = apply_nets(board, nets)
    add_edge(board)
    add_zone(board, pcbnew.In1_Cu, "GND")
    add_zone(board, pcbnew.In2_Cu, "+5V")
    hits = courtyard_overlaps(board)
    outside = courtyard_outside(board)
    pad_hits = pads_outside(board)
    print(f"courtyard overlaps {len(hits)}")
    for pair in hits[:30]:
        print(" ", pair[0], pair[1])
    print(f"courtyards outside the outline {len(outside)}")
    for row in outside:
        print(" ", row)
    print(f"pads outside the outline {len(pad_hits)}")
    for row in pad_hits:
        print(" ", row)
    for ref in ("J1", "J5"):
        fp = board.FindFootprintByReference(ref)
        box = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
        print(
            ref,
            "rot", fp.GetOrientation().AsDegrees(),
            "anchor", round(pcbnew.ToMM(fp.GetPosition().x), 2),
            round(pcbnew.ToMM(fp.GetPosition().y), 2),
            "court",
            round(pcbnew.ToMM(box.GetLeft()), 2),
            round(pcbnew.ToMM(box.GetTop()), 2),
            round(pcbnew.ToMM(box.GetRight()), 2),
            round(pcbnew.ToMM(box.GetBottom()), 2),
        )
    if hits or outside or pad_hits:
        print("stop: placement does not fit the Eurocard")
        return
    # Placement only. The grid router is not used.
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(str(BOARD))
    print("missing pad assignments", len(missing))
    print("tracks", len(list(board.GetTracks())))
    print("saved", BOARD)
    return
    base, pads, nx, ny, no_via = build_grid(board)
    bare = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNumber() and not pad.GetNetname():
                bare.append(f"{fp.GetReference()}.{pad.GetNumber()}")
    print("numbered pads without a net", len(bare), " ".join(bare[:12]))
    routed, failed = route_signals(board, base, pads, nx, ny, no_via, limit=300000)
    if failed:
        print(f"retry {len(failed)} nets", flush=True)
        routed2, failed = route_signals(
            board, base, pads, nx, ny, no_via, limit=800000, only=set(failed))
        routed += routed2
    print(f"routed nets {routed} failed {len(failed)}")
    for name in failed:
        print(" ", name)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(str(BOARD))
    print("missing pad assignments", len(missing))
    for row in missing[:20]:
        print(" ", row)
    print("saved", BOARD)


if __name__ == "__main__":
    main()
