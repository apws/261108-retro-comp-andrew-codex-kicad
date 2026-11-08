#!/usr/bin/env python3
"""Route a specified netgroup in Z80_Simple_Computer.kicad_pcb using a Manhattan maze router.

Respects:
- JLCPCB 4-layer design rules:
  - Default netclass: track width 0.15 mm, clearance 0.15 mm, via 0.70 mm / 0.30 mm drill.
  - Power netclass (+5V, GND): clearance 0.30 mm.
- Solid In1.Cu (GND) and In2.Cu (+5V) inner planes.
- Multi-pin nets routed via progressive Steiner expansion.
"""

import sys
import math
import heapq
import argparse
import subprocess
from pathlib import Path
from collections import defaultdict
import pcbnew

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARD = ROOT / "Z80_Simple_Computer.kicad_pcb"
DEFAULT_OUT_REPORT = ROOT / "out/drc.rpt"

STEP = 0.1  # 0.1 mm grid
BOARD_W = 160.0
BOARD_H = 100.0
NX = int(BOARD_W / STEP) + 1
NY = int(BOARD_H / STEP) + 1
STRIDE = NX * NY

TRACK_W = 0.15
CLEARANCE = 0.15
POWER_CLEARANCE = 0.30
VIA_DIA = 0.70
VIA_DRILL = 0.30
VIA_RAD = VIA_DIA / 2.0

# Groups of nets
NET_GROUPS = {
    "group1": [
        "/CLK",
        "/CLK_UART",
        "/~{RESET}",
        "/SW_RESET",
        "/RESET_RC",
        "/RESET_H",
    ],
    "group2": [
        "/C1P",
        "/C1M",
        "/C2P",
        "/C2M",
        "/VS_MINUS",
        "/VS_PLUS",
        "/DSR_RS232",
        "/DTR_RS232",
        "/RX_RS232",
        "/TX_RS232",
        "/TX_TTL",
        "/RX_TTL",
        "/DTR_TTL",
        "/DSR_TTL",
    ],
    "group3": [
        "/D0",
        "/D1",
        "/D2",
        "/D3",
        "/D4",
        "/D5",
        "/D6",
        "/D7",
    ],
    "group4": [
        "/~{A15}",
        "/~{A7}",
        "/~{PIOCS}",
        "/~{ROMCS}",
        "/~{RAMCS}",
        "/~{UARTCS}",
        "/A0",
        "/A1",
        "/A2",
    ],
    "group5": [
        "/~{MREQ}",
        "/~{IORQ}",
        "/A15",
        "/A7",
        "/~{RD}",
        "/~{WR}",
    ],
    "group6": [
        "/A3",
        "/A4",
        "/A5",
        "/A6",
        "/A8",
        "/A9",
        "/A10",
        "/A11",
        "/A12",
        "/A13",
        "/A14",
    ],
    "group7": [
        "/BAUDOUT",
        "/PA0",
        "/PA1",
        "/PA2",
        "/PA3",
        "/PA4",
        "/PA5",
        "/PA6",
        "/PA7",
        "/PB0",
        "/PB1",
        "/PB2",
        "/PB3",
        "/PB4",
        "/PB5",
        "/PB6",
        "/PB7",
        "/PC0",
        "/PC1",
        "/PC2",
        "/PC3",
        "/PC4",
        "/PC5",
        "/PC6",
        "/PC7",
    ],
}


def to_mm(val):
    return pcbnew.ToMM(val)


def from_mm(val):
    return pcbnew.FromMM(val)


def mark_via_obstacle(grid, grid_via, cx, cy):
    r_tr = VIA_RAD + CLEARANCE + TRACK_W / 2.0
    r_via = VIA_RAD + CLEARANCE + VIA_RAD
    x0 = max(0, int((cx - r_via) / STEP))
    x1 = min(NX - 1, int((cx + r_via) / STEP))
    y0 = max(0, int((cy - r_via) / STEP))
    y1 = min(NY - 1, int((cy + r_via) / STEP))

    r_tr2 = r_tr * r_tr
    r_via2 = r_via * r_via
    for iy in range(y0, y1 + 1):
        y = iy * STEP
        dy2 = (y - cy) * (y - cy)
        row = iy * NX
        for ix in range(x0, x1 + 1):
            x = ix * STEP
            d2 = (x - cx) * (x - cx) + dy2
            if d2 <= r_tr2:
                grid[0][row + ix] = 1
                grid[1][row + ix] = 1
            if d2 <= r_via2:
                grid_via[row + ix] = 1


