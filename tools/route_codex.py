#!/usr/bin/env python3
"""Variant 2: new direction-aware octilinear planner with native KiCad gates.

The C++ planner is Codex work. Obstacle geometry, net groups, copper emission
and copper-length metrics are shared with the attributed variant-1 adapter
in route_netgroup.py. The candidate/DRC/promotion workflow follows variant 1.
No routed copper from variant 1 is imported.
"""
import argparse
import ctypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pcbnew as pcb
import route_netgroup as shared

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / 'layouts/02-codex-router'
BOARD = LAYOUT / 'Z80_Simple_Computer.kicad_pcb'
REPORT = ROOT / 'reports/routing/02-codex-router'
SOURCE = ROOT / 'tools/octilinear_router.cpp'
STRIDE, NX, NY, STEP = shared.STRIDE, shared.NX, shared.NY, shared.STEP


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def buffer(value):
    return ctypes.addressof((ctypes.c_uint8 * len(value)).from_buffer(value))


class Planner:
    def __init__(self):
        binary = REPORT / 'cache/octilinear_router.so'
        binary.parent.mkdir(parents=True, exist_ok=True)
        if not binary.exists() or binary.stat().st_mtime < SOURCE.stat().st_mtime:
            subprocess.run(['g++', '-std=c++17', '-O3', '-shared', '-fPIC',
                            str(SOURCE), '-o', str(binary)], check=True)
        self.lib = ctypes.CDLL(str(binary))
        self.lib.find_octilinear_route.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_int]
        self.lib.find_octilinear_route.restype = ctypes.c_int
        self.lib.octile_distances.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_int, ctypes.c_int]
        self.lib.octile_distances.restype = None

    def search(self, start, goals, blocked, no_via, via_cost, bend_cost):
        capacity = 100000
        output = (ctypes.c_uint32 * capacity)()
        expanded = ctypes.c_uint64()
        n = self.lib.find_octilinear_route(
            NX, NY, *start, buffer(blocked[0]), buffer(blocked[1]),
            buffer(no_via), buffer(goals), via_cost, bend_cost,
            output, capacity, ctypes.byref(expanded), 5000000)
        if n < 0:
            raise RuntimeError(f'Planner status {n}; expanded {expanded.value} states')
        path = []
        for state in output[:n]:
            layer, cell = divmod(state, STRIDE)
            y, x = divmod(cell, NX)
            path.append((x, y, layer))
        return path, expanded.value


def grid(coord):
    return tuple(int(round(x / STEP)) for x in coord)


