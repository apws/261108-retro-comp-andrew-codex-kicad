#!/usr/bin/env python3
"""Task-local stdio MCP client for the official embedded Freerouting server.

No global Codex configuration changes. Saves initialize/tools discovery and
request/response evidence. Drop *.request.json files in reports/.../mcp/inbox;
responses are written beside them. A STOP file ends only this owned server.
"""
import json
import os
import selectors
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'reports/routing/04-freerouting'
MCP = REPORT / 'mcp'
JAR = ROOT / 'tools/.cache/freerouting/freerouting-2.5.0.jar'


def main():
    inbox = MCP / 'inbox'
    inbox.mkdir(parents=True, exist_ok=True)
    if (MCP / 'STOP').exists():
        raise SystemExit('Old STOP marker exists; review it before starting a new session.')
    command = ['java', '-Djava.awt.headless=true', '-jar', str(JAR),
               '--gui.enabled=false', '-da', '--logging.console.level=WARN',
               f'--user_data_path={REPORT / "server-state"}',
               '--api_server.enabled=true',
               '--api_server.endpoints=http://127.0.0.1:37864',
               '--api_server.authentication.enabled=false',
               '--mcp_server.enabled=true',
               '--mcp_server.endpoints=http://127.0.0.1:37964',
               '--mcp_server.target_api_base_url=http://127.0.0.1:37864',
               '--mcp_server.authentication.enabled=false', '--mcp_server.stdio=true']
    (MCP / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    stderr = (MCP / 'server-stderr.log').open('ab')
    process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=stderr, bufsize=0)
    (MCP / 'owned-pid').write_text(str(process.pid) + '\n')
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending, sequence = bytearray(), 0

    def send(value):
        process.stdin.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
        process.stdin.flush()

    def rpc(method, params, timeout=120):
        nonlocal sequence
        sequence += 1
        request_id = sequence
        send(dict(jsonrpc='2.0', id=request_id, method=method, params=params))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b'\n' in pending:
                line, _, rest = pending.partition(b'\n')
                pending[:] = rest
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError):
                    with (MCP / 'stdout-nonprotocol.log').open('ab') as log:
                        log.write(line + b'\n')
                    continue
                if value.get('id') == request_id:
                    return value
                with (MCP / 'notifications.jsonl').open('a') as log:
                    log.write(json.dumps(value) + '\n')
            if process.poll() is not None:
                raise RuntimeError(f'Owned MCP process exited: {process.returncode}')
            if selector.select(timeout=1):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('MCP stdout closed')
                pending.extend(chunk)
        raise TimeoutError(f'MCP request timed out: {method}')

    try:
        initialize = rpc('initialize', dict(protocolVersion='2024-11-05',
                         capabilities={}, clientInfo=dict(name='codex-kicad-comparison', version='1.0')))
        (MCP / 'initialize.json').write_text(json.dumps(initialize, indent=2) + '\n')
        if 'error' in initialize:
            raise RuntimeError(str(initialize['error']))
        send(dict(jsonrpc='2.0', method='notifications/initialized', params={}))
        tools = rpc('tools/list', {})
        (MCP / 'tools.json').write_text(json.dumps(tools, indent=2) + '\n')
        if 'error' in tools:
            raise RuntimeError(str(tools['error']))
        print(f'MCP ready: {len(tools.get("result", {}).get("tools", []))} discovered tools', flush=True)
        last_request = time.monotonic()
        while not (MCP / 'STOP').exists() and time.monotonic() - last_request < 900:
            if process.poll() is not None:
                raise RuntimeError(f'MCP server exited: {process.returncode}')
            for path in sorted(inbox.glob('*.request.json')):
                output = path.with_name(path.name.replace('.request.json', '.response.json'))
                if output.exists():
                    continue
                request = json.loads(path.read_text())
                print(f'MCP request: {path.name}', flush=True)
                response = rpc(request['method'], request.get('params', {}), request.get('timeout', 600))
                output.write_text(json.dumps(response, indent=2) + '\n')
                print(f'MCP response saved: {output.name}', flush=True)
                last_request = time.monotonic()
            time.sleep(0.2)
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stderr.close()


if __name__ == '__main__':
    main()
