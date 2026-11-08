#!/usr/bin/env python3
"""FreeRouting 2.5.0 CLI control using the MCP experiment's same DSN/settings."""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports/routing/04-freerouting'


def main():
    output = REPORT / 'cli-control'
    output.mkdir(exist_ok=True)
    command = ['java', '-Djava.awt.headless=true', '-jar',
               str(ROOT / 'tools/.cache/freerouting/freerouting-2.5.0.jar'),
               '-de', str(REPORT / 'Z80_Simple_Computer.dsn'),
               '-do', str(output / 'Z80_Simple_Computer.ses'),
               '--gui.enabled=false', '-da', f'--user_data_path={output / "state"}',
               '--api_server.enabled=false', '--mcp_server.enabled=false',
               '--router.autorouter.max_passes=30', '--router.autorouter.max_threads=1',
               '--router.optimizer.enabled=true', '--router.optimizer.max_passes=10',
               '--router.optimizer.max_threads=1', '--router.optimizer.improvement_threshold=2.5',
               '--router.fanout.enabled=false', '--router.automatic_neckdown=false',
               '--router.copper_to_edge_clearance_um=200', '--router.hole_clearance_um=200',
               '--router.clearance_tolerance_um=0', '--router.strict_drc=true',
               '--router.plane_nets=GND,+5V', '--router.max_threads=1',
               '--router.job_timeout=00:05:00',
               f'--router.result_json={output / "result.json"}']
    (output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    started = time.monotonic()
    with (output / 'cli.log').open('w') as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=420)
    summary = dict(returncode=result.returncode, wall_seconds=round(time.monotonic()-started, 3))
    (output / 'process.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