def mark_track_obstacle(grid, grid_via, layer_idx, x0, y0, x1, y1):
    r_tr = TRACK_W / 2.0 + CLEARANCE + TRACK_W / 2.0
    r_via = TRACK_W / 2.0 + CLEARANCE + VIA_RAD

    dist = math.hypot(x1 - x0, y1 - y0)
    steps = max(1, int(dist / (STEP * 0.5)))
    for i in range(steps + 1):
        t = i / steps
        cx = x0 + (x1 - x0) * t
        cy = y0 + (y1 - y0) * t

        ix0 = max(0, int((cx - r_via) / STEP))
        ix1 = min(NX - 1, int((cx + r_via) / STEP))
        iy0 = max(0, int((cy - r_via) / STEP))
        iy1 = min(NY - 1, int((cy + r_via) / STEP))

        r_tr2 = r_tr * r_tr
        r_via2 = r_via * r_via
        for iy in range(iy0, iy1 + 1):
            y = iy * STEP
            dy2 = (y - cy) * (y - cy)
            row = iy * NX
            for ix in range(ix0, ix1 + 1):
                x = ix * STEP
                d2 = (x - cx) * (x - cx) + dy2
                if d2 <= r_tr2:
                    grid[layer_idx][row + ix] = 1
                if d2 <= r_via2:
                    grid_via[row + ix] = 1


def build_obstacle_grids(board):
    """Build separate pad and track obstacle grids."""
    pad_grid = [bytearray(STRIDE), bytearray(STRIDE)]
    pad_via_grid = bytearray(STRIDE)
    track_grid = [bytearray(STRIDE), bytearray(STRIDE)]
    track_via_grid = bytearray(STRIDE)

    # 1. Board edge clearance (0.6 mm)
    edge_cells = int(0.6 / STEP)
    for iy in range(NY):
        for ix in range(NX):
            if ix < edge_cells or ix >= NX - edge_cells or iy < edge_cells or iy >= NY - edge_cells:
                pad_grid[0][iy * NX + ix] = 1
                pad_grid[1][iy * NX + ix] = 1
                pad_via_grid[iy * NX + ix] = 1

    # 2. Pads
    for pad in board.GetPads():
        pos = pad.GetPosition()
        cx, cy = to_mm(pos.x), to_mm(pos.y)
        netname = pad.GetNetname()

        is_power = any(p in netname for p in ("+5V", "GND", "VCC"))
        clear = POWER_CLEARANCE if is_power else CLEARANCE
        pad_clear = clear + TRACK_W / 2.0
        via_clear = clear + VIA_RAD

        shape = pad.GetShape()
        sx = to_mm(pad.GetSize().x) / 2.0
        sy = to_mm(pad.GetSize().y) / 2.0
        is_rect = shape in (pcbnew.PAD_SHAPE_RECT, getattr(pcbnew, 'PAD_SHAPE_ROUNDRECT', 4), 1, 4)
        max_r = math.hypot(sx, sy)

        reach = max_r + via_clear
        x0 = max(0, int((cx - reach) / STEP))
        x1 = min(NX - 1, int((cx + reach) / STEP))
        y0 = max(0, int((cy - reach) / STEP))
        y1 = min(NY - 1, int((cy + reach) / STEP))

        for iy in range(y0, y1 + 1):
            y = iy * STEP
            row = iy * NX
            for ix in range(x0, x1 + 1):
                x = ix * STEP
                if is_rect:
                    dx = max(0.0, abs(x - cx) - sx)
                    dy = max(0.0, abs(y - cy) - sy)
                    dist = math.hypot(dx, dy)
                else:
                    dist = max(0.0, math.hypot(x - cx, y - cy) - max(sx, sy))

                if dist <= pad_clear:
                    pad_grid[0][row + ix] = 1
                    pad_grid[1][row + ix] = 1
                if dist <= via_clear:
                    pad_via_grid[row + ix] = 1

    # 3. Existing tracks and vias on the board
    for track in board.GetTracks():
        if track.Type() == pcbnew.PCB_VIA_T:
            pos = track.GetPosition()
            cx, cy = to_mm(pos.x), to_mm(pos.y)
            mark_via_obstacle(track_grid, track_via_grid, cx, cy)
        else:
            p1, p2 = track.GetStart(), track.GetEnd()
            layer_idx = 0 if track.GetLayer() == pcbnew.F_Cu else 1
            mark_track_obstacle(track_grid, track_via_grid, layer_idx, to_mm(p1.x), to_mm(p1.y), to_mm(p2.x), to_mm(p2.y))

    return pad_grid, pad_via_grid, track_grid, track_via_grid


