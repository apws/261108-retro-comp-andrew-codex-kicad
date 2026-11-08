#!/usr/bin/env python3
"""Export review views of a hash-verified routing result without editing it."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'layouts/01-reused-agy-router/Z80_Simple_Computer.kicad_pcb'
OUT = ROOT / 'output/routing/01-reused-agy-router'


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


def main():
    global BOARD, OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', choices=['01-reused-agy-router', '02-codex-router', '04-freerouting'],
                        default='01-reused-agy-router')
    args = parser.parse_args()
    BOARD = ROOT / 'layouts' / args.layout / 'Z80_Simple_Computer.kicad_pcb'
    OUT = ROOT / 'output/routing' / args.layout
    verification_path = ROOT / 'reports/routing/verification.json' if args.layout == '01-reused-agy-router' else ROOT / 'reports/routing' / args.layout / 'verification.json'
    verification = json.loads(verification_path.read_text())
    before = hashlib.sha256(BOARD.read_bytes()).hexdigest()
    assert verification['status'] == 'routed-checks-pass' and before == verification['board_sha256']
    OUT.mkdir(parents=True, exist_ok=True)
    for name, layers, mirror in [
        ('top', 'F.Cu,F.Silkscreen,Edge.Cuts', False),
        ('bottom', 'B.Cu,Edge.Cuts', True),
        ('signals', 'F.Cu,B.Cu,F.Silkscreen,Edge.Cuts', False),
        ('planes', 'In1.Cu,In2.Cu,Edge.Cuts', False),
    ]:
        command = ['kicad-cli', 'pcb', 'export', 'svg', '--mode-single',
                   '--layers', layers, '--page-size-mode', '2', '--exclude-drawing-sheet',
                   '-o', str(OUT / f'{name}.svg')]
        if mirror:
            command.append('--mirror')
        run(command + [str(BOARD)])
        run(['rsvg-convert', '-w', '1800', '-b', 'white',
             '-o', str(OUT / f'{name}.png'), str(OUT / f'{name}.svg')])
    for side in ('top', 'bottom'):
        run(['kicad-cli', 'pcb', 'render', '--side', side, '--width', '1600',
             '--height', '1000', '--background', 'opaque', '--quality', 'basic',
             '-o', str(OUT / f'3d-{side}.png'), str(BOARD)])
    assert hashlib.sha256(BOARD.read_bytes()).hexdigest() == before
    (OUT / 'provenance.json').write_text(json.dumps(dict(
        board=str(BOARD.relative_to(ROOT)), board_sha256=before,
        bottom_view='Mirrored SVG/PNG, viewed from underside',
        note='Review images; no changes to native PCB.'), indent=2) + '\n')
    print(OUT)


if __name__ == '__main__':
    main()
