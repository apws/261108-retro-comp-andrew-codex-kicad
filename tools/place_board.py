#!/usr/bin/env python3
"""Create the first Codex Eurocard placement using native KiCad pcbnew.

Run with /usr/bin/python3. Reads KiCad's exported XML netlist. The placement
is new; the reference PCB is not loaded. Regeneration refuses local board edits.
"""

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pcbnew as pcb

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "layouts/01-reused-agy-router"
BOARD = LAYOUT / "Z80_Simple_Computer.kicad_pcb"
REPORT = ROOT / "reports/placement"
NETLIST = ROOT / "output/schematic/Z80_Simple_Computer.xml"
FP_ROOT = ROOT / "libs/kicad-footprints-10.0.7"

# Courtyard center (mm) and rotation (degrees). Connector anchors are specified
# separately so their mating faces overhang the intended board edge.
PLACEMENT = {
    "U1": (80, 22, 90), "U3": (130, 22, 90), "U2": (130, 49, 90),
    "U5": (65, 49, 270), "U4": (80, 78, 90),
    "U6": (20, 50, 180), "U7": (104, 49, 0), "U8": (119, 78, 0),
    "Y1": (35, 14, 0), "Y2": (38, 29, 0),
    "J2": (50, 94, 90), "J3": (112, 94, 90), "J4": (81, 94, 90),
    "SW1": (20, 90, 0), "R1": (15, 77, 0), "R2": (17, 82, 0),
    "C1": (30, 78, 0), "C20": (30, 58, 0), "C21": (30, 50, 0),
    "C22": (20, 70, 0), "C23": (30, 66, 0), "C24": (30, 42, 180),
    "H1": (5, 5, 0), "H2": (155, 5, 0),
    "H3": (5, 95, 0), "H4": (155, 95, 0),
}
ANCHORS = {"J1": (7.78, 55.54, -90), "J5": (154.4, 17.08, 90)}
BYPASS = {"C2": ("U1", "11"), "C3": ("U2", "28"), "C4": ("U3", "28"),
          "C5": ("U4", "26"), "C6": ("U5", "40"), "C7": ("U6", "16"),
          "C8": ("U7", "14"), "C9": ("U8", "14"),
          "C10": ("Y1", "14"), "C11": ("Y2", "8")}


def mm(value):
    return pcb.FromMM(value)


def xy(x, y):
    return pcb.VECTOR2I(mm(x), mm(y))


def rectangle(fp):
    b = fp.GetCourtyard(pcb.F_CrtYd).BBox()
    return tuple(pcb.ToMM(v) for v in (b.GetLeft(), b.GetTop(), b.GetRight(), b.GetBottom()))


def centered(fp, x, y, angle):
    fp.SetOrientationDegrees(angle)
    left, top, right, bottom = rectangle(fp)
    p = fp.GetPosition()
    fp.SetPosition(p + xy(x - (left + right) / 2, y - (top + bottom) / 2))


def intersects(a, b, gap=0.30):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or
                a[3] + gap <= b[1] or b[3] + gap <= a[1])


def add_zone(board, layer, net):
    zone = pcb.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNet(net)
    zone.SetPadConnection(pcb.ZONE_CONNECTION_THERMAL)
    zone.SetThermalReliefGap(mm(0.30))
    zone.SetThermalReliefSpokeWidth(mm(0.40))
    zone.SetMinThickness(mm(0.25))
    zone.SetLocalClearance(mm(0.30))
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in [(0.5, 0.5), (159.5, 0.5), (159.5, 99.5), (0.5, 99.5)]:
        outline.Append(mm(x), mm(y))
    board.Add(zone)


