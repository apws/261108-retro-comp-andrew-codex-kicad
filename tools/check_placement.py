#!/usr/bin/env python3
"""Verify the native placed, unrouted PCB against KiCad's exported netlist.

Run after the DRC command documented in the placement handover. Unconnected
signal items are expected; geometry violations or parity findings are not.
"""

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew as pcb

from place_board import BOARD, BYPASS, LAYOUT, NETLIST, REPORT, ROOT, intersects, rectangle


def main():
    board = pcb.LoadBoard(str(BOARD))
    drc = json.loads((REPORT / "drc.json").read_text())
    assert not drc["violations"], "Resolve placement violations before checkpointing"
    assert not drc["schematic_parity"], "Resolve schematic parity findings"
    footprints = {fp.GetReference(): fp for fp in board.GetFootprints()}
    xml = ET.parse(NETLIST)
    references = {c.attrib["ref"] for c in xml.findall(".//components/comp")}
    assert set(footprints) == references
    actual = {}
    for ref, fp in footprints.items():
        for pad in fp.Pads():
            key = ref, pad.GetNumber()
            if pad.GetNetname():
                if key in actual:
                    assert actual[key] == pad.GetNetname()
                actual[key] = pad.GetNetname()
    expected = {(node.attrib["ref"], node.attrib["pin"]): net.attrib["name"]
                for net in xml.findall(".//nets/net") for node in net.findall("node")}
    assert actual == expected, "Schematic-to-pad mapping differs"
    assert board.GetCopperLayerCount() == 4
    assert len(list(board.GetTracks())) == 0
    zones = {(zone.GetLayer(), zone.GetNetname()) for zone in board.Zones()}
    assert zones == {(pcb.In1_Cu, "GND"), (pcb.In2_Cu, "+5V")}
    for zone in board.Zones():
        assert zone.GetFilledPolysList(zone.GetLayer()).OutlineCount() > 0, "Empty plane fill"
    edges = [item for item in board.GetDrawings() if item.GetLayer() == pcb.Edge_Cuts]
    assert len(edges) == 4
    points = [point for edge in edges for point in (edge.GetStart(), edge.GetEnd())]
    assert max(p.x for p in points) - min(p.x for p in points) == pcb.FromMM(160)
    assert max(p.y for p in points) - min(p.y for p in points) == pcb.FromMM(100)
    boxes = {ref: rectangle(fp) for ref, fp in footprints.items()}
    for ref, box in boxes.items():
        if ref not in ("J1", "J5"):
            assert box[0] >= 0 and box[1] >= 0 and box[2] <= 160 and box[3] <= 100
        for other, other_box in boxes.items():
            if ref < other:
                assert not intersects(box, other_box, 0), (ref, other)

    distances = {}
    for cap, (ref, number) in BYPASS.items():
        a = next(p for p in footprints[cap].Pads() if p.GetNumber() == "1").GetPosition()
        b = next(p for p in footprints[ref].Pads() if p.GetNumber() == number).GetPosition()
        distance = pcb.ToMM(int(round(math.hypot(a.x - b.x, a.y - b.y))))
        assert distance < 5.0, (cap, distance)
        distances[cap] = round(distance, 3)
    models = 0
    for fp in footprints.values():
        for model in fp.Models():
            path = Path(model.m_Filename.replace("${KIPRJMOD}", str(LAYOUT)))
            assert path.is_file(), f"Missing vendored model for {fp.GetReference()}"
            models += 1
    # Every footprint with an actual component body has its vendored STEP model.
    assert models == len(footprints) - 4
    assert not any("[GND]" in item["description"] or "[+5V]" in item["description"]
                   for finding in drc["unconnected_items"] for item in finding["items"]), "Power plane disconnected"
    project = BOARD.with_suffix(".kicad_pro")
    result = dict(status="placed-unrouted-checks-pass", components=len(footprints),
                  checked_schematic_pins=len(expected), board_size_mm=[160, 100], copper_layers=4,
                  tracks_and_vias=0, filled_power_planes=2, models=models,
                  geometry_and_silkscreen_violations=0, schematic_parity_issues=0,
                  expected_unconnected_items=len(drc["unconnected_items"]),
                  bypass_supply_pad_distances_mm=distances,
                  board_sha256=hashlib.sha256(BOARD.read_bytes()).hexdigest(),
                  schematic_sha256=hashlib.sha256(BOARD.with_suffix(".kicad_sch").read_bytes()).hexdigest(),
                  project_settings_sha256=hashlib.sha256(project.read_bytes()).hexdigest(),
                  limitation="Unrouted checkpoint; not a completed or fabrication-ready board.")
    (REPORT / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if not k.endswith("sha256")}, indent=2))


if __name__ == "__main__":
    main()