def get_pad_escape_cells(coords):
    """Compute cells within pad radius that are allowed for this net to enter/leave."""
    escape_cells = set()
    for cx, cy in coords:
        r = 1.05
        x0 = max(0, int((cx - r) / STEP))
        x1 = min(NX - 1, int((cx + r) / STEP))
        y0 = max(0, int((cy - r) / STEP))
        y1 = min(NY - 1, int((cy + r) / STEP))
        for iy in range(y0, y1 + 1):
            row = iy * NX
            for ix in range(x0, x1 + 1):
                escape_cells.add(row + ix)
    return escape_cells


def astar_search(start_coords, goals, pad_grid, pad_via_grid, track_grid, track_via_grid, escape_cells):
    """A* search from start_coords to ANY state in goals set (x, y, layer)."""
    sx, sy = start_coords

    def h(x, y):
        best = 1e9
        for gx, gy, _ in goals:
            d = abs(x - gx) + abs(y - gy)
            if d < best:
                best = d
        return best * 1.001

    openq = []
    gscore = [1e9] * (2 * STRIDE)

    # Start allowed on either F.Cu (0) or B.Cu (1) since all pads are PTH
    for sl in (0, 1):
        s_idx = sy * NX + sx
        if track_grid[sl][s_idx] == 0:
            gscore[sl * STRIDE + s_idx] = 0.0
            heapq.heappush(openq, (h(sx, sy), 0.0, (sx, sy, sl)))

    came = {}
    found = None

    while openq:
        _, g, cur = heapq.heappop(openq)
        x, y, layer = cur
        idx = layer * STRIDE + y * NX + x

        if (x, y, layer) in goals:
            found = cur
            break

        if g > gscore[idx]:
            continue

        # 4 orthogonal neighbors
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx_x = x + dx
            nx_y = y + dy
            if 0 <= nx_x < NX and 0 <= nx_y < NY:
                cell_idx = nx_y * NX + nx_x
                if track_grid[layer][cell_idx] == 0 and (pad_grid[layer][cell_idx] == 0 or cell_idx in escape_cells):
                    n_idx = layer * STRIDE + cell_idx
                    # F.Cu (0) prefers horizontal, B.Cu (1) prefers vertical
                    cost = 1.0 if (layer == 0 and dy == 0) or (layer == 1 and dx == 0) else 2.5
                    ng = g + cost
                    if ng < gscore[n_idx]:
                        gscore[n_idx] = ng
                        came[(nx_x, nx_y, layer)] = cur
                        heapq.heappush(openq, (ng + h(nx_x, nx_y), ng, (nx_x, nx_y, layer)))

        # Layer transition via
        cell_idx = y * NX + x
        if track_via_grid[cell_idx] == 0 and pad_via_grid[cell_idx] == 0:
            other_layer = 1 - layer
            o_idx = other_layer * STRIDE + cell_idx
            ng = g + 15.0  # Via penalty
            if ng < gscore[o_idx]:
                gscore[o_idx] = ng
                came[(x, y, other_layer)] = cur
                heapq.heappush(openq, (ng + h(x, y), ng, (x, y, other_layer)))

    if not found:
        return None

    path = []
    c = found
    while c in came:
        path.append(c)
        c = came[c]
    path.append(c)
    path.reverse()
    return path


