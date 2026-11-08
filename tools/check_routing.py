#!/usr/bin/env python3
"""Verify routed copper, unchanged circuit/placement/rules and preserved input.

Run after a fresh native KiCad DRC writes reports/routing/final-drc.json.
This verifies native output, not the router's internal path representation.
"""
import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pcbnew as pcb
from route_netgroup import metrics, NET_GROUPS

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / 'layouts/01-reused-agy-router'
CHECKPOINT = ROOT / 'checkpoints/01-codex-placed-unrouted'
REPORT = ROOT / 'reports/routing'
NAME = 'Z80_Simple_Computer'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def footprint_geometry(board):
    return {fp.GetReference(): (
        fp.GetPosition().x, fp.GetPosition().y, fp.GetOrientationDegrees(),
        fp.GetValue(), fp.GetFPID().GetUniStringLibId(),
        sorted((p.GetNumber(), p.GetPosition().x, p.GetPosition().y,
                p.GetSize().x, p.GetSize().y, p.GetDrillSize().x,
                p.GetDrillSize().y, p.GetOrientationDegrees(), p.GetNetname())
               for p in fp.Pads())) for fp in board.GetFootprints()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', choices=['01-reused-agy-router', '02-codex-router'],
                        default='01-reused-agy-router')
    args = parser.parse_args()
    layout = ROOT / 'layouts' / args.layout
    report_dir = REPORT if args.layout == '01-reused-agy-router' else REPORT / args.layout
    record = json.loads((CHECKPOINT / 'checkpoint.json').read_text())
    for name, expected in record['files_sha256'].items():
        assert digest(CHECKPOINT / name) == expected, f'Checkpoint modified: {name}'
    board_path = layout / f'{NAME}.kicad_pcb'
    board = pcb.LoadBoard(str(board_path))
    original = pcb.LoadBoard(str(CHECKPOINT / board_path.name))
    assert footprint_geometry(board) == footprint_geometry(original), 'Placement/footprints changed'
    assert digest(layout / f'{NAME}.kicad_pro') == record['files_sha256'][f'{NAME}.kicad_pro']
    assert digest(layout / f'{NAME}.kicad_sch') == record['files_sha256'][f'{NAME}.kicad_sch']
    assert digest(ROOT / f'design/{NAME}.kicad_sch') == record['files_sha256'][f'{NAME}.kicad_sch']
    xml = ET.parse(ROOT / f'output/schematic/{NAME}.xml')
    expected = {(n.attrib['ref'], n.attrib['pin']): net.attrib['name']
                for net in xml.findall('.//nets/net') for n in net.findall('node')}
    actual = {(fp.GetReference(), p.GetNumber()): p.GetNetname()
              for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname()}
    assert actual == expected, 'Native pads differ from exported schematic netlist'
    assert board.GetCopperLayerCount() == 4
    def outline(b):
        return sorted((x.GetStart().x, x.GetStart().y, x.GetEnd().x, x.GetEnd().y)
                      for x in b.GetDrawings() if x.GetLayer() == pcb.Edge_Cuts)
    assert outline(board) == outline(original), 'Board outline changed'
    zones = {(z.GetLayer(), z.GetNetname()) for z in board.Zones()}
    assert zones == {(pcb.In1_Cu, 'GND'), (pcb.In2_Cu, '+5V')}
    for z in board.Zones():
        assert z.GetFilledPolysList(z.GetLayer()).OutlineCount() > 0
    for item in board.GetTracks():
        assert item.GetNetname() not in ('', '+5V', 'GND')
        if item.Type() == pcb.PCB_VIA_T:
            assert all(item.GetWidth(layer) == pcb.FromMM(.70)
                       for layer in (pcb.F_Cu, pcb.In1_Cu, pcb.In2_Cu, pcb.B_Cu))
            assert item.GetDrillValue() == pcb.FromMM(.30)
            assert item.TopLayer() == pcb.F_Cu and item.BottomLayer() == pcb.B_Cu
        else:
            assert item.GetWidth() == pcb.FromMM(.15)
            assert item.GetLayer() in (pcb.F_Cu, pcb.B_Cu)
            assert item.GetStart() != item.GetEnd()
    drc = json.loads((report_dir / 'final-drc.json').read_text())
    assert not drc['violations'] and not drc['unconnected_items'] and not drc['schematic_parity']
    erc = json.loads((report_dir / 'final-erc.json').read_text())
    assert not [v for sheet in erc['sheets'] for v in sheet['violations']]
    summaries = []
    for group in NET_GROUPS:
        path = REPORT / args.layout / group / 'summary.json'
        summary = json.loads(path.read_text())
        assert summary['status'] == 'validated'
        summaries.append(summary)
    assert summaries[-1]['output_board_sha256'] == digest(board_path)
    reference = pcb.LoadBoard(str(ROOT / f'reference/grok-agy/{NAME}.kicad_pcb'))
    reference_drc = json.loads((REPORT / 'reference-drc.json').read_text())
    result = dict(status='routed-checks-pass', variant=args.layout, footprints=len(list(board.GetFootprints())),
                  checked_schematic_pins=len(expected), copper_layers=4,
                  board_size_mm=[160, 100], filled_power_planes=2,
                  unchanged_placement=True, unchanged_project_rules=True,
                  unchanged_schematic=True, preserved_checkpoint_hashes=True,
                  drc_violations=0, unconnected_items=0, schematic_parity_issues=0,
                  erc_violations=0, completed_groups=list(NET_GROUPS),
                  group_runtime_seconds=round(sum(x['runtime_seconds'] for x in summaries), 2),
                  board_sha256=digest(board_path),
                  schematic_sha256=digest(ROOT / f'design/{NAME}.kicad_sch'),
                  settings_sha256=digest(layout / f'{NAME}.kicad_pro'),
                  current=metrics(board), historical_reference=metrics(reference),
                  reference_drc_violations=dict(Counter(v['type'] for v in reference_drc['violations'])),
                  reference_unconnected_items=len(reference_drc['unconnected_items']),
                  reference_parity_issues=len(reference_drc['schematic_parity']),
                  scope='Different placement and adapted router; not an isolated algorithm benchmark. Hardware untested.')
    if args.layout == '02-codex-router':
        baseline = json.loads((REPORT / 'verification.json').read_text())
        baseline_board = LAYOUT / f'{NAME}.kicad_pcb'
        assert digest(baseline_board) == baseline['board_sha256'], 'Variant 1 changed'
        assert digest(LAYOUT / f'{NAME}.kicad_pro') == baseline['settings_sha256']
        assert digest(LAYOUT / f'{NAME}.kicad_sch') == baseline['schematic_sha256']
        result['variant1_preserved'] = True
        result['variant1'] = metrics(pcb.LoadBoard(str(baseline_board)))
        attempts = [json.loads(p.read_text()) for p in
                    (REPORT / args.layout).glob('group*/attempt-*/summary.json')]
        result['all_attempt_runtime_seconds'] = round(sum(a['runtime_seconds'] for a in attempts), 2)
        result['rejected_attempts'] = [dict(group=a['group'], attempt=a['attempt'],
                                          reason=a['failure'], seconds=a['runtime_seconds'])
                                      for a in attempts if a['status'] != 'validated']
        result['accepted_route_orders'] = {a['group']: a.get('route_order', NET_GROUPS[a['group']])
                                           for a in summaries}
        result['diagonal_track_segments'] = sum(
            1 for t in board.GetTracks() if t.Type() != pcb.PCB_VIA_T
            and abs(t.GetStart().x - t.GetEnd().x) > 0
            and abs(t.GetStart().y - t.GetEnd().y) > 0
            and abs(abs(t.GetStart().x - t.GetEnd().x) - abs(t.GetStart().y - t.GetEnd().y)) <= 2)
        assert result['diagonal_track_segments'] > 0, 'Expected octilinear copper in variant 2'
        result['scope'] = 'Variant 1 versus 2: identical placed input/circuit/rules; different planner and cost model. Hardware untested.'
    (report_dir / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
