#!/usr/bin/env python3
"""Check KiCad-exported connectivity, decoder truth tables and baseline changes."""

import hashlib
import itertools
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def connectivity(path):
    tree = ET.parse(path)
    pins = {}
    for net in tree.findall(".//nets/net"):
        name = net.attrib["name"].removeprefix("/")
        for node in net.findall("node"):
            key = (node.attrib["ref"], node.attrib["pin"])
            assert key not in pins, f"pin appears twice: {key}"
            pins[key] = name
    return tree, pins


def main():
    spec = json.loads((ROOT / "design/circuit.json").read_text())
    tree, pins = connectivity(ROOT / "output/schematic/Z80_Simple_Computer.xml")
    declared = set()
    for p in spec["components"]:
        for number, expected in p["pins"].items():
            key = p["ref"], number
            declared.add(key)
            assert pins.get(key) == expected, f"{key}: expected {expected}, got {pins.get(key)}"
        for number in p["no_connect"]:
            key = p["ref"], number
            declared.add(key)
            assert pins[key].startswith("unconnected-"), (key, pins[key])
    assert set(pins) == declared, f"Unexpected/missing pins: {set(pins) ^ declared}"

    def net(ref, number):
        return pins[ref, str(number)]

    inv = [(1, 2), (3, 4), (5, 6), (9, 8), (11, 10), (13, 12)]
    or_gates = [(1, 2, 3), (4, 5, 6), (9, 10, 8), (12, 13, 11)]

    def solve(mreq, iorq, a15, a7, rc):
        state = {"+5V": 1, "GND": 0, net("U1", 19): mreq,
                 net("U1", 20): iorq, net("U1", 5): a15,
                 net("U1", 37): a7, net("R1", 2): rc}
        for _ in range(8):
            for a, y in inv:
                if net("U7", a) in state:
                    state[net("U7", y)] = 1 - state[net("U7", a)]
            for a, b, y in or_gates:
                if net("U8", a) in state and net("U8", b) in state:
                    state[net("U8", y)] = state[net("U8", a)] | state[net("U8", b)]
        return state

    rows = []
    for mreq, iorq, a15, a7, rc in itertools.product((0, 1), repeat=5):
        state = solve(mreq, iorq, a15, a7, rc)
        result = {"rom_selected": state[net("U3", 20)] == 0,
                  "ram_selected": state[net("U2", 20)] == 0,
                  "pio_selected": state[net("U4", 6)] == 0,
                  "uart_selected": state[net("U5", 14)] == 0}
        assert result["rom_selected"] == (mreq == 0 and a15 == 0)
        assert result["ram_selected"] == (mreq == 0 and a15 == 1)
        assert result["pio_selected"] == (iorq == 0 and a7 == 0)
        assert result["uart_selected"] == (iorq == 0 and a7 == 1)
        assert state[net("U1", 26)] == rc
        assert state[net("U4", 35)] == state[net("U5", 35)] == 1 - rc
        rows.append(dict(mreq=mreq, iorq=iorq, a15=a15, a7=a7, reset_rc=rc, **result))

    assert net("U3", 1) == net("U3", 28) == "+5V"
    assert net("U5", 15) == net("U5", 9)  # BAUDOUT -> RCLK
    assert net("U5", 36) == "GND"  # asserted CTS
    assert net("U5", 38) == net("U5", 39) == "+5V"  # inactive DCD, RI
    assert net("C24", 1) == "GND" and net("C24", 2) == net("U6", 6)
    assert net("J1", 2) == net("U6", 13) and net("J1", 3) == net("U6", 14)

    _, baseline = connectivity(ROOT / "reports/baseline-netlist.xml")
    differences = [dict(ref=ref, pin=number, before=baseline.get((ref, number)), after=name)
                   for (ref, number), name in sorted(pins.items()) if baseline.get((ref, number)) != name]
    assert {(d["ref"], d["pin"]) for d in differences} == {("U5", "36"), ("U5", "38"), ("U5", "39")}
    report = dict(status="pass", components=len(tree.findall(".//components/comp")),
                  assigned_or_nc_pins=len(pins), truth_table_cases=len(rows),
                  baseline_connectivity_differences=differences,
                  schematic_sha256=hashlib.sha256((ROOT / "design/Z80_Simple_Computer.kicad_sch").read_bytes()).hexdigest(),
                  note="Static connectivity and combinational checks; not analog reset timing, bus timing, SI or hardware validation.")
    (ROOT / "reports/schematic/connectivity-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    (ROOT / "reports/schematic/decoder-truth-table.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