def add_path_to_board(board, net, path, start_coord, end_coord):
    """Simplify path and add tracks & vias to KiCad board."""
    real_path = [(start_coord[0], start_coord[1], path[0][2])]
    for pt in path[1:-1]:
        real_path.append((pt[0] * STEP, pt[1] * STEP, pt[2]))
    real_path.append((end_coord[0], end_coord[1], path[-1][2]))

    layers = (pcbnew.F_Cu, pcbnew.B_Cu)
    run_layer = real_path[0][2]
    run_x, run_y = real_path[0][0], real_path[0][1]
    prev = real_path[0]

    added_items = []

    for pt in real_path[1:]:
        if pt[2] != prev[2]:
            # Finish track on previous layer to via coordinate
            if not (math.isclose(run_x, prev[0], abs_tol=1e-5) and math.isclose(run_y, prev[1], abs_tol=1e-5)):
                tr = pcbnew.PCB_TRACK(board)
                tr.SetStart(pcbnew.VECTOR2I(from_mm(run_x), from_mm(run_y)))
                tr.SetEnd(pcbnew.VECTOR2I(from_mm(prev[0]), from_mm(prev[1])))
                tr.SetWidth(from_mm(TRACK_W))
                tr.SetLayer(layers[run_layer])
                tr.SetNet(net)
                board.Add(tr)
                added_items.append(('track', run_layer, run_x, run_y, prev[0], prev[1]))

            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(from_mm(prev[0]), from_mm(prev[1])))
            via.SetWidth(from_mm(VIA_DIA))
            via.SetDrill(from_mm(VIA_DRILL))
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            via.SetNet(net)
            board.Add(via)
            added_items.append(('via', prev[0], prev[1]))

            run_layer = pt[2]
            run_x, run_y = prev[0], prev[1]
        elif not math.isclose((pt[0] - prev[0]) * (prev[1] - run_y), (pt[1] - prev[1]) * (prev[0] - run_x), abs_tol=1e-5):
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(pcbnew.VECTOR2I(from_mm(run_x), from_mm(run_y)))
            tr.SetEnd(pcbnew.VECTOR2I(from_mm(prev[0]), from_mm(prev[1])))
            tr.SetWidth(from_mm(TRACK_W))
            tr.SetLayer(layers[run_layer])
            tr.SetNet(net)
            board.Add(tr)
            added_items.append(('track', run_layer, run_x, run_y, prev[0], prev[1]))
            run_x, run_y = prev[0], prev[1]
        prev = pt

    if not (math.isclose(run_x, prev[0], abs_tol=1e-5) and math.isclose(run_y, prev[1], abs_tol=1e-5)):
        tr = pcbnew.PCB_TRACK(board)
        tr.SetStart(pcbnew.VECTOR2I(from_mm(run_x), from_mm(run_y)))
        tr.SetEnd(pcbnew.VECTOR2I(from_mm(prev[0]), from_mm(prev[1])))
        tr.SetWidth(from_mm(TRACK_W))
        tr.SetLayer(layers[run_layer])
        tr.SetNet(net)
        board.Add(tr)
        added_items.append(('track', run_layer, run_x, run_y, prev[0], prev[1]))

    return added_items