def main():
    if list(LAYOUT.glob("*.lck")):
        raise SystemExit("Close the KiCad project before generating the placement.")
    metadata_path = LAYOUT / "experiment.json"
    metadata = json.loads(metadata_path.read_text())
    schematic = LAYOUT / "Z80_Simple_Computer.kicad_sch"
    shash = hashlib.sha256(schematic.read_bytes()).hexdigest()
    if shash != metadata["schematic_snapshot"]["sha256"]:
        raise SystemExit("Layout schematic differs from its synchronized snapshot.")
    if BOARD.exists():
        expected = metadata.get("placement", {}).get("board_sha256")
        if hashlib.sha256(BOARD.read_bytes()).hexdigest() != expected:
            raise SystemExit("PCB has local changes; retain/review them before regeneration.")

    xml = ET.parse(NETLIST)
    board = pcb.BOARD()
    board.SetFileName(str(BOARD))
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().SetBoardThickness(mm(1.6))
    nets = {}
    pin_nets = {}
    pin_attributes = {}
    for n in xml.findall(".//nets/net"):
        name = n.attrib["name"]
        net = pcb.NETINFO_ITEM(board, name)
        board.Add(net)
        nets[name] = net
        for node in n.findall("node"):
            key = (node.attrib["ref"], node.attrib["pin"])
            pin_nets[key] = name
            pin_attributes[key] = node.attrib

    footprints = {}
    missing = []
    for component in xml.findall(".//components/comp"):
        ref = component.attrib["ref"]
        lib, name = component.findtext("footprint").split(":", 1)
        fp = pcb.FootprintLoad(str(FP_ROOT / f"{lib}.pretty"), name)
        if not fp:
            raise ValueError(f"Missing footprint for {ref}")
        fp.SetFPID(pcb.LIB_ID(lib, name))
        fp.SetReference(ref)
        fp.SetValue(component.findtext("value"))
        fp.GetField(pcb.FIELD_T_DESCRIPTION).SetText(component.findtext("description") or "")
        fp.GetField(pcb.FIELD_T_DATASHEET).SetText(component.findtext("fields/field[@name='Datasheet']") or "")
        path = pcb.KIID_PATH()
        for token in component.findtext("tstamps").split()[:1]:
            path.push_back(pcb.KIID(token))
        fp.SetPath(path)
        if ref.startswith("H"):
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
        board.Add(fp)
        footprints[ref] = fp
        found = set()
        for pad in fp.Pads():
            key = ref, pad.GetNumber()
            if key in pin_nets:
                pad.SetNet(nets[pin_nets[key]])
                attrs = pin_attributes[key]
                if hasattr(pad, "SetPinFunction"):
                    pad.SetPinFunction(attrs.get("pinfunction", ""))
                if hasattr(pad, "SetPinType"):
                    pad.SetPinType(attrs.get("pintype", ""))
                found.add(key)
        missing.extend(key for key in pin_nets if key[0] == ref and key not in found)
    if missing:
        raise ValueError(f"Unmapped schematic pads: {missing}")

    for ref, (x, y, angle) in PLACEMENT.items():
        centered(footprints[ref], x, y, angle)
    for ref, (x, y, angle) in ANCHORS.items():
        footprints[ref].SetOrientationDegrees(angle)
        footprints[ref].SetPosition(xy(x, y))

    fixed = set(PLACEMENT) | set(ANCHORS)
    occupied = {ref: rectangle(footprints[ref]) for ref in fixed}
    for ref, box in occupied.items():
        for other, obox in occupied.items():
            if ref < other and intersects(box, obox, 0):
                raise ValueError(f"Initial courtyard overlap: {ref}, {other}")

    # Search discrete candidate positions and orientations using actual footprint
    # courtyards. Score the capacitor's +5V pad distance from its IC supply pad.
    bypass_report = []
    for ref, (target_ref, target_number) in BYPASS.items():
        target = next(p for p in footprints[target_ref].Pads() if p.GetNumber() == target_number)
        tx, ty = pcb.ToMM(target.GetPosition().x), pcb.ToMM(target.GetPosition().y)
        fp = footprints[ref]
        choices = []
        for angle in (0, 90, 180, 270):
            centered(fp, 0, 0, angle)
            local = rectangle(fp)
            plus = next(p for p in fp.Pads() if p.GetNumber() == "1").GetPosition()
            px, py = pcb.ToMM(plus.x), pcb.ToMM(plus.y)
            for ix in range(-24, 25):
                x = round(tx * 2) / 2 + ix * 0.5
                for iy in range(-24, 25):
                    y = round(ty * 2) / 2 + iy * 0.5
                    box = (local[0] + x, local[1] + y, local[2] + x, local[3] + y)
                    if box[0] < 1 or box[1] < 1 or box[2] > 159 or box[3] > 99:
                        continue
                    if any(intersects(box, b) for b in occupied.values()):
                        continue
                    distance = math.hypot(x + px - tx, y + py - ty)
                    choices.append((distance, math.hypot(x - tx, y - ty), x, y, angle))
        if not choices:
            raise ValueError(f"No legal bypass position for {ref}")
        distance, _, x, y, angle = min(choices)
        centered(fp, x, y, angle)
        occupied[ref] = rectangle(fp)
        bypass_report.append(dict(capacitor=ref, target=target_ref, supply_pin=target_number,
                                  supply_pad_distance_mm=round(distance, 3),
                                  center_mm=[x, y], rotation_deg=angle))

    for start, end in [((0, 0), (160, 0)), ((160, 0), (160, 100)),
                       ((160, 100), (0, 100)), ((0, 100), (0, 0))]:
        edge = pcb.PCB_SHAPE(board)
        edge.SetShape(pcb.SHAPE_T_SEGMENT)
        edge.SetStart(xy(*start))
        edge.SetEnd(xy(*end))
        edge.SetWidth(mm(0.1))
        edge.SetLayer(pcb.Edge_Cuts)
        board.Add(edge)
    add_zone(board, pcb.In1_Cu, nets["GND"])
    add_zone(board, pcb.In2_Cu, nets["+5V"])

    # Keep reference/value text horizontal. Values on passives remain on F.Fab;
    # chip values fit inside their bodies and remain useful on the bare PCB.
    for ref, fp in footprints.items():
        left, top, right, bottom = rectangle(fp)
        cx, cy = (left + right) / 2, (top + bottom) / 2
        reference = fp.Reference()
        reference.SetTextSize(xy(0.85, 0.85))
        reference.SetTextThickness(mm(0.13))
        reference.SetTextAngleDegrees(0)
        reference.SetVisible(True)
        reference.SetPosition(xy(cx, top - 1.0))
        value = fp.Value()
        value.SetTextSize(xy(0.8, 0.8))
        value.SetTextThickness(mm(0.12))
        value.SetTextAngleDegrees(0)
        value.SetVisible(False)
        if ref.startswith(("U", "Y")):
            reference.SetPosition(xy(cx, cy - 1.0))
            value.SetLayer(pcb.F_SilkS)
            value.SetPosition(xy(cx, cy + 1.0))
            value.SetVisible(True)
            if ref in ("U7", "U8"):
                value.SetTextSize(xy(0.65, 0.8))
                value.SetTextThickness(mm(0.10))
        if ref in ("J2", "J3", "J4"):
            value.SetLayer(pcb.F_SilkS)
            value.SetPosition(xy(cx, bottom + 1.1))
            value.SetVisible(True)
        if ref == "J1":
            reference.SetPosition(xy(8, 32))
        if ref == "J5":
            reference.SetPosition(xy(149, 6))
        if ref in ("C2", "C6"):
            reference.SetPosition(xy(left - 1.8, cy))
        if ref.startswith("H"):
            reference.SetVisible(False)

    def text(label, x, y, size=1.0):
        item = pcb.PCB_TEXT(board)
        item.SetText(label)
        item.SetPosition(xy(x, y))
        item.SetTextSize(xy(size, size))
        item.SetTextThickness(mm(0.15))
        item.SetLayer(pcb.F_SilkS)
        board.Add(item)
    text("ANDREW Z80 / CODEX C1", 80, 4)
    text("160 x 100 mm / 4 layers", 80, 7, 0.85)
    text("+5V IN", 144, 5, 0.85)
    text("GND", 154.4, 7.6, 0.8)
    text("+5V", 154.4, 22, 0.8)
    text("RS-232", 8, 29, 0.8)
    text("RESET", 20, 96, 0.85)

    for fp in footprints.values():
        if fp.GetReference() not in ("J1", "J5"):
            box = rectangle(fp)
            assert box[0] >= 0 and box[1] >= 0 and box[2] <= 160 and box[3] <= 100, fp.GetReference()
        for pad in fp.Pads():
            p = pad.GetPosition()
            assert 0 < p.x < mm(160) and 0 < p.y < mm(100), fp.GetReference()

    pcb.ZONE_FILLER(board).Fill(board.Zones())
    REPORT.mkdir(parents=True, exist_ok=True)
    pcb.SaveBoard(str(BOARD), board)
    board_hash = hashlib.sha256(BOARD.read_bytes()).hexdigest()
    manifest = dict(board_size_mm=[160, 100], copper_layers=4, component_count=len(footprints),
                    tracks=0, vias=0, zones=2, schematic_sha256=shash,
                    netlist_sha256=hashlib.sha256(NETLIST.read_bytes()).hexdigest(),
                    board_sha256=board_hash, bypass=bypass_report,
                    placement={ref: dict(courtyard_center_mm=[round((rectangle(fp)[0] + rectangle(fp)[2]) / 2, 4),
                                                             round((rectangle(fp)[1] + rectangle(fp)[3]) / 2, 4)],
                                         rotation_deg=fp.GetOrientationDegrees(),
                                         courtyard_mm=[round(x, 4) for x in rectangle(fp)])
                               for ref, fp in sorted(footprints.items())})
    (REPORT / "placement.json").write_text(json.dumps(manifest, indent=2) + "\n")
    metadata["status"] = "placed-unrouted-awaiting-checkpoint-validation"
    metadata["board_status"] = "placed-unrouted"
    metadata["placement"] = dict(board_sha256=board_hash, schematic_sha256=shash,
                                 report="reports/placement/placement.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Created placement: {len(footprints)} footprints, 4 layers, no signal tracks or vias.")
    print("Bypass supply-pad distances (mm):", {x["capacitor"]: x["supply_pad_distance_mm"] for x in bypass_report})


if __name__ == "__main__":
    main()
