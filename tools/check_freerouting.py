#!/usr/bin/env python3
"""Independent check of the promoted native MCP board and optional CLI control."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import pcbnew as pcb
from check_routing import footprint_geometry
from route_netgroup import metrics

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports/routing/04-freerouting'
CHECKPOINT = ROOT / 'checkpoints/01-codex-placed-unrouted'
LAYOUT = ROOT / 'layouts/04-freerouting'
NAME = 'Z80_Simple_Computer'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    cp = json.loads((CHECKPOINT / 'checkpoint.json').read_text())
    for name, expected in cp['files_sha256'].items():
        assert sha(CHECKPOINT / name) == expected
    meta = json.loads((LAYOUT / 'experiment.json').read_text())
    for variant, files in meta['preserved_variants'].items():
        for name, expected in files.items():
            assert sha(ROOT / 'layouts' / variant / name) == expected, f'Changed preserved variant: {variant}/{name}'
    for suffix in ('.kicad_sch', '.kicad_pro'):
        assert sha(LAYOUT / (NAME + suffix)) == cp['files_sha256'][NAME + suffix]
    assert sha(ROOT / 'design' / (NAME + '.kicad_sch')) == cp['files_sha256'][NAME + '.kicad_sch']
    board_path = LAYOUT / (NAME + '.kicad_pcb')
    board = pcb.LoadBoard(str(board_path))
    original = pcb.LoadBoard(str(CHECKPOINT / board_path.name))
    assert footprint_geometry(board) == footprint_geometry(original)
    def edges(b):
        return sorted((x.GetStart().x, x.GetStart().y, x.GetEnd().x, x.GetEnd().y)
                      for x in b.GetDrawings() if x.GetLayer() == pcb.Edge_Cuts)
    assert edges(board) == edges(original)
    xml = ET.parse(ROOT / 'output/schematic' / (NAME + '.xml'))
    expected = {(n.attrib['ref'], n.attrib['pin']): net.attrib['name']
                for net in xml.findall('.//nets/net') for n in net.findall('node')}
    actual = {(fp.GetReference(), pad.GetNumber()): pad.GetNetname()
              for fp in board.GetFootprints() for pad in fp.Pads() if pad.GetNetname()}
    assert actual == expected
    assert board.GetCopperLayerCount() == 4
    assert {(z.GetLayer(), z.GetNetname()) for z in board.Zones()} == {(pcb.In1_Cu,'GND'),(pcb.In2_Cu,'+5V')}
    assert all(z.GetFilledPolysList(z.GetLayer()).OutlineCount() > 0 for z in board.Zones())
    for item in board.GetTracks():
        assert item.GetNetname() not in ('','GND','+5V')
        if item.Type() == pcb.PCB_VIA_T:
            assert item.GetDrillValue() == pcb.FromMM(.3)
            assert item.TopLayer() == pcb.F_Cu and item.BottomLayer() == pcb.B_Cu
            assert all(item.GetWidth(layer) == pcb.FromMM(.7)
                       for layer in (pcb.F_Cu,pcb.In1_Cu,pcb.In2_Cu,pcb.B_Cu))
        else:
            assert item.GetLayer() in (pcb.F_Cu,pcb.B_Cu) and item.GetWidth() == pcb.FromMM(.15)
            assert item.GetStart() != item.GetEnd()
    drc = json.loads((REPORT / 'final-drc.json').read_text())
    assert not any(drc[k] for k in ('violations','unconnected_items','schematic_parity'))
    erc = json.loads((REPORT / 'final-erc.json').read_text())
    assert not [v for s in erc['sheets'] for v in s['violations']]
    imported = json.loads((REPORT / 'native-mcp/summary.json').read_text())
    assert imported['status'] == 'validated' and sha(board_path) == imported['board_sha256']
    jar = ROOT / 'tools/.cache/freerouting/freerouting-2.5.0.jar'
    assert sha(jar) == 'f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7'
    initialization = json.loads((REPORT / 'mcp/initialize.json').read_text())
    job = json.loads((REPORT / 'mcp/job-completed.json').read_text())
    assert job['state'] == 'COMPLETED' and job['statistics']['total_nets'] >= 81
    control = None
    if (REPORT / 'cli-control/result.json').exists():
        control = json.loads((REPORT / 'cli-control/result.json').read_text())
    result = dict(status='routed-checks-pass', variant='04-freerouting',
                  method='Official Freerouting 2.5.0 embedded local MCP over stdio',
                  mcp_server_info=initialization['result'].get('serverInfo'),
                  board_sha256=sha(board_path), input_board_sha256=cp['files_sha256'][board_path.name],
                  settings_sha256=sha(LAYOUT / (NAME + '.kicad_pro')),
                  schematic_sha256=sha(LAYOUT / (NAME + '.kicad_sch')),
                  transport_dsn_sha256=sha(REPORT / (NAME + '.dsn')),
                  footprints=len(list(board.GetFootprints())), checked_schematic_pins=len(expected),
                  board_size_mm=[160,100], copper_layers=4, filled_power_planes=2,
                  unchanged_placement=True, unchanged_project_rules=True, unchanged_schematic=True,
                  preserved_checkpoint_hashes=True, previous_variants_preserved=True,
                  drc_violations=0, erc_violations=0, unconnected_items=0, schematic_parity_issues=0,
                  current=metrics(board), mcp_reported_job_seconds=job['duration_seconds'],
                  cli_incomplete_connections=control['board_statistics']['connections']['incomplete_count'] if control else None,
                  bounded_position_restorations=imported['bounded_position_restorations'],
                  scope='Native-verified routing result; no hardware, timing or SI qualification.')
    for variant in ('01-reused-agy-router','02-codex-router'):
        result[variant] = metrics(pcb.LoadBoard(str(ROOT / 'layouts' / variant / board_path.name)))
    (REPORT / 'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
