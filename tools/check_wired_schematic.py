#!/usr/bin/env python3
"""Check native netlist equality and drawn wire/bus paths independently.

Run native KiCad ERC and netlist export before this command. Scalar path checks
ignore net labels: actual wire geometry must connect all pins on each signal.
"""
import argparse
import collections
import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import kicad_schematic as k

ROOT = Path(__file__).resolve().parents[1]
NAME = 'Z80_Simple_Computer'
VARIANT = ROOT / 'schematics/02-wired-a3'
OUTPUT = ROOT / 'output/schematic-wired-a3'
REPORT = ROOT / 'reports/schematic-wired-a3'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def point(x, y):
    return round(float(x) * 10000), round(float(y) * 10000)


def xy(node):
    return point(k.atom(node[1]), k.atom(node[2]))


def native(path):
    tree = ET.parse(path)
    pins = {(n.attrib['ref'], n.attrib['pin']): net.attrib['name']
            for net in tree.findall('.//nets/net') for n in net.findall('node')}
    components = {c.attrib['ref']: (c.findtext('value'), c.findtext('footprint'))
                  for c in tree.findall('.//components/comp')}
    return pins, components


def graph(segments, connections):
    """Join wire ends, pins and explicit junctions; leave plain crossings apart."""
    points = set(connections)
    points.update(p for seg in segments for p in seg)
    adj = collections.defaultdict(set)
    for a, b in segments:
        assert a != b and (a[0] == b[0] or a[1] == b[1])
        on = sorted(p for p in points if
                    min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and
                    min(a[1], b[1]) <= p[1] <= max(a[1], b[1]) and
                    (a[0] == b[0] == p[0] or a[1] == b[1] == p[1]))
        for p, q in zip(on, on[1:]):
            adj[p].add(q)
            adj[q].add(p)
    groups = {}
    for start in adj:
        if start in groups:
            continue
        number = len(groups)
        todo = [start]
        groups[start] = number
        while todo:
            for p in adj[todo.pop()]:
                if p not in groups:
                    groups[p] = number
                    todo.append(p)
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accept-manual', action='store_true',
                        help='record the current manually edited drawing after all checks pass')
    args = parser.parse_args()
    path = VARIANT / (NAME + '.kicad_sch')
    tree = k.parse(path.read_text())
    assert k.atom(k.kid(tree, 'paper')[1]) == 'A3'
    assert not k.kids(tree, 'sheet'), 'Expected a single flat sheet'
    base_pins, base_parts = native(ROOT / 'output/schematic' / (NAME + '.xml'))
    actual_pins, actual_parts = native(OUTPUT / (NAME + '.xml'))
    assert actual_pins == base_pins, 'Native pin-to-net mapping changed'
    assert actual_parts == base_parts, 'Component values or footprints changed'
    erc = json.loads((REPORT / 'erc.json').read_text())
    assert not [v for s in erc['sheets'] for v in s['violations']]
    lib = {k.atom(sym[1]): sym for sym in k.kids(k.kid(tree, 'lib_symbols'), 'symbol')}
    pins = {}
    all_points = set()
    for sym in k.kids(tree, 'symbol'):
        at = k.kid(sym, 'at')
        assert float(k.atom(at[3])) == 0, 'Unexpected symbol rotation'
        x, y = float(k.atom(at[1])), float(k.atom(at[2]))
        unit = int(k.atom(k.kid(sym, 'unit')[1]))
        ref = next(k.atom(p[2]) for p in k.kids(sym, 'property')
                   if k.atom(p[1]) == 'Reference')
        definition = lib[k.atom(k.kid(sym, 'lib_id')[1])]
        for number, records in k.collect_pins(definition).items():
            selected = [r for r in records if r['unit'] in (0, unit) and r['style'] in (0, 1)]
            if not selected:
                continue
            rec = min(selected, key=lambda r: r['style'] != 1)
            p = point(x + rec['x'], y - rec['y'])
            all_points.add(p)
            if (ref, number) in actual_pins:
                pins[ref, number] = p
    assert set(pins) == set(actual_pins)
    def segments(kind):
        return [(xy(k.kids(k.kid(item, 'pts'), 'xy')[0]),
                 xy(k.kids(k.kid(item, 'pts'), 'xy')[1])) for item in k.kids(tree, kind)]
    wires, buses = segments('wire'), segments('bus')
    junctions = {xy(k.kid(j, 'at')) for j in k.kids(tree, 'junction')}
    labels = [(k.atom(l[1]), xy(k.kid(l, 'at'))) for l in k.kids(tree, 'label')]
    entries = []
    for entry in k.kids(tree, 'bus_entry'):
        a = xy(k.kid(entry, 'at'))
        delta = xy(k.kid(entry, 'size'))
        entries.append((a, (a[0] + delta[0], a[1] + delta[1])))
    wg = graph(wires, all_points | junctions | {a for a, b in entries})
    bg = graph(buses, junctions | {b for a, b in entries})
    def wire_groups_at(p):
        # KiCad may merge collinear runs across label anchors when saving.
        # Locate such a label on its segment without using it to join wires.
        if p in wg:
            return {wg[p]}
        return {wg[a] for a, b in wires if
                min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and
                min(a[1], b[1]) <= p[1] <= max(a[1], b[1]) and
                (a[0] == b[0] == p[0] or a[1] == b[1] == p[1])}
    member_labels = [(name, wire_groups_at(p)) for name, p in labels
                     if re.fullmatch('[AD][0-9]+', name)]
    by_net = collections.defaultdict(list)
    for key, name in actual_pins.items():
        by_net[name].append(key)
    scalar = {}
    bus_members = {}
    bus_roots = {name: bg[p] for name, p in labels if name in ('A[0..15]', 'D[0..7]')}
    assert len(bus_roots) == 2 and len(set(bg.values())) == 2
    assert bus_roots['A[0..15]'] != bus_roots['D[0..7]']
    for net, keys in by_net.items():
        bare = net.removeprefix('/')
        if bare in ('GND', '+5V') or bare.startswith('unconnected-'):
            continue
        if re.fullmatch('[AD][0-9]+', bare):
            bus_name = 'A[0..15]' if bare.startswith('A') else 'D[0..7]'
            for key in keys:
                p = pins[key]
                assert p in wg
                hits = [(a, b) for a, b in entries if a in wg and wg[a] == wg[p]]
                assert hits and all(bg.get(b) == bus_roots[bus_name] for a, b in hits), key
                # The member name must be present on the physical breakout.
                assert any(name == bare and groups == {wg[p]} for name, groups in member_labels), key
            bus_members[bare] = len(keys)
        else:
            assert all(pins[key] in wg for key in keys), (net, keys)
            groups = {wg[pins[key]] for key in keys}
            assert len(groups) == 1, f'{net} depends on labels instead of continuous wires'
            scalar[bare] = len(keys)
    # The accepted circuit, common placement and completed PCB experiments are
    # independent of this drawing. Compare them with the recorded baseline.
    publication = json.loads((ROOT / 'reports/publication/261108-2026/verification.json').read_text())
    for name, expected in publication['board_sha256'].items():
        assert digest(ROOT / name) == expected, f'Existing board changed: {name}'
    cp = json.loads((ROOT / 'checkpoints/01-codex-placed-unrouted/checkpoint.json').read_text())
    assert digest(ROOT / 'design' / (NAME + '.kicad_sch')) == cp['files_sha256'][NAME + '.kicad_sch']
    base_project = json.loads((ROOT / 'checkpoints/01-codex-placed-unrouted' / (NAME + '.kicad_pro')).read_text())
    project_path = VARIANT / (NAME + '.kicad_pro')
    project = json.loads(project_path.read_text())
    protected = copy.deepcopy(project)
    # Native Eeschema records the root sheet and revision on save. These four
    # metadata leaves are unrelated to ERC, netclasses or board design rules.
    protected['board']['ipc2581']['sch_revision'] = base_project['board']['ipc2581']['sch_revision']
    protected['schematic']['top_level_sheets'][0]['uuid'] = base_project['schematic']['top_level_sheets'][0]['uuid']
    protected['sheets'][0][0] = base_project['sheets'][0][0]
    protected['sheets'][0][1] = base_project['sheets'][0][1]
    assert protected == base_project, 'Project rules/settings changed beyond save metadata'
    meta = json.loads((VARIANT / 'provenance.json').read_text())
    manual = digest(path) != meta['generated_sha256']
    if manual and not args.accept_manual:
        record = meta.get('manual_review', {})
        assert record.get('schematic_sha256') == digest(path), 'Human edits require review with --accept-manual'
        assert record.get('project_sha256') == digest(project_path), 'Project changed after human review'
    assert meta['circuit_spec_sha256'] == digest(ROOT / 'design/circuit.json')
    assert meta['source_schematic_sha256'] == digest(ROOT / 'design' / (NAME + '.kicad_sch'))
    result = dict(status='pass', paper='A3', sheets=1,
                  components=len(actual_parts), checked_pin_assignments=len(actual_pins),
                  native_netlist_exact_match=True, component_values_and_footprints_unchanged=True,
                  erc_violations=0, wire_segments=len(wires), bus_segments=len(buses),
                  bus_entries=len(entries), scalar_nets_with_continuous_drawn_paths=len(scalar),
                  bus_member_nets=len(bus_members), bus_member_pin_connections=sum(bus_members.values()),
                  separate_address_and_data_bus_components=True,
                  source_schematic_and_all_four_pcb_variants_unchanged=True,
                  schematic_sha256=digest(path),
                  project_sha256=digest(project_path),
                  project_rules_unchanged=True,
                  drawing_ownership='human-edited' if manual else 'generated',
                  scalar_path_pin_counts=scalar, bus_member_pin_counts=bus_members,
                  scope='Drawing/connectivity verification; no new hardware or timing qualification.')
    (REPORT / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    meta.update(status='human-reviewed-verified' if manual else 'wired-verified',
                verification='reports/schematic-wired-a3/verification.json')
    if manual:
        review = dict(meta.get('manual_review', {}))
        review.update(schematic_sha256=digest(path), project_sha256=digest(project_path),
                      original_generated_sha256=meta['generated_sha256'],
                      note='Validated manually edited schematic; generator overwrite guard stays active.')
        meta.update(ownership='human-edited', manual_review=review)
    (VARIANT / 'provenance.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
