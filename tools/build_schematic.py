#!/usr/bin/env python3
"""Build the Codex A3 schematic from design/circuit.json and explicit geometry.

Uses attributed Grok/AGY low-level helpers in kicad_schematic.py. Connectivity,
placement, direct wiring, deterministic rendering and review output are Codex work.
The circuit JSON and this script own generated output; read docs before editing.
"""

import argparse
import hashlib
import json
import uuid
from pathlib import Path

import kicad_schematic as k

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "design/Z80_Simple_Computer.kicad_sch"
SPEC = ROOT / "design/circuit.json"

POSITIONS = {
    "U1": (48.26, 99.06), "U3": (118.11, 90.17),
    "U2": (186.69, 90.17), "U4": (260.35, 99.06),
    "U5": (355.60, 99.06), "U6": (300.99, 218.44),
    "Y1": (66.04, 161.29), "Y2": (355.60, 166.37),
    "R1": (24.13, 193.04), "C1": (24.13, 217.17),
    "R2": (40.64, 229.87), "SW1": (66.04, 240.03),
    "J1": (387.35, 231.14), "J5": (389.89, 172.72),
    "J2": (231.14, 172.72), "J3": (256.54, 172.72), "J4": (281.94, 172.72),
    "C20": (259.08, 199.39), "C21": (342.90, 199.39),
    "C22": (389.89, 198.12), "C23": (360.68, 212.09),
    "C24": (342.90, 219.71),
}
GATES = {
    "U7": {1: (55.88, 205.74), 2: (88.90, 205.74),
           3: (129.54, 238.76), 4: (129.54, 195.58),
           5: (224.79, 265.43), 6: (255.27, 265.43), 7: (222.25, 234.95)},
    "U8": {1: (172.72, 215.90), 2: (172.72, 236.22),
           3: (172.72, 172.72), 4: (172.72, 193.04), 5: (245.11, 234.95)},
}


def stable_id(kind, index):
    return str(uuid.uuid5(uuid.UUID(k.SHEET_UUID), f"{kind}/{index}"))