def route_net(board, name, tracks, track_vias, planner, args):
    started = time.monotonic()
    coords = sorted({(round(pcb.ToMM(p.GetPosition().x), 3),
                      round(pcb.ToMM(p.GetPosition().y), 3))
                     for p in board.GetPads() if p.GetNetname() == name})
    if len(coords) < 2:
        raise ValueError(f'Expected multi-pad signal net: {name}')
    pad_masks, pad_vias, _, _ = shared.build_obstacle_grids(
        board, current_net=name, include_tracks=False)
    blocked = [bytearray(a | b for a, b in zip(pad_masks[layer], tracks[layer]))
               for layer in (0, 1)]
    no_via = bytearray(a | b for a, b in zip(pad_vias, track_vias))
    goals = bytearray(2 * STRIDE)

    # Deterministic closest seed pair; subsequent pads grow into the net tree.
    _, a, b = min((math.dist(coords[a], coords[b]), a, b)
                  for a in range(len(coords)) for b in range(a + 1, len(coords)))
    connected = [coords[b]]
    remaining = [coords[a]] + [c for i, c in enumerate(coords) if i not in (a, b)]
    gx, gy = grid(coords[b])
    goals[gy * NX + gx] = goals[STRIDE + gy * NX + gx] = 1
    added, expanded = [], 0
    for number in range(len(remaining)):
        if number:
            remaining.sort(key=lambda c: (min(math.dist(c, v) for v in connected), c))
        coord = remaining.pop(0)
        path, states = planner.search(grid(coord), goals, blocked, no_via,
                                      args.via_cost, args.bend_cost)
        expanded += states
        end = (path[-1][0] * STEP, path[-1][1] * STEP)
        items = shared.add_path_to_board(board, board.FindNet(name), path, coord, end)
        added.extend(items)
        for x, y, layer in path:
            goals[layer * STRIDE + y * NX + x] = 1
        gx, gy = grid(coord)
        goals[gy * NX + gx] = goals[STRIDE + gy * NX + gx] = 1
        connected.append(coord)
    for item in added:
        if item[0] == 'track':
            shared.mark_track_obstacle(tracks, track_vias, *item[1:])
        else:
            shared.mark_via_obstacle(tracks, track_vias, *item[1:])
    result = dict(net=name, pads=len(coords), expanded_states=expanded,
                  seconds=round(time.monotonic() - started, 3))
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=[*shared.NET_GROUPS, 'all'], default='group1')
    parser.add_argument('--via-cost', type=int, default=3000)
    parser.add_argument('--bend-cost', type=int, default=180)
    parser.add_argument('--priority-net', action='append', default=[],
                        help='Route this net first within its group; repeat for a recorded priority order')
    args = parser.parse_args()
    assert args.via_cost > 0 and args.bend_cost >= 0
    manifest_path = LAYOUT / 'experiment.json'
    manifest = json.loads(manifest_path.read_text())
    completed = manifest.get('routing', {}).get('completed_groups', [])
    groups = list(shared.NET_GROUPS) if args.group == 'all' else [args.group]
    assert all(n in [v for g in groups for v in shared.NET_GROUPS[g]] for n in args.priority_net)
    planner = None
    for group in groups:
        if group in completed:
            print(f'{group} already validated; skipping.', flush=True)
            continue
        if list(LAYOUT.glob('*.lck')):
            raise SystemExit('Close the KiCad variant-2 layout before routing.')
        previous = manifest.get('routing', {}).get('board_sha256', manifest['placement']['board_sha256'])
        assert digest(BOARD) == previous, 'Working PCB changed; preserve/review it first'
        schematic = BOARD.with_suffix('.kicad_sch')
        assert digest(schematic) == manifest['schematic_snapshot']['sha256']
        assert digest(ROOT / 'design' / schematic.name) == digest(schematic)
        assert digest(ROOT / 'layouts/01-reused-agy-router' / BOARD.name) == manifest['preserved_variant1_sha256']
        checkpoint = ROOT / 'checkpoints/01-codex-placed-unrouted'
        checkpoint_meta = json.loads((checkpoint / 'checkpoint.json').read_text())
        assert digest(BOARD.with_suffix('.kicad_pro')) == checkpoint_meta['files_sha256']['Z80_Simple_Computer.kicad_pro']
        configuration = dict(via_cost=args.via_cost, bend_cost=args.bend_cost,
                             straight_cost=100, diagonal_cost=146,
                             nonpreferred_axis_extra_cost=25, grid_mm=STEP)
        if completed:
            assert configuration == manifest['routing']['configuration'], 'Changing route costs mid-run is a separate experiment'
        started = time.monotonic()
        group_dir = REPORT / group
        group_dir.mkdir(parents=True, exist_ok=True)
        attempt = 1
        while (group_dir / f'attempt-{attempt:02d}').exists():
            attempt += 1
        stage = group_dir / f'attempt-{attempt:02d}'
        stage.mkdir()
        candidate = stage / BOARD.name
        for suffix in ('.kicad_sch', '.kicad_pro'):
            shutil.copy2(BOARD.with_suffix(suffix), candidate.with_suffix(suffix))
        for name in ('fp-lib-table', 'sym-lib-table'):
            shutil.copy2(LAYOUT / name, stage / name)
        (stage / 'libs').symlink_to(os.path.relpath(ROOT / 'libs', stage), target_is_directory=True)
        if planner is None:
            planner = Planner()
        print(f'Starting {group}, attempt {attempt}', flush=True)
        board = pcb.LoadBoard(str(BOARD))
        _, _, tracks, track_vias = shared.build_obstacle_grids(board)
        results, failure = [], None
        order = sorted(shared.NET_GROUPS[group],
                       key=lambda n: (args.priority_net.index(n) if n in args.priority_net
                                      else len(args.priority_net) + shared.NET_GROUPS[group].index(n)))
        try:
            for name in order:
                assert not any(t.GetNetname() == name for t in board.GetTracks()), 'Do not reroute an existing net without a new input'
                results.append(route_net(board, name, tracks, track_vias, planner, args))
        except (RuntimeError, ValueError) as error:
            failure = f'{name}: {error}'
        print('Filling zones and checking native copper...', flush=True)
        pcb.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(str(candidate))
        drc_path = stage / 'drc.json'
        run = subprocess.run(['kicad-cli', 'pcb', 'drc', '--schematic-parity',
                              '--all-track-errors', '--format', 'json',
                              '-o', str(drc_path), str(candidate)], capture_output=True, text=True)
        (stage / 'drc-command.log').write_text(run.stdout + run.stderr)
        assert run.returncode == 0 and drc_path.exists(), 'DRC did not run successfully'
        drc = json.loads(drc_path.read_text())
        residual = [f for f in drc['unconnected_items']
                    if any(f'[{name}]' in item['description'] for name in shared.NET_GROUPS[group]
                           for item in f['items'])]
        accepted = not failure and not drc['violations'] and not drc['schematic_parity'] and not residual
        summary = dict(status='validated' if accepted else 'rejected', group=group,
                       attempt=attempt, candidate=str(candidate.relative_to(ROOT)),
                       utc=datetime.now(timezone.utc).isoformat(), failure=failure,
                       runtime_seconds=round(time.monotonic() - started, 2),
                       input_board_sha256=previous, output_board_sha256=digest(candidate),
                       schematic_sha256=digest(schematic), settings_sha256=digest(candidate.with_suffix('.kicad_pro')),
                       planner_sha256=digest(SOURCE), wrapper_sha256=digest(Path(__file__)),
                       shared_geometry_and_writer_sha256=digest(Path(shared.__file__)),
                       configuration=configuration, route_order=order, net_results=results,
                       violations=len(drc['violations']), parity_issues=len(drc['schematic_parity']),
                       target_net_unconnected_items=len(residual),
                       remaining_unconnected_items=len(drc['unconnected_items']), **shared.metrics(board))
        (stage / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps({k:v for k,v in summary.items() if k not in ('net_results',)}, indent=2), flush=True)
        if not accepted:
            raise SystemExit(f'Candidate rejected; working board preserved. Inspect {stage}')
        assert digest(BOARD) == previous and not list(LAYOUT.glob('*.lck')), 'Working PCB changed during planning'
        shutil.copy2(candidate, BOARD)
        shutil.copy2(stage / 'summary.json', group_dir / 'summary.json')
        completed.append(group)
        manifest['routing'] = dict(completed_groups=completed, board_sha256=digest(BOARD),
                                   configuration=configuration, report=str(stage.relative_to(ROOT)),
                                   **shared.metrics(board))
        manifest['status'] = 'routing-validated'
        manifest['board_status'] = 'routed' if not drc['unconnected_items'] else 'partially-routed'
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        print(f'{group} promoted to variant 2.', flush=True)


if __name__ == '__main__':
    main()
