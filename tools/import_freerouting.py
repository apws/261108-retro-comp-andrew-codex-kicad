#!/usr/bin/env python3
"""Import an MCP/CLI session into a candidate native project; DRC before promotion."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pcbnew as pcb
from check_routing import footprint_geometry
from route_netgroup import metrics

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports/routing/04-freerouting'
CHECKPOINT = ROOT / 'checkpoints/01-codex-placed-unrouted'
LAYOUT = ROOT / 'layouts/04-freerouting'
NAME = 'Z80_Simple_Computer'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['mcp', 'cli'])
    args = parser.parse_args()
    session = REPORT / 'mcp-routed.ses' if args.kind == 'mcp' else REPORT / 'cli-control' / f'{NAME}.ses'
    assert session.exists() and session.stat().st_size > 1000, 'Reject an empty/incomplete routing session'
    stage = REPORT / f'native-{args.kind}'
    stage.mkdir(exist_ok=True)
    for suffix in ('.kicad_sch', '.kicad_pro'):
        shutil.copy2(CHECKPOINT / (NAME + suffix), stage / (NAME + suffix))
    for name in ('fp-lib-table', 'sym-lib-table'):
        shutil.copy2(CHECKPOINT / name, stage / name)
    if not (stage / 'libs').exists():
        (stage / 'libs').symlink_to(os.path.relpath(ROOT / 'libs', stage), target_is_directory=True)
    candidate = stage / f'{NAME}.kicad_pcb'
    board = pcb.LoadBoard(str(CHECKPOINT / candidate.name))
    original = pcb.LoadBoard(str(CHECKPOINT / candidate.name))
    board.SetFileName(str(candidate))
    assert pcb.ImportSpecctraSES(board, str(session)), 'Native session import failed'
    # Specctra um/10 resolution quantizes native nanometre coordinates.
    # Restore only bounded round-trip position/orientation representation
    # differences; reject any actual placement change or other pad mismatch.
    originals = {f.GetReference(): f for f in original.GetFootprints()}
    restored = []
    for fp in board.GetFootprints():
        before = originals[fp.GetReference()]
        dx = fp.GetPosition().x - before.GetPosition().x
        dy = fp.GetPosition().y - before.GetPosition().y
        da = (fp.GetOrientationDegrees() - before.GetOrientationDegrees() + 180) % 360 - 180
        assert abs(dx) <= 100 and abs(dy) <= 100 and abs(da) < 1e-6, 'SES changed placement beyond 0.1 um rounding'
        if dx or dy or fp.GetOrientationDegrees() != before.GetOrientationDegrees():
            restored.append(dict(reference=fp.GetReference(), import_delta_nm=[dx, dy]))
            fp.SetPosition(before.GetPosition())
            fp.SetOrientationDegrees(before.GetOrientationDegrees())
    assert footprint_geometry(board) == footprint_geometry(original), 'Import changed placement/pads'
    pcb.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(candidate))
    drc = stage / 'drc.json'
    command = ['kicad-cli', 'pcb', 'drc', '--schematic-parity', '--all-track-errors',
               '--format', 'json', '-o', str(drc), str(candidate)]
    run = subprocess.run(command, capture_output=True, text=True)
    (stage / 'drc-command.log').write_text(run.stdout + run.stderr)
    assert not run.returncode and drc.exists()
    report = json.loads(drc.read_text())
    outer_only = all(t.Type() == pcb.PCB_VIA_T or t.GetLayer() in (pcb.F_Cu, pcb.B_Cu)
                     for t in board.GetTracks())
    signals_only = all(t.GetNetname() not in ('', '+5V', 'GND') for t in board.GetTracks())
    dimensions = all(t.GetWidth() == pcb.FromMM(.15) if t.Type() != pcb.PCB_VIA_T
                     else t.GetDrillValue() == pcb.FromMM(.30) and
                     all(t.GetWidth(l) == pcb.FromMM(.70) for l in
                         (pcb.F_Cu, pcb.In1_Cu, pcb.In2_Cu, pcb.B_Cu))
                     for t in board.GetTracks())
    accepted = not any(report[k] for k in ('violations', 'unconnected_items', 'schematic_parity'))
    accepted = accepted and outer_only and signals_only and dimensions
    result = dict(status='validated' if accepted else 'rejected', transport=args.kind,
                  input_board_sha256=digest(CHECKPOINT / candidate.name),
                  session_sha256=digest(session), board_sha256=digest(candidate),
                  unchanged_placement=True, outer_signal_layers_only=outer_only,
                  bounded_position_restorations=restored,
                  no_added_power_routes=signals_only, dimensions_match=dimensions,
                  violations=len(report['violations']), unconnected_items=len(report['unconnected_items']),
                  parity_issues=len(report['schematic_parity']), **metrics(board))
    (stage / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not accepted:
        raise SystemExit('Native candidate rejected; working projects preserved.')
    target = LAYOUT if args.kind == 'mcp' else LAYOUT / 'cli-control'
    assert not list(target.glob('*.lck'))
    target.mkdir(exist_ok=True)
    if args.kind == 'mcp':
        assert digest(target / candidate.name) == digest(CHECKPOINT / candidate.name), 'Preserve an existing edited/routed board'
        for suffix in ('.kicad_sch', '.kicad_pro'):
            assert digest(target / (NAME + suffix)) == digest(CHECKPOINT / (NAME + suffix)), 'Preserve edited circuit/settings'
    elif (target / candidate.name).exists():
        raise SystemExit('CLI-control board already exists; preserve it before another import.')
    for name in [NAME + suffix for suffix in ('.kicad_pcb', '.kicad_sch', '.kicad_pro')] + ['fp-lib-table','sym-lib-table']:
        shutil.copy2(stage / name, target / name)
    if not (target / 'libs').exists():
        (target / 'libs').symlink_to(os.path.relpath(ROOT / 'libs', target), target_is_directory=True)
    print('Promoted:', target)


if __name__ == '__main__':
    main()