def build(spec):
    sheet = k.Sheet()
    parts = {p["ref"]: p for p in spec["components"]}
    for i in range(2, 12):
        POSITIONS[f"C{i}"] = (17.78 + (i - 2) * 19.05, 273.05)
    for i in range(1, 5):
        POSITIONS[f"H{i}"] = (213.36 + (i - 1) * 22.86, 279.40)
    for p in parts.values():
        lib, name = p["lib_id"].split(":")
        if p["lib_id"] not in sheet.syms:
            sheet.use(k.prepare(lib, name, p["parent"]))
        pins = k.collect_pins(sheet.syms[p["lib_id"]])
        declared = set(p["pins"]) | set(p["no_connect"])
        if declared != set(pins) or set(p["pins"]) & set(p["no_connect"]):
            raise ValueError(f"incomplete or duplicated pin specification: {p['ref']}")
        units = GATES.get(p["ref"], {1: POSITIONS.get(p["ref"])})
        for unit, position in units.items():
            sheet.place(p["ref"], p["lib_id"], p["value"], *position,
                        unit, p["footprint"], pins, mechanical=p["ref"].startswith("H"))
    for lib, name in (("power", "+5V"), ("power", "GND"), ("power", "PWR_FLAG")):
        sheet.use(k.prepare(lib, name))

    handled = set()

    def pin(ref, number):
        for p in sheet.placed:
            if p["ref"] == ref and str(number) in p["pins"]:
                return p["pins"][str(number)]
        raise ValueError((ref, number))

    def pos(ref, number):
        handled.add((ref, str(number)))
        return tuple(round(v, 4) for v in pin(ref, number)["world"])

    def wire(ref1, pin1, ref2, pin2, corners=()):
        assert parts[ref1]["pins"][str(pin1)] == parts[ref2]["pins"][str(pin2)]
        points = [pos(ref1, pin1), *corners, pos(ref2, pin2)]
        for a, b in zip(points, points[1:]):
            if a != b:
                assert a[0] == b[0] or a[1] == b[1], (a, b)
                sheet.tie(a, b)

    def tap(ref, number, point):
        sheet.tie(pos(ref, number), point)
        sheet.junctions.append(point)

    # Reset: the RC node, pushbutton discharge path and two polarities are wired.
    wire("R1", 2, "C1", 1)
    tap("U7", 1, (24.13, 205.74))
    tap("R2", 1, (40.64, 205.74))
    wire("R2", 2, "SW1", 1, [(40.64, 240.03)])
    wire("U7", 2, "U7", 3)
    sheet.labels.extend([((24.13, 205.74), "RESET_RC", "left bottom"),
                         ((66.04, 205.74), "RESET_H", "left bottom"),
                         ((40.64, 240.03), "SW_RESET", "left bottom")])

    # Address inversion to the associated OR input is explicit copper intent.
    wire("U7", 8, "U8", 13)
    wire("U7", 6, "U8", 5)
    sheet.labels.extend([((143.51, 195.58), "~{A15}", "left bottom"),
                         ((143.51, 238.76), "~{A7}", "left bottom")])

    # MAX232 flying capacitors are visibly wired with their physical polarities.
    wire("C20", 1, "U6", 1)
    wire("C20", 2, "U6", 3)
    wire("U6", 4, "C21", 1)
    wire("U6", 5, "C21", 2)
    wire("U6", 2, "C23", 1)
    wire("U6", 6, "C24", 2, [(330.20, 215.90), (330.20, 223.52)])
    tap("C24", 1, (335.28, 215.90))
    sheet.power_at(335.28, 215.90, "GND")
    # Keep this ground symbol to the left of the title-block boundary.
    tap("U6", 15, (290.83, 248.92))
    sheet.power_at(290.83, 248.92, "GND")
    for point, net in [((265.43, 195.58), "C1P"), ((265.43, 203.20), "C1M"),
                       ((325.12, 195.58), "C2P"), ((325.12, 203.20), "C2M"),
                       ((325.12, 208.28), "VS_PLUS"), ((331.47, 223.52), "VS_MINUS")]:
        sheet.labels.append((point, net, "left bottom"))

    # UART baud generator feeds its receive clock directly on the drawing.
    wire("U5", 15, "U5", 9, [(388.62, 129.54), (388.62, 134.62)])
    sheet.labels.append(((388.62, 134.62), "BAUDOUT", "left bottom"))

    # Adjacent strapped pins share a rail rather than stacked power symbols.
    for ref, numbers, rail, length in [
        ("U1", [16, 17], "+5V", 7.62),
        ("U5", [12, 13], "+5V", 7.62),
        ("U5", [19, 22, 25], "GND", 10.16),
        ("U5", [38, 39], "+5V", 10.16),
    ]:
        sheet.bus_to_power([pin(ref, n) for n in numbers], rail, length)
        handled.update((ref, str(n)) for n in numbers)

    for p in list(sheet.placed):
        if p["power"]:
            continue
        specpart = parts[p["ref"]]
        for number, rec in p["pins"].items():
            if (p["ref"], number) in handled:
                continue
            if number in specpart["no_connect"]:
                sheet.nc(p["ref"], number, unit=p["unit"])
                continue
            net = specpart["pins"][number]
            if net in ("+5V", "GND"):
                # Positive end of negative reservoir is GND: a plain label keeps
                # the ground symbol from obscuring the capacitor's plus mark.
                if p["ref"] == "C24" and number == "1":
                    sheet.label_pin(p["ref"], number, net, unit=p["unit"], length=2.54)
                else:
                    length = 7.62 if p["ref"] == "U1" else 3.81
                    if p["ref"] == "U3" and number == "1":
                        length = 12.70
                    sheet.power_on(p["ref"], number, net, unit=p["unit"], length=length)
            else:
                sheet.label_pin(p["ref"], number, net, unit=p["unit"], length=5.08)

    # Explicit source flags are attached to the external supply nets.
    for x, kind in ((372.11, "+5V"), (402.59, "GND")):
        flag = sheet.flag(x, 161.29)
        rail = sheet.power_at(x, 157.48 if kind == "+5V" else 165.10, kind)
        sheet.tie(flag["pins"]["1"]["world"], rail["pins"]["1"]["world"])

    # Split orthogonal segments at T-junctions so every branch is a real join.
    endpoints = set(sheet.junctions)
    endpoints.update(p for segment in sheet.wires for p in segment)
    split = []
    for a, b in sheet.wires:
        points = [p for p in endpoints if
                  min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and
                  min(a[1], b[1]) <= p[1] <= max(a[1], b[1]) and
                  (a[0] == b[0] == p[0] or a[1] == b[1] == p[1])]
        points.sort()
        split.extend(zip(points, points[1:]))
    sheet.wires = sorted(set(split))
    sheet.junctions = sorted(set(sheet.junctions))

    for text, x, y, size in [
        ("ANDREW'S SIMPLE Z80 / CODEX SCHEMATIC BASE", 15.24, 15.24, 2.0),
        ("Thomas Scherrer source; 27C256 substitution. Labels join buses; scalar support circuits use short wires.", 15.24, 22.86, 1.1),
        ("CPU", 40.64, 38.10, 1.6), ("ROM 0000-7FFF", 106.68, 38.10, 1.6),
        ("RAM 8000-FFFF", 175.26, 38.10, 1.6), ("PIO 00-03", 247.65, 38.10, 1.6),
        ("UART 80-87", 340.36, 38.10, 1.6),
        ("RESET / active-high peripherals, active-low CPU", 15.24, 179.07, 1.2),
        ("MEMORY DECODE", 113.03, 156.21, 1.2),
        ("I/O DECODE / A7 only; addresses mirror", 113.03, 207.01, 1.1),
        ("PIO HEADERS / signal pins only", 217.17, 153.67, 1.1),
        ("MAX232 / RS-232", 285.75, 179.07, 1.1),
        ("100nF bypass: C2 U1 / C3 U2 / C4 U3 / C5 U4 / C6 U5 / C7 U6 / C8 U7 / C9 U8 / C10 Y1 / C11 Y2", 15.24, 256.54, 1.0),
        ("External regulated +5V. No onboard regulator. Polled I/O; interrupt inputs held inactive.", 15.24, 30.48, 1.0),
    ]:
        sheet.text(text, x, y, size)
    return sheet, parts


