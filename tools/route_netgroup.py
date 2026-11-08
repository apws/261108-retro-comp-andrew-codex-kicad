#!/usr/bin/env python3
"""Attributed AGY Manhattan router, adapted for the fresh Codex placement.

Source: reference/grok-agy/tools/route_netgroup.py (2026-11-06).
Retains the 0.1 mm two-layer A*, costs, group order, growing net tree and
track/via emission. Codex changes: rotated conservative pad geometry,
net-aware pad access, drilling protection, exact fast heuristic lookup,
physical pad layer joins, guards and native-DRC-gated group promotion.

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
import ctypes
import hashlib
import json
import shutil
import time
from array import array
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import pcbnew

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "layouts/01-reused-agy-router"
DEFAULT_BOARD = LAYOUT / "Z80_Simple_Computer.kicad_pcb"
REPORT = ROOT / "reports/routing/01-reused-agy-router"
MARGIN = 0.02  # Extra raster/segment tolerance; never reduces project rules.

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
    r_tr = VIA_RAD + CLEARANCE + TRACK_W / 2.0 + MARGIN
    r_via = VIA_RAD + CLEARANCE + VIA_RAD + MARGIN
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
    r_tr = TRACK_W / 2.0 + CLEARANCE + TRACK_W / 2.0 + MARGIN
    r_via = TRACK_W / 2.0 + CLEARANCE + VIA_RAD + MARGIN

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


def build_obstacle_grids(board, current_net=None, include_tracks=True):
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
        pad_clear = clear + TRACK_W / 2.0 + MARGIN
        via_clear = clear + VIA_RAD + MARGIN

        shape = pad.GetShape()
        sx = to_mm(pad.GetSize().x) / 2.0
        sy = to_mm(pad.GetSize().y) / 2.0
        is_rect = shape in (pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_ROUNDRECT)
        if shape not in (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_OVAL,
                         pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_ROUNDRECT):
            raise ValueError(f"Unsupported pad shape: {shape}")
        # KiCad pad size is local; orientation includes footprint rotation.
        angle = math.radians(pad.GetOrientationDegrees())
        ca, sa = math.cos(angle), math.sin(angle)
        drill = pad.GetDrillSize()
        hole = max(to_mm(drill.x), to_mm(drill.y)) / 2
        max_r = max(math.hypot(sx, sy), hole)

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
                lx = ca * (x - cx) - sa * (y - cy)
                ly = sa * (x - cx) + ca * (y - cy)
                if is_rect:
                    # The full oriented rectangle conservatively encloses
                    # rounded corners instead of underestimating pad 1.
                    dx = max(0.0, abs(lx) - sx)
                    dy = max(0.0, abs(ly) - sy)
                    dist = math.hypot(dx, dy)
                elif shape == pcbnew.PAD_SHAPE_OVAL:
                    radius = min(sx, sy)
                    dx = max(0.0, abs(lx) - max(0.0, sx - radius))
                    dy = max(0.0, abs(ly) - max(0.0, sy - radius))
                    dist = max(0.0, math.hypot(dx, dy) - radius)
                else:
                    dist = max(0.0, math.hypot(x - cx, y - cy) - max(sx, sy))

                own_pad = current_net is not None and netname == current_net
                if not own_pad and dist <= pad_clear:
                    pad_grid[0][row + ix] = 1
                    pad_grid[1][row + ix] = 1
                # Do not place a via next to a component/NPTH drill even
                # when the nearby pad belongs to the currently routed net.
                hole_dist = math.hypot(x - cx, y - cy)
                if dist <= via_clear or hole_dist <= hole + VIA_DRILL / 2 + 0.20 + MARGIN:
                    pad_via_grid[row + ix] = 1

    # 3. Existing tracks and vias on the board
    for track in board.GetTracks():
        if not include_tracks:
            break
        if track.Type() == pcbnew.PCB_VIA_T:
            pos = track.GetPosition()
            cx, cy = to_mm(pos.x), to_mm(pos.y)
            mark_via_obstacle(track_grid, track_via_grid, cx, cy)
        else:
            p1, p2 = track.GetStart(), track.GetEnd()
            layer_idx = 0 if track.GetLayer() == pcbnew.F_Cu else 1
            mark_track_obstacle(track_grid, track_via_grid, layer_idx, to_mm(p1.x), to_mm(p1.y), to_mm(p2.x), to_mm(p2.y))

    return pad_grid, pad_via_grid, track_grid, track_via_grid


_distance_library = None


def goal_distances(goals):
    """Exact AGY Manhattan heuristic, computed once per branch in native code."""
    global _distance_library
    if _distance_library is None:
        source = ROOT / 'tools/manhattan_distance.cpp'
        binary = REPORT / 'cache/manhattan_distance.so'
        binary.parent.mkdir(parents=True, exist_ok=True)
        if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
            subprocess.run(['g++', '-O3', '-shared', '-fPIC', str(source), '-o', str(binary)], check=True)
        _distance_library = ctypes.CDLL(str(binary))
        _distance_library.manhattan_distance.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                        ctypes.c_int, ctypes.c_int]
        _distance_library.manhattan_distance.restype = None
    mask = bytearray(STRIDE)
    for x, y, _ in goals:
        mask[y * NX + x] = 1
    distances = array('H', [30000]) * STRIDE
    _distance_library.manhattan_distance(
        ctypes.addressof((ctypes.c_uint8 * STRIDE).from_buffer(mask)),
        distances.buffer_info()[0], NX, NY)
    return distances


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

    distances = goal_distances(goals)

    def h(x, y):
        return distances[y * NX + x] * 1.001

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
            via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
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

    # Rebuild only the pad masks, excluding this net from track obstacles.
    # No arbitrary escape square may open a hole through a foreign pad.
    pad_grid, pad_via_grid, _, _ = build_obstacle_grids(
        board, current_net=netname, include_tracks=False)
    escape_cells = set()

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
    for cx, cy in coords[:2]:
        gx, gy = int(round(cx / STEP)), int(round(cy / STEP))
        tree_points.update(((gx, gy, 0), (gx, gy, 1)))

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
        gx, gy = start_pt
        tree_points.update(((gx, gy, 0), (gx, gy, 1)))
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
    parser = argparse.ArgumentParser(description="Adapted AGY router; each group is gated by native DRC")
    parser.add_argument("--group", default="group1", choices=[*NET_GROUPS, 'all'])
    args = parser.parse_args()
    metadata_path = LAYOUT / 'experiment.json'
    metadata = json.loads(metadata_path.read_text())
    groups = list(NET_GROUPS) if args.group == 'all' else [args.group]
    completed = metadata.get('routing', {}).get('completed_groups', [])
    for group in groups:
        if group in completed:
            print(f'{group} already validated; skipping.', flush=True)
            continue
        if list(LAYOUT.glob('*.lck')):
            raise SystemExit('Close the active KiCad layout before routing.')
        expected = metadata.get('routing', {}).get('board_sha256',
                    metadata['placement']['board_sha256'])
        if sha256(DEFAULT_BOARD) != expected:
            raise SystemExit('Working board changed since the saved milestone; preserve/review it first.')
        schematic = DEFAULT_BOARD.with_suffix('.kicad_sch')
        if sha256(schematic) != metadata['schematic_snapshot']['sha256']:
            raise SystemExit('Schematic snapshot changed; synchronize and review the board first.')
        if sha256(ROOT / 'design/Z80_Simple_Computer.kicad_sch') != sha256(schematic):
            raise SystemExit('Authoritative circuit and layout snapshot differ.')

        started = time.monotonic()
        stage = REPORT / group
        stage.mkdir(parents=True, exist_ok=True)
        candidate = stage / DEFAULT_BOARD.name
        for suffix in ('.kicad_sch', '.kicad_pro'):
            shutil.copy2(DEFAULT_BOARD.with_suffix(suffix), candidate.with_suffix(suffix))
        for name in ('fp-lib-table', 'sym-lib-table'):
            shutil.copy2(LAYOUT / name, stage / name)
        if not (stage / 'libs').exists():
            (stage / 'libs').symlink_to(Path(shutil.os.path.relpath(ROOT / 'libs', stage)), target_is_directory=True)

        print(f'Loading {group} from {DEFAULT_BOARD}', flush=True)
        board = pcbnew.LoadBoard(str(DEFAULT_BOARD))
        pads, pad_vias, tracks, track_vias = build_obstacle_grids(board)
        target_nets = NET_GROUPS[group]
        routed = []
        for name in target_nets:
            if board.FindNet(name) is None:
                raise SystemExit(f'Missing expected net: {name}')
            if any(item.GetNetname() == name for item in board.GetTracks()):
                raise SystemExit(f'{name} already has copper; do not duplicate a routing pass.')
            if not route_net(board, name, pads, pad_vias, tracks, track_vias):
                board.Save(str(candidate))
                raise SystemExit(f'Failed {group}/{name}; working PCB was not overwritten.')
            routed.append(name)
        print('Refilling power planes and saving candidate...', flush=True)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(str(candidate))
        drc_path = stage / 'drc.json'
        result = subprocess.run(['kicad-cli', 'pcb', 'drc', '--schematic-parity',
                                 '--all-track-errors', '--format', 'json',
                                 '-o', str(drc_path), str(candidate)], capture_output=True, text=True)
        (stage / 'drc-command.log').write_text(result.stdout + result.stderr)
        if result.returncode or not drc_path.exists():
            raise SystemExit(f'Native DRC failed to run; see {stage}. Working PCB preserved.')
        drc = json.loads(drc_path.read_text())
        residual = [finding for finding in drc['unconnected_items']
                    if any(f'[{name}]' in item['description'] for name in target_nets
                           for item in finding['items'])]
        accepted = not drc['violations'] and not drc['schematic_parity'] and not residual
        stats = metrics(board)
        summary = dict(status='validated' if accepted else 'rejected', group=group,
                       utc=datetime.now(timezone.utc).isoformat(),
                       runtime_seconds=round(time.monotonic() - started, 2),
                       input_board_sha256=expected, output_board_sha256=sha256(candidate),
                       schematic_sha256=sha256(schematic),
                       settings_sha256=sha256(candidate.with_suffix('.kicad_pro')),
                       router_sha256=sha256(Path(__file__)),
                       distance_helper_sha256=sha256(ROOT / 'tools/manhattan_distance.cpp'),
                       routed_nets=routed, violations=len(drc['violations']),
                       parity_issues=len(drc['schematic_parity']),
                       target_net_unconnected_items=len(residual),
                       remaining_unconnected_items=len(drc['unconnected_items']), **stats)
        (stage / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2), flush=True)
        if not accepted:
            raise SystemExit(f'Candidate rejected by native DRC; see {drc_path}. Working PCB preserved.')
        if sha256(DEFAULT_BOARD) != expected or list(LAYOUT.glob('*.lck')):
            raise SystemExit('Working project changed during routing; candidate preserved without promotion.')
        shutil.copy2(candidate, DEFAULT_BOARD)
        completed.append(group)
        metadata['routing'] = dict(completed_groups=completed, board_sha256=sha256(DEFAULT_BOARD),
                                   report=str(stage.relative_to(ROOT)), **stats)
        metadata['board_status'] = 'routed' if not drc['unconnected_items'] else 'partially-routed'
        metadata['status'] = 'routing-validated'
        metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
        print(f'{group} promoted to working layout.', flush=True)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(board):
    vias, segments, length = 0, 0, 0.0
    layers = defaultdict(float)
    for item in board.GetTracks():
        if item.Type() == pcbnew.PCB_VIA_T:
            vias += 1
        else:
            segments += 1
            a, b = item.GetStart(), item.GetEnd()
            mm_length = math.hypot(to_mm(a.x - b.x), to_mm(a.y - b.y))
            length += mm_length
            layers[board.GetLayerName(item.GetLayer())] += mm_length
    return dict(track_segments=segments, vias=vias, track_length_mm=round(length, 3),
                layer_lengths_mm={k: round(v, 3) for k, v in layers.items()})


if __name__ == "__main__":
    main()
