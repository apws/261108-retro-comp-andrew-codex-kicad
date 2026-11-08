#!/usr/bin/env python3
"""Export the preserved common input and add explicit transport constraints.

Does not edit native boards. The v2.5.0 parser requires autoroute_settings
after layer declarations but before planes establish layerStructure.
"""
import hashlib
import json
from pathlib import Path
import pcbnew as pcb

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports/routing/04-freerouting'
CHECKPOINT = ROOT / 'checkpoints/01-codex-placed-unrouted'


def main():
    meta = json.loads((CHECKPOINT / 'checkpoint.json').read_text())
    for name, expected in meta['files_sha256'].items():
        assert hashlib.sha256((CHECKPOINT / name).read_bytes()).hexdigest() == expected
    REPORT.mkdir(parents=True, exist_ok=True)
    board = pcb.LoadBoard(str(CHECKPOINT / 'Z80_Simple_Computer.kicad_pcb'))
    raw = REPORT / 'Z80_Simple_Computer.raw.dsn'
    assert pcb.ExportSpecctraDSN(board, str(raw))
    text = raw.read_text()
    for layer in ('In1.Cu', 'In2.Cu'):
        marker = f'(layer {layer}\n      (type signal)'
        assert text.count(marker) == 1
        text = text.replace(marker, f'(layer {layer}\n      (type power)')
    text = text.replace('(clearance 37.5 (type smd_smd))',
                        '(clearance 150 (type smd_smd))')
    settings = '''    (autoroute_settings
      (autoroute on) (postroute on) (vias on)
      (via_costs 50) (plane_via_costs 5) (start_ripup_costs 100)
      (layer_rule F.Cu (active on) (preferred_direction horizontal)
        (preferred_direction_trace_costs 1.0) (against_preferred_direction_trace_costs 2.5))
      (layer_rule In1.Cu (active off) (preferred_direction vertical))
      (layer_rule In2.Cu (active off) (preferred_direction horizontal))
      (layer_rule B.Cu (active on) (preferred_direction vertical)
        (preferred_direction_trace_costs 1.0) (against_preferred_direction_trace_costs 2.5))
    )
'''
    assert text.count('    (boundary\n') == 1
    text = text.replace('    (boundary\n', settings + '    (boundary\n')
    target = REPORT / 'Z80_Simple_Computer.dsn'
    target.write_text(text)
    result = dict(raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                  routing_dsn_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                  changes=['Classify two inner layers as power',
                           'Explicit outer-layer-only route settings before boundary/planes',
                           'Raise unused SMD-specific exception to native 150 um minimum'],
                  native_board_unchanged=True)
    (REPORT / 'dsn-normalization.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