def render(sheet, parts):
    lines = ['(kicad_sch', '  (version 20260306)', '  (generator "eeschema")',
             '  (generator_version "10.0")', f'  (uuid "{k.SHEET_UUID}")', '  (paper "A3")',
             '  (title_block (title "Andrew Z80 / Codex schematic base") (date "2026-11-08")',
             '    (rev "C1") (company "Falken / Codex")',
             '    (comment 1 "27C256 ROM 0000-7FFF; 62256 RAM 8000-FFFF")',
             '    (comment 2 "Regulated +5V input; 4MHz CPU; 1.8432MHz UART")',
             '    (comment 3 "UART CTS low; DCD and RI high. DE9 socket uses DTE pins.")',
             '    (comment 4 "Schematic base only / PCB placement and routing pending"))',
             '  (lib_symbols']
    lines.extend('    ' + k.dump(sym) for sym in sheet.syms.values())
    lines.append('  )')
    for p in sheet.placed:
        node = k.emit_instance(p)
        if p["power"] and p["value"] == "+5V":
            value = next(prop for prop in k.kids(node, "property") if k.atom(prop[1]) == "Value")
            value[:] = [item for item in value if not isinstance(item, list) or k.atom(item[0]) != "hide"]
            k.kid(value, "at")[1:3] = [k.a(k.fmt(p["x"])), k.a(k.fmt(p["y"] - 2.54))]
            k.kid(k.kid(value, "effects"), "font")[1] = [k.a("size"), k.a("1.0"), k.a("1.0")]
        if p["ref"] in parts:
            part = parts[p["ref"]]
            for prop in k.kids(node, "property"):
                name = k.atom(prop[1])
                if name == "Description":
                    prop[2] = k.q(part["role"])
                # Compact fields adjacent to passives; keep large IC fields
                # clear of their centered power pins.
                position = None
                if not p["power"] and name in ("Reference", "Value"):
                    if p["ref"].startswith(("C", "R")):
                        position = (p["x"] + 3.81, p["y"] + (-1.27 if name == "Reference" else 1.27))
                        effects = k.kid(prop, "effects")
                        effects.append([k.a("justify"), k.a("left")])
                    elif (p["ref"].startswith("U") and (p["ref"] not in GATES or p["unit"] == max(GATES[p["ref"]]))) or p["ref"].startswith("Y"):
                        top = min(pin["world"][1] for pin in p["pins"].values())
                        if not p["ref"].startswith("Y") or name == "Reference":
                            position = (p["x"] + 11.43, top + (-3.81 if name == "Reference" else -1.27))
                if position:
                    prop_at = k.kid(prop, "at")
                    prop_at[1:3] = [k.a(k.fmt(v)) for v in position]
                k.kid(k.kid(prop, "effects"), "font")[1] = [k.a("size"), k.a("1.0"), k.a("1.0")]
        lines.append('  ' + k.dump(node))
    for i, (a, b) in enumerate(sheet.wires):
        lines.append(f'  (wire (pts (xy {k.fmt(a[0])} {k.fmt(a[1])}) (xy {k.fmt(b[0])} {k.fmt(b[1])})) (stroke (width 0) (type default)) (uuid "{stable_id("wire", i)}"))')
    for i, (x, y) in enumerate(sheet.junctions):
        lines.append(f'  (junction (at {k.fmt(x)} {k.fmt(y)}) (diameter 0) (color 0 0 0 0) (uuid "{stable_id("junction", i)}"))')
    for i, ((x, y), net, justify) in enumerate(sheet.labels):
        lines.append(f'  (label {k.dump(k.q(net))} (at {k.fmt(x)} {k.fmt(y)} 0) (effects (font (size 1 1)) (justify {justify})) (uuid "{stable_id("label", i)}"))')
    for i, (x, y) in enumerate(sheet.ncs):
        lines.append(f'  (no_connect (at {k.fmt(x)} {k.fmt(y)}) (uuid "{stable_id("nc", i)}"))')
    for i, (text, x, y, size) in enumerate(sheet.texts):
        lines.append(f'  (text {k.dump(k.q(text))} (exclude_from_sim no) (at {k.fmt(x)} {k.fmt(y)} 0) (effects (font (size {size} {size})) (justify left bottom)) (uuid "{stable_id("text", i)}"))')
    lines.extend(['  (sheet_instances (path "/" (page "1")))', '  (embedded_fonts no)', ')', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the authoritative generated schematic")
    parser.add_argument("--output", type=Path, help="write a review copy to this path instead")
    args = parser.parse_args()
    sheet, parts = build(json.loads(SPEC.read_text()))
    data = render(sheet, parts).encode()
    if not args.write and args.output is None:
        print(f"Ready: {len(parts)} components, {len(sheet.wires)} wires. Use --write or --output PATH.")
        return
    output = args.output or DEST
    if list(output.parent.glob("*.lck")):
        raise SystemExit("Close the KiCad schematic before regenerating its file.")
    if output == DEST and output.exists():
        provenance = json.loads((DEST.parent / "provenance.json").read_text())
        expected = provenance.get("generated_sha256", provenance.get("seed_sha256"))
        if hashlib.sha256(output.read_bytes()).hexdigest() != expected:
            raise SystemExit("Schematic has local edits; review them before regeneration.")
    output.write_bytes(data)
    if output == DEST:
        provenance.update(status="codex-schematic-base-awaiting-hardware-validation",
                          circuit_spec="design/circuit.json", generator="tools/build_schematic.py",
                          generated_sha256=hashlib.sha256(data).hexdigest(),
                          note="New Codex capture and layout; attributed low-level Grok/AGY serialization helpers.")
        (DEST.parent / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Wrote {output}: {len(parts)} components, {len(sheet.wires)} wires")


if __name__ == "__main__":
    main()