def route_net(board, netname, pad_grid, pad_via_grid, track_grid, track_via_grid):
    """Route all pads belonging to netname using progressive Steiner tree."""
    pads = [p for p in board.GetPads() if p.GetNetname() == netname]
    if len(pads) < 2:
        print(f"Skipping {netname}: has {len(pads)} pads.")
        return True

    net = board.FindNet(netname)
    coords = []
    seen = set()
    for p in pads:
        pos = p.GetPosition()
        c = (round(to_mm(pos.x), 3), round(to_mm(pos.y), 3))
        if c not in seen:
            seen.add(c)
            coords.append(c)

    coords.sort(key=lambda c: c[0])
    print(f"Routing {netname} ({len(coords)} unique pads)...")

    # Escape cells only around this net's own pads
    escape_cells = get_pad_escape_cells(coords)

    # Initial target: Pad 1 on either layer (0 or 1)
    p1_gx = int(round(coords[1][0] / STEP))
    p1_gy = int(round(coords[1][1] / STEP))
    goal_set = {(p1_gx, p1_gy, 0), (p1_gx, p1_gy, 1)}

    start_grid = (int(round(coords[0][0] / STEP)), int(round(coords[0][1] / STEP)))

    path = astar_search(start_grid, goal_set, pad_grid, pad_via_grid, track_grid, track_via_grid, escape_cells)
    if not path:
        print(f"  FAILED to connect initial pair for {netname}")
        return False

    added = add_path_to_board(board, net, path, coords[0], coords[1])

    tree_points = set()
    for pt in path:
        tree_points.add((pt[0], pt[1], pt[2]))

    # For any vias added, both layers at that coordinate are in tree_points
    for item in added:
        if item[0] == 'via':
            vx = int(round(item[1] / STEP))
            vy = int(round(item[2] / STEP))
            tree_points.add((vx, vy, 0))
            tree_points.add((vx, vy, 1))

    # Connect remaining pads to the growing tree
    for pad_coord in coords[2:]:
        start_pt = (int(round(pad_coord[0] / STEP)), int(round(pad_coord[1] / STEP)))
        sub_path = astar_search(start_pt, tree_points, pad_grid, pad_via_grid, track_grid, track_via_grid, escape_cells)
        if not sub_path:
            print(f"  FAILED to connect pad {pad_coord} for {netname}")
            return False

        end_grid = (sub_path[-1][0], sub_path[-1][1])
        end_coord = (end_grid[0] * STEP, end_grid[1] * STEP)

        sub_added = add_path_to_board(board, net, sub_path, pad_coord, end_coord)
        added.extend(sub_added)

        for pt in sub_path:
            tree_points.add((pt[0], pt[1], pt[2]))
        for item in sub_added:
            if item[0] == 'via':
                vx = int(round(item[1] / STEP))
                vy = int(round(item[2] / STEP))
                tree_points.add((vx, vy, 0))
                tree_points.add((vx, vy, 1))

    # Protect the newly added tracks and vias in track_grid
    for item in added:
        if item[0] == 'track':
            _, layer_idx, x0, y0, x1, y1 = item
            mark_track_obstacle(track_grid, track_via_grid, layer_idx, x0, y0, x1, y1)
        elif item[0] == 'via':
            _, vx, vy = item
            mark_via_obstacle(track_grid, track_via_grid, vx, vy)

    print(f"  Successfully routed {netname}!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Manhattan Router for Z80 Simple Computer")
    parser.add_argument("--group", default="group1", choices=list(NET_GROUPS.keys()), help="Net group to route")
    parser.add_argument("--board", default=str(DEFAULT_BOARD), help="Path to board file")
    parser.add_argument("--out", default=str(DEFAULT_BOARD), help="Path to save routed board")
    parser.add_argument("--drc", action="store_true", default=True, help="Run DRC after routing")
    args = parser.parse_args()

    board_path = Path(args.board)
    out_path = Path(args.out)

    print(f"Loading board: {board_path}")
    board = pcbnew.LoadBoard(str(board_path))

    target_nets = NET_GROUPS[args.group]
    print(f"Routing group '{args.group}': {target_nets}")

    # Build obstacle grids
    pad_grid, pad_via_grid, track_grid, track_via_grid = build_obstacle_grids(board)

    success_count = 0
    for netname in target_nets:
        ok = route_net(board, netname, pad_grid, pad_via_grid, track_grid, track_via_grid)
        if ok:
            success_count += 1
        else:
            print(f"Routing stopped due to failure on {netname}")
            break

    print(f"\nRefilling inner copper zones (GND on In1.Cu, +5V on In2.Cu)...")
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())

    print(f"Saving board to: {out_path}")
    board.Save(str(out_path))

    track_count = len(list(board.GetTracks()))
    print(f"Board saved. Total tracks/vias: {track_count}")

    if args.drc:
        print("\nRunning KiCad DRC check...")
        cmd = [
            "kicad-cli", "pcb", "drc",
            "--schematic-parity",
            "-o", str(DEFAULT_OUT_REPORT),
            str(out_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(res.stdout)
        if res.stderr:
            print(res.stderr)


if __name__ == "__main__":
    main()
