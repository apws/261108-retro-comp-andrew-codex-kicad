#!/usr/bin/env python3
"""Rebuild Z80_Simple_Computer.kicad_sch from stock KiCad 10.0.7 symbols.

The symbols live in libs/kicad-symbols-10.0.7/ (official tag 10.0.7).
Symbols that only ``extend`` a parent are flattened so the sheet carries
the drawings and pins itself and still opens with no system libraries.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBROOT = ROOT / "libs" / "kicad-symbols-10.0.7"
OUT = ROOT / "Z80_Simple_Computer.kicad_sch"
PROJECT = "Z80_Simple_Computer"
SHEET_UUID = "e3a4b5c6-d7e8-4f9a-0b1c-2d3e4f5a6b7c"

FP_DIP14 = "Package_DIP:DIP-14_W7.62mm"
FP_DIP16 = "Package_DIP:DIP-16_W7.62mm"
FP_R = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
FP_C = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm"
FP_CP = "Capacitor_THT:CP_Radial_D5.0mm_P2.50mm"
FP_SW = "Button_Switch_THT:SW_PUSH_6mm"
FP_DE9 = (
    "Connector_Dsub:DSUB-9_Socket_Horizontal_P2.77x2.84mm_"
    "EdgePinOffset4.94mm_Housed_MountingHolesOffset4.94mm"
)
FP_HDR = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
GRID = 1.27


def snap(v):
    return round(round(float(v) / GRID) * GRID, 4)


def parse(text: str):
    s = text
    i = 0
    n = len(s)

    def skip():
        nonlocal i
        while i < n and s[i].isspace():
            i += 1

    def rec():
        nonlocal i
        skip()
        if i >= n:
            raise ValueError("unexpected end")
        if s[i] == "(":
            i += 1
            node = []
            while True:
                skip()
                if s[i] == ")":
                    i += 1
                    return node
                node.append(rec())
        if s[i] == '"':
            i += 1
            buf = []
            while s[i] != '"':
                if s[i] == "\\":
                    i += 1
                buf.append(s[i])
                i += 1
            i += 1
            return ("str", "".join(buf))
        j = i
        while i < n and not s[i].isspace() and s[i] not in "()":
            i += 1
        return ("atom", s[j:i])

    tree = rec()
    skip()
    if i != n:
        raise ValueError(f"trailing input at {i}")
    return tree


def atom(node, default=None):
    if isinstance(node, tuple):
        return node[1]
    return default


def is_list(node):
    return isinstance(node, list)


def dump(node) -> str:
    if isinstance(node, tuple):
        kind, val = node
        if kind == "str":
            esc = val.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{esc}"'
        return val
    parts = []
    for child in node:
        parts.append(dump(child))
    return "(" + " ".join(parts) + ")"


def kid(node, name):
    for child in node:
        if is_list(child) and child and atom(child[0]) == name:
            return child
    return None


def kids(node, name):
    return [c for c in node if is_list(c) and c and atom(c[0]) == name]


def top_symbol(tree):
    return kid(tree, "symbol")


def load_lib_symbol(rel: str):
    return parse((LIBROOT / rel).read_text())


def prop_name(prop):
    # (property "Name" "value" ...)
    return atom(prop[1])


def flatten(child_tree, parent_tree, new_name: str):
    """Parent drawings + child properties, under new_name."""
    child = top_symbol(child_tree)
    parent = top_symbol(parent_tree)
    parent_name = atom(parent[1])
    out = copy.deepcopy(parent)
    out[1] = ("str", new_name)
    child_props = {prop_name(p): copy.deepcopy(p) for p in kids(child, "property")}
    out[:] = [c for c in out if not (is_list(c) and atom(c[0]) == "property")]
    # Keep library flags, then the child's properties, then units.
    inserted = False
    rebuilt = [out[0], out[1]]
    for item in out[2:]:
        if not inserted and is_list(item) and atom(item[0]) == "symbol":
            rebuilt.extend(child_props.values())
            inserted = True
        rebuilt.append(item)
    if not inserted:
        rebuilt.extend(child_props.values())
    # Rename unit symbols Parent_1_1 -> Child_1_1
    def rename(node):
        if not is_list(node):
            return
        if node and atom(node[0]) == "symbol" and isinstance(node[1], tuple):
            nm = atom(node[1])
            if nm == parent_name or nm.startswith(parent_name + "_"):
                node[1] = ("str", new_name + nm[len(parent_name):])
        for c in node:
            if is_list(c):
                rename(c)

    rename(rebuilt)
    return rebuilt


def prepare(lib: str, filename: str, parent: str | None = None):
    tree = load_lib_symbol(f"{lib}.kicad_symdir/{filename}.kicad_sym")
    sym = top_symbol(tree)
    name = atom(sym[1])
    if parent:
        parent_tree = load_lib_symbol(f"{lib}.kicad_symdir/{parent}.kicad_sym")
        sym = flatten(tree, parent_tree, name)
    else:
        sym = copy.deepcopy(sym)
    sym[1] = ("str", f"{lib}:{name}")
    return sym


def unit_of(unit_name: str, symbol_name: str):
    # "74HC14_1_0" -> (1, 0); symbol_name is the bare name
    rest = unit_name[len(symbol_name) + 1:]
    unit_s, style_s = rest.split("_")
    return int(unit_s), int(style_s)


def bare_name(lib_id: str) -> str:
    return lib_id.split(":", 1)[1]


def collect_pins(sym):
    """pin number -> list of {unit, style, x, y, ang, name, etype} in symbol space."""
    lib_id = atom(sym[1])
    name = bare_name(lib_id)
    found = {}

    def walk(node):
        if not is_list(node) or not node:
            return
        if atom(node[0]) == "symbol" and isinstance(node[1], tuple):
            un = atom(node[1])
            if un.startswith(name + "_"):
                unit, style = unit_of(un, name)
                for pin in kids(node, "pin"):
                    at = kid(pin, "at")
                    num = atom(kid(pin, "number")[1])
                    nm = kid(pin, "name")
                    pname = atom(nm[1]) if nm else ""
                    rec = {
                        "num": num,
                        "name": pname,
                        "unit": unit,
                        "style": style,
                        "x": float(atom(at[1])),
                        "y": float(atom(at[2])),
                        "ang": float(atom(at[3])),
                        "etype": atom(pin[1]),
                    }
                    found.setdefault(num, []).append(rec)
                return
        for c in node:
            if is_list(c):
                walk(c)

    walk(sym)
    return found


class Sheet:
    def __init__(self):
        self.syms = {}  # lib_id -> symbol tree
        self.placed = []  # dicts
        self.wires = []
        self.junctions = []
        self.labels = []
        self.ncs = []
        self.texts = []
        self._pwr = 0
        self._flg = 0

    def use(self, sym):
        self.syms[atom(sym[1])] = sym
        return collect_pins(sym)

    def place(self, ref, lib_id, value, x, y, unit, footprint, pins, *, power=False, mechanical=False):
        x, y = snap(x), snap(y)
        # Every unit of a real part stays in the netlist. KiCad 10 drops a
        # unit whose in_bom is no, so a gate drawn that way never connects.
        # The BOM still lists the reference once. Power symbols stay out.
        # A mounting hole is on the board and out of the BOM, and it has no pin.
        bom = not power and not mechanical
        chosen = []
        for num, recs in pins.items():
            keep = [
                r for r in recs
                if r["style"] in (0, 1) and (r["unit"] == unit or r["unit"] == 0)
            ]
            if not keep:
                continue
            # Prefer the default body style if both exist.
            keep.sort(key=lambda r: 0 if r["style"] == 1 else 1)
            chosen.append(keep[0])
        if not chosen and not mechanical:
            raise SystemExit(f"no pins for {ref} unit {unit} ({lib_id})")
        inst = {
            "ref": ref,
            "lib_id": lib_id,
            "value": value,
            "x": x,
            "y": y,
            "unit": unit,
            "footprint": footprint or "",
            "bom": bom,
            "on_board": not power,
            "pos": not power,
            "power": power,
            "uuid": str(uuid.uuid4()),
            "pins": {},
        }
        for rec in chosen:
            world = self._world(inst, rec)
            inst["pins"][rec["num"]] = {**rec, "world": world, "uuid": str(uuid.uuid4())}
        self.placed.append(inst)
        return inst

    def _world(self, inst, rec):
        return (inst["x"] + rec["x"], inst["y"] - rec["y"])

    def inst(self, ref, unit=None):
        hits = [p for p in self.placed if p["ref"] == ref and (unit is None or p["unit"] == unit)]
        if len(hits) != 1:
            raise SystemExit(f"expected one {ref} unit {unit}, found {len(hits)}")
        return hits[0]

    def pin(self, ref, number, unit=None):
        inst = self.inst(ref, unit)
        key = str(number)
        if key not in inst["pins"]:
            names = {p["num"]: p["name"] for p in inst["pins"].values()}
            raise SystemExit(f"{ref} has no pin {number}. Have {names}")
        return inst["pins"][key]

    def pin_named(self, ref, name, unit=None):
        inst = self.inst(ref, unit)
        hits = [p for p in inst["pins"].values() if p["name"] == name]
        if len(hits) != 1:
            have = sorted({p["name"] for p in inst["pins"].values()})
            raise SystemExit(f"{ref} pin {name!r} matched {len(hits)}. Names: {have}")
        return hits[0]

    def _outward(self, ang):
        a = int(round(ang)) % 360
        return {0: (-1.0, 0.0), 180: (1.0, 0.0), 90: (0.0, 1.0), 270: (0.0, -1.0)}[a]

    def stub(self, pin, length=5.08):
        x, y = pin["world"]
        dx, dy = self._outward(pin["ang"])
        end = (round(x + dx * length, 4), round(y + dy * length, 4))
        start = (round(x, 4), round(y, 4))
        if start != end:
            self.wires.append((start, end))
        return end, (dx, dy)

    def label_pin(self, ref, pin_id, net, unit=None, length=5.08):
        pin = self.pin(ref, pin_id, unit) if not isinstance(pin_id, dict) else pin_id
        if isinstance(pin_id, str) and not pin_id.isdigit() and not isinstance(pin_id, dict):
            pass
        end, (dx, dy) = self.stub(pin, length)
        if dx < 0:
            justify = "right bottom"
        elif dx > 0:
            justify = "left bottom"
        else:
            justify = "left bottom"
        self.labels.append((end, net, justify))
        return end

    def label_named(self, ref, name, net, unit=None, length=5.08):
        return self.label_pin(ref, self.pin_named(ref, name, unit), net, length=length)

    def power_on(self, ref, pin_or_name, kind, unit=None, length=3.81):
        if isinstance(pin_or_name, str) and not str(pin_or_name).isdigit():
            pin = self.pin_named(ref, pin_or_name, unit)
        else:
            pin = self.pin(ref, pin_or_name, unit)
        end, _ = self.stub(pin, length)
        self.power_at(*end, kind)

    def power_at(self, x, y, kind):
        self._pwr += 1
        ref = f"#PWR{self._pwr:03d}"
        lib = {"+5V": "power:+5V", "GND": "power:GND"}[kind]
        pins = collect_pins(self.syms[lib])
        return self.place(ref, lib, kind, x, y, 1, "", pins, power=True)

    def flag(self, x, y):
        self._flg += 1
        ref = f"#FLG{self._flg:02d}"
        lib = "power:PWR_FLAG"
        pins = collect_pins(self.syms[lib])
        return self.place(ref, lib, "PWR_FLAG", x, y, 1, "", pins, power=True)

    def tie(self, a, b):
        self.wires.append(
            (tuple(round(c, 4) for c in a), tuple(round(c, 4) for c in b))
        )

    def bus_to_power(self, pins, kind, length=7.62):
        """Join pin stubs with one straight rail and a single power symbol.

        Adjacent pins otherwise stack a power symbol on every pin. The rail
        stays on the stub ends, past the shorter net-label stubs.
        """
        ends = []
        for pin in pins:
            end, _ = self.stub(pin, length)
            ends.append(end)
        xs = {e[0] for e in ends}
        ys = {e[1] for e in ends}
        if len(xs) == 1:
            x = ends[0][0]
            ordered = sorted(ys)
            # A stub that lands in the middle of one long wire is not a
            # connection. Each tap has to be the end of its own segment.
            for y_a, y_b in zip(ordered, ordered[1:]):
                self.wires.append(((x, y_a), (x, y_b)))
            for y in ordered:
                self.junctions.append((x, y))
            # +5V draws upward from its pin. GND draws downward.
            self.power_at(x, ordered[0] if kind == "+5V" else ordered[-1], kind)
        elif len(ys) == 1:
            y = ends[0][1]
            ordered = sorted(xs)
            for x_a, x_b in zip(ordered, ordered[1:]):
                self.wires.append(((x_a, y), (x_b, y)))
            for x in ordered:
                self.junctions.append((x, y))
            self.power_at(ordered[0] if kind == "+5V" else ordered[-1], y, kind)
        else:
            raise SystemExit(f"power rail for {kind} is not straight: {ends}")

    def nc(self, ref, pin_or_name, unit=None):
        if isinstance(pin_or_name, str) and not str(pin_or_name).isdigit():
            pin = self.pin_named(ref, pin_or_name, unit)
        else:
            pin = self.pin(ref, pin_or_name, unit)
        x, y = pin["world"]
        self.ncs.append((round(x, 4), round(y, 4)))

    def text(self, s, x, y, size=1.27):
        self.texts.append((s, x, y, size))


def q(s):
    return ("str", s)


def a(s):
    return ("atom", s)


def emit_property(name, value, x, y, hide=False):
    node = [
        a("property"), q(name), q(value),
        [a("at"), a(fmt(x)), a(fmt(y)), a("0")],
        [a("show_name"), a("no")],
        [a("do_not_autoplace"), a("yes")],
    ]
    if hide:
        node.append([a("hide"), a("yes")])
    node.append(
        [a("effects"), [a("font"), [a("size"), a("1.27"), a("1.27")]]]
    )
    return node


def fmt(n):
    s = f"{float(n):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def emit_instance(p):
    node = [
        a("symbol"),
        [a("lib_id"), q(p["lib_id"])],
        [a("at"), a(fmt(p["x"])), a(fmt(p["y"])), a("0")],
        [a("unit"), a(str(p["unit"]))],
        [a("body_style"), a("1")],
        [a("exclude_from_sim"), a("no")],
        [a("in_bom"), a("yes" if p["bom"] else "no")],
        [a("on_board"), a("yes" if p["on_board"] else "no")],
        [a("in_pos_files"), a("yes" if p["pos"] else "no")],
        [a("dnp"), a("no")],
        [a("uuid"), q(p["uuid"])],
    ]
    # Reference above, value below, using pin span when there is one.
    ys = [pin["world"][1] for pin in p["pins"].values()]
    top = min(ys) - 2.54 if ys else p["y"] - 5
    bot = max(ys) + 2.54 if ys else p["y"] + 5
    node.append(emit_property("Reference", p["ref"], p["x"], top, hide=p["power"]))
    node.append(emit_property("Value", p["value"], p["x"], bot, hide=p["power"]))
    node.append(emit_property("Footprint", p["footprint"], p["x"], p["y"], hide=True))
    node.append(emit_property("Datasheet", "", p["x"], p["y"], hide=True))
    node.append(emit_property("Description", "", p["x"], p["y"], hide=True))
    for pin in p["pins"].values():
        node.append([a("pin"), q(pin["num"]), [a("uuid"), q(pin["uuid"])]])
    node.append(
        [a("instances"),
         [a("project"), q(PROJECT),
          [a("path"), q("/"),
           [a("reference"), q(p["ref"])],
           [a("unit"), a(str(p["unit"]))]]]]
    )
    return node


def build():
    sh = Sheet()
    parts = {
        "cpu": prepare("CPU", "Z84C00xxP", parent="Z084000xP"),
        "ram": prepare("Memory_RAM", "HM62256BLP", parent="KM62256CLP"),
        "rom": prepare("Memory_EPROM", "27C256"),
        "pio": prepare("Interface", "82C55A", parent="8255"),
        "uart": prepare("Interface_UART", "16550"),
        "max": prepare("Interface_UART", "MAX232"),
        "inv": prepare("74xx", "74HC14"),
        "or": prepare("74xx", "74LS32"),
        "p5": prepare("power", "+5V"),
        "gnd": prepare("power", "GND"),
        "flg": prepare("power", "PWR_FLAG"),
        "r": prepare("Device", "R"),
        "c": prepare("Device", "C"),
        "cp": prepare("Device", "C_Polarized"),
        "y14": prepare("Oscillator", "CXO_DIP14"),
        "y8": prepare("Oscillator", "CXO_DIP8"),
        "sw": prepare("Switch", "SW_Push"),
        "de9": prepare("Connector", "DE9_Socket"),
        "hdr": prepare("Connector_Generic", "Conn_01x08"),
        "screw": prepare("Connector", "Screw_Terminal_01x02"),
        "hole": prepare("Mechanical", "MountingHole"),
    }
    pins = {k: sh.use(v) for k, v in parts.items()}

    # Centers, millimetres, Y down. A3 landscape is 420 x 297.
    # A4 was the asked page. It does not fit: a DIP-40 symbol is about
    # 100 mm tall, so two cannot stack on 210 mm, and one row of the five
    # large chips does not fit across 297 mm. The plan says stop at A3.
    sh.place("U1", "CPU:Z84C00xxP", "Z84C00", 55, 115, 1,
             "Package_DIP:DIP-40_W15.24mm", pins["cpu"])
    sh.place("U2", "Memory_RAM:HM62256BLP", "62256", 125, 100, 1,
             "Package_DIP:DIP-28_W15.24mm", pins["ram"])
    sh.place("U3", "Memory_EPROM:27C256", "27C256", 185, 105, 1,
             "Package_DIP:DIP-28_W15.24mm", pins["rom"])
    sh.place("U4", "Interface:82C55A", "82C55A", 140, 225, 1,
             "Package_DIP:DIP-40_W15.24mm", pins["pio"])
    sh.place("U5", "Interface_UART:16550", "16C550", 275, 155, 1,
             "Package_DIP:DIP-40_W15.24mm", pins["uart"])
    sh.place("U6", "Interface_UART:MAX232", "MAX232", 365, 120, 1,
             FP_DIP16, pins["max"])
    sh.place("Y1", "Oscillator:CXO_DIP14", "4MHz", 55, 185, 1,
             "Oscillator:Oscillator_DIP-14", pins["y14"])
    sh.place("Y2", "Oscillator:CXO_DIP8", "1.8432MHz", 275, 235, 1,
             "Oscillator:Oscillator_DIP-8", pins["y8"])
    sh.place("J1", "Connector:DE9_Socket", "DE9", 365, 210, 1, FP_DE9, pins["de9"])

    # 74HC14 units 1..6 are gates, 7 is power. Value says HCT: same pinout,
    # and the 10.0.7 library has no separate 74HCT14 symbol.
    for unit, xy in (
        (1, (40, 218)),   # U7A reset in
        (2, (95, 218)),   # U7B reset out
        (3, (280, 32)),   # U7C A7
        (4, (130, 32)),   # U7D A15
        (5, (185, 270)),  # U7E spare
        (6, (230, 270)),  # U7F spare
        (7, (395, 170)),  # power, above the title block
    ):
        sh.place("U7", "74xx:74HC14", "74HCT14", xy[0], xy[1], unit, FP_DIP14, pins["inv"])

    # 74LS32 is the only quad OR in the 10.0.7 library with this pinout.
    # Value is the part the GIF calls for.
    for unit, xy in (
        (1, (330, 32)),   # U8A PIO
        (2, (380, 32)),   # U8B UART
        (3, (225, 32)),   # U8C ROM
        (4, (175, 32)),   # U8D RAM
        (5, (395, 206)),  # power, above the title block
    ):
        sh.place("U8", "74xx:74LS32", "74HCT32", xy[0], xy[1], unit, FP_DIP14, pins["or"])

    # --- Z80 ---
    for bit in range(16):
        sh.label_named("U1", f"A_{{{bit}}}", f"A{bit}")
    for bit in range(8):
        sh.label_named("U1", f"D_{{{bit}}}", f"D{bit}")
    for name, net in (
        ("~{MREQ}", "~{MREQ}"),
        ("~{IORQ}", "~{IORQ}"),
        ("~{RD}", "~{RD}"),
        ("~{WR}", "~{WR}"),
        ("~{RESET}", "~{RESET}"),
        ("CLK", "CLK"),
    ):
        sh.label_named("U1", name, net)
    # INT and NMI are one pin apart, so they share a rail instead of two
    # stacked +5V symbols. WAIT and BUSREQ each have a no-connect beside them.
    sh.bus_to_power(
        [sh.pin_named("U1", "~{INT}"), sh.pin_named("U1", "~{NMI}")],
        "+5V",
        length=7.62,
    )
    sh.power_on("U1", "~{WAIT}", "+5V", length=7.62)
    sh.power_on("U1", "~{BUSREQ}", "+5V", length=7.62)
    for name in ("~{HALT}", "~{M1}", "~{RFSH}", "~{BUSACK}"):
        sh.nc("U1", name)
    sh.power_on("U1", "V_{CC}", "+5V")
    sh.power_on("U1", "GND", "GND")

    # --- RAM U2, the GIF's U2. Data pins are Q0..Q7. ---
    for bit in range(15):
        sh.label_named("U2", f"A{bit}", f"A{bit}")
    for bit in range(8):
        sh.label_named("U2", f"Q{bit}", f"D{bit}")
    sh.label_named("U2", "~{CS}", "~{RAMCS}")
    sh.label_named("U2", "~{OE}", "~{RD}")
    sh.label_named("U2", "~{WE}", "~{WR}")
    sh.power_on("U2", "VCC", "+5V")
    sh.power_on("U2", "GND", "GND")

    # --- EPROM U3. 27C256 pin 1 is VPP, not A15. ---
    for bit in range(15):
        sh.label_named("U3", f"A{bit}", f"A{bit}")
    for bit in range(8):
        sh.label_named("U3", f"D{bit}", f"D{bit}")
    sh.label_named("U3", "~{CE}", "~{ROMCS}")
    sh.label_named("U3", "~{OE}", "~{RD}")
    sh.power_on("U3", "VPP", "+5V", length=7.62)
    sh.power_on("U3", "VCC", "+5V")
    sh.power_on("U3", "GND", "GND")

    # --- PIO ---
    for bit in range(8):
        sh.label_named("U4", f"D{bit}", f"D{bit}")
        sh.label_named("U4", f"PA{bit}", f"PA{bit}", length=10.16)
        sh.label_named("U4", f"PB{bit}", f"PB{bit}", length=10.16)
        sh.label_named("U4", f"PC{bit}", f"PC{bit}", length=10.16)
    sh.label_named("U4", "A0", "A0")
    sh.label_named("U4", "A1", "A1")
    sh.label_named("U4", "~{RD}", "~{RD}")
    sh.label_named("U4", "~{WR}", "~{WR}")
    sh.label_named("U4", "~{CS}", "~{PIOCS}")
    sh.label_named("U4", "RESET", "RESET_H")
    sh.power_on("U4", "VCC", "+5V")
    sh.power_on("U4", "GND", "GND")
    # The GIF leaves the PIO ports open. A header gives each label a second pin
    # so the net is a real connector instead of a dangling name.
    for ref, prefix, xy in (
        ("J2", "PA", (230, 195)),
        ("J3", "PB", (230, 225)),
        ("J4", "PC", (230, 255)),
    ):
        sh.place(ref, "Connector_Generic:Conn_01x08", prefix, xy[0], xy[1], 1, FP_HDR, pins["hdr"])
        for bit in range(8):
            sh.label_pin(ref, str(bit + 1), f"{prefix}{bit}")

    # --- UART. /WR and /RD are the Z80 strobes; WR and RD tied inactive. ---
    for bit in range(8):
        sh.label_named("U5", f"D{bit}", f"D{bit}")
    for name, net in (
        ("A0", "A0"), ("A1", "A1"), ("A2", "A2"),
        ("~{CS2}", "~{UARTCS}"),
        ("~{RD}", "~{RD}"),
        ("~{WR}", "~{WR}"),
        ("XIN", "CLK_UART"),
        ("~{BAUDOUT}", "BAUDOUT"),
        ("RCLK", "BAUDOUT"),
        ("SIN", "RX_TTL"),
        ("SOUT", "TX_TTL"),
        ("~{DTR}", "DTR_TTL"),
        ("~{DSR}", "DSR_TTL"),
        ("MR", "RESET_H"),
    ):
        sh.label_named("U5", name, net)
    # CS0/CS1 are adjacent. WR, RD and /ADS are the same column, with /RD's
    # label between RD and /ADS, so that rail sits further out than the labels.
    sh.bus_to_power(
        [sh.pin_named("U5", "CS0"), sh.pin_named("U5", "CS1")],
        "+5V",
        length=7.62,
    )
    sh.bus_to_power(
        [sh.pin_named("U5", "WR"), sh.pin_named("U5", "RD"), sh.pin_named("U5", "~{ADS}")],
        "GND",
        length=10.16,
    )
    for name in ("XOUT", "DDIS", "~{TXRDY}", "~{RXRDY}", "INTR",
                 "~{OUT1}", "~{OUT2}", "~{RTS}", "~{CTS}", "~{DCD}", "~{RI}"):
        sh.nc("U5", name)
    sh.power_on("U5", "VCC", "+5V")
    sh.power_on("U5", "GND", "GND")

    # --- MAX232. Charge-pump caps follow the datasheet, not a misread of the GIF. ---
    for name, net in (
        ("T1IN", "TX_TTL"),
        ("T1OUT", "TX_RS232"),
        ("R1IN", "RX_RS232"),
        ("R1OUT", "RX_TTL"),
        ("T2IN", "DTR_TTL"),
        ("T2OUT", "DTR_RS232"),
        ("R2IN", "DSR_RS232"),
        ("R2OUT", "DSR_TTL"),
    ):
        sh.label_named("U6", name, net)
    sh.power_on("U6", "VCC", "+5V")
    sh.power_on("U6", "GND", "GND")

    def polarized(ref, x, y, net_plus, net_minus):
        sh.place(ref, "Device:C_Polarized", "1uF", x, y, 1, FP_CP, pins["cp"])
        # pin 1 is the positive end, at the top of the symbol (local +Y)
        sh.label_named(ref, "", net_plus) if False else None
        # C_Polarized pins are unnamed. Pin 1 is +, pin 2 is -.
        end_p, _ = sh.stub(sh.pin(ref, "1"), 2.54)
        end_m, _ = sh.stub(sh.pin(ref, "2"), 2.54)
        # The stub of a vertical cap goes up from pin 1 and down from pin 2.
        # Drop a power symbol or a label there.
        if net_plus in ("+5V", "GND"):
            sh.power_at(*end_p, net_plus)
        else:
            sh.labels.append((end_p, net_plus, "left bottom"))
        if net_minus in ("+5V", "GND"):
            sh.power_at(*end_m, net_minus)
        else:
            sh.labels.append((end_m, net_minus, "left bottom"))

    # C1+ to C1-, positive at C1+. Local labels, wires already on the stubs.
    polarized("C20", 320, 78, "C1P", "C1M")
    polarized("C21", 400, 78, "C2P", "C2M")
    polarized("C22", 365, 62, "+5V", "GND")  # extra 1uF across the rail
    # Fix C22: it should be VS+ to GND, not +5V. Re-label by not using that.
    # Easier to place VS caps explicitly after removing the mistaken C22 nets.
    # C22 was just created with +5V/GND. Change those two labels/power calls:
    # polarized already emitted them. I'll place VS caps as their own and
    # not use C22 for +5V. See below: C22 is redone by direct pin labels.

    # The helper above already wired C22 to +5V/GND. That cap is still a
    # valid 1uF across the rail; use three more for the pump and V-.
    # Rewire pump pins with labels matching C20/C21, and add V+ / V- caps.
    sh.label_named("U6", "C1+", "C1P")
    sh.label_named("U6", "C1-", "C1M")
    sh.label_named("U6", "C2+", "C2P")
    sh.label_named("U6", "C2-", "C2M")
    sh.label_named("U6", "VS+", "VS_PLUS")
    sh.label_named("U6", "VS-", "VS_MINUS")
    polarized("C23", 400, 118, "VS_PLUS", "GND")
    # V- cap: positive at GND, negative at VS-.
    polarized("C24", 400, 138, "GND", "VS_MINUS")

    # --- DE9 as DTE ---
    for num, net in (("2", "RX_RS232"), ("3", "TX_RS232"), ("4", "DTR_RS232"),
                     ("5", "GND"), ("6", "DSR_RS232")):
        if net in ("+5V", "GND"):
            sh.power_on("J1", num, net)
        else:
            sh.label_pin("J1", num, net)
    for num in ("1", "7", "8", "9"):
        sh.nc("J1", num)

    # --- clocks ---
    sh.label_named("Y1", "OUT", "CLK")
    sh.power_on("Y1", "EN", "+5V")
    sh.power_on("Y1", "GND", "GND")
    sh.power_on("Y1", "Vcc", "+5V")
    sh.label_named("Y2", "OUT", "CLK_UART")
    sh.power_on("Y2", "EN", "+5V")
    sh.power_on("Y2", "GND", "GND")
    sh.power_on("Y2", "Vcc", "+5V")

    # --- address decode, matching the GIF's gate pins and the printed map ---
    # U7D: A15 -> /A15. U8D: /MREQ OR /A15 = /RAMCS. U8C: /MREQ OR A15 = /ROMCS.
    sh.label_pin("U7", "9", "A15", unit=4)
    sh.label_pin("U7", "8", "~{A15}", unit=4)
    sh.label_pin("U8", "12", "~{MREQ}", unit=4)
    sh.label_pin("U8", "13", "~{A15}", unit=4)
    sh.label_pin("U8", "11", "~{RAMCS}", unit=4)
    sh.label_pin("U8", "9", "~{MREQ}", unit=3)
    sh.label_pin("U8", "10", "A15", unit=3)
    sh.label_pin("U8", "8", "~{ROMCS}", unit=3)
    # U7C: A7 -> /A7. U8A: /IORQ OR A7 = /PIOCS (A7 low). U8B: /IORQ OR /A7 = /UARTCS.
    sh.label_pin("U7", "5", "A7", unit=3)
    sh.label_pin("U7", "6", "~{A7}", unit=3)
    sh.label_pin("U8", "1", "~{IORQ}", unit=1)
    sh.label_pin("U8", "2", "A7", unit=1)
    sh.label_pin("U8", "3", "~{PIOCS}", unit=1)
    sh.label_pin("U8", "4", "~{IORQ}", unit=2)
    sh.label_pin("U8", "5", "~{A7}", unit=2)
    sh.label_pin("U8", "6", "~{UARTCS}", unit=2)

    # --- reset. Two Schmitt inversions. Active-high parts take the first. ---
    sh.place("R1", "Device:R", "10k", 40, 248, 1, FP_R, pins["r"])
    sh.place("R2", "Device:R", "10", 78, 248, 1, FP_R, pins["r"])
    sh.place("C1", "Device:C_Polarized", "1uF", 40, 268, 1, FP_CP, pins["cp"])
    sh.place("SW1", "Switch:SW_Push", "RESET", 118, 248, 1, FP_SW, pins["sw"])
    # R1 pin 1 top toward +5V, pin 2 bottom toward RESET_RC
    sh.power_on("R1", "1", "+5V", length=2.54)
    sh.label_pin("R1", "2", "RESET_RC", length=2.54)
    sh.label_pin("C1", "1", "RESET_RC", length=2.54)
    sh.power_on("C1", "2", "GND", length=2.54)
    sh.label_pin("R2", "1", "RESET_RC", length=2.54)
    sh.label_pin("R2", "2", "SW_RESET", length=2.54)
    sh.label_pin("SW1", "1", "SW_RESET", length=2.54)
    sh.power_on("SW1", "2", "GND", length=2.54)
    sh.label_pin("U7", "1", "RESET_RC", unit=1)
    sh.label_pin("U7", "2", "RESET_H", unit=1)
    sh.label_pin("U7", "3", "RESET_H", unit=2)
    sh.label_pin("U7", "4", "~{RESET}", unit=2)

    # spare inverters: input held, output unused
    sh.power_on("U7", "11", "GND", unit=5)
    sh.nc("U7", "10", unit=5)
    sh.power_on("U7", "13", "GND", unit=6)
    sh.nc("U7", "12", unit=6)
    sh.power_on("U7", "14", "+5V", unit=7)
    sh.power_on("U7", "7", "GND", unit=7)
    sh.power_on("U8", "14", "+5V", unit=5)
    sh.power_on("U8", "7", "GND", unit=5)

    # decoupling, one 100nF from +5V to GND per package, as on the GIF
    for i, (ref, x) in enumerate(
        [("C2", 18), ("C3", 30), ("C4", 42), ("C5", 54), ("C6", 66),
         ("C7", 78), ("C8", 90), ("C9", 102), ("C10", 114), ("C11", 126)],
        start=1,
    ):
        sh.place(ref, "Device:C", "100nF", x, 58, 1, FP_C, pins["c"])
        sh.power_on(ref, "1", "+5V", length=2.54)
        sh.power_on(ref, "2", "GND", length=2.54)

    # one PWR_FLAG on each rail, on the 1.27 mm grid
    flg_hi = sh.flag(18, 78)
    pwr_hi = sh.power_at(18, 78 - 2.54, "+5V")
    sh.tie(flg_hi["pins"]["1"]["world"], pwr_hi["pins"]["1"]["world"])
    flg_lo = sh.flag(30, 78)
    pwr_lo = sh.power_at(30, 78 + 2.54, "GND")
    sh.tie(flg_lo["pins"]["1"]["world"], pwr_lo["pins"]["1"]["world"])

    notes = [
        "Simple Z80, redrawn from the z80.info test schematic. Symbols are stock KiCad 10.0.7.",
        "U3 is a 27C256, not the drawing's 27C512. Pin 1 is VPP, tied to +5V. A15 only feeds the decoder.",
        "U1 is the Z84C00 symbol (CMOS Z80). U2 is HM62256, value 62256.",
        "No 74HCT14 or 74HCT32 in this library. U7 is a 74HC14 symbol, U8 is a 74LS32 symbol, values set to the HCT parts.",
        "PIO answers any I/O cycle with A7 low (use 00-03). UART answers any with A7 high (use 80-87).",
        "8255 RESET and 16550 MR are active high, after one inverter. Z80 /RESET is after the second.",
    ]
    for i, line in enumerate(notes):
        sh.text(line, 16, 14 + i * 2.6, 1.27)
    sh.text("C2-C11: 100nF, one per IC, drawn here rather than beside each chip.", 140, 48, 1.27)

    # Power inlet and the four board holes. They are not on the GIF.
    # J5 is the screw terminal: pin 1 is +5V, pin 2 is GND.
    FP_J5 = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal"
    FP_HOLE = "MountingHole:MountingHole_3.2mm_M3"
    sh.place("J5", "Connector:Screw_Terminal_01x02", "PWR", 400, 52, 1, FP_J5, pins["screw"])
    sh.power_on("J5", "1", "+5V")
    sh.power_on("J5", "2", "GND")
    for i, ref in enumerate(("H1", "H2", "H3", "H4")):
        sh.place(ref, "Mechanical:MountingHole", "M3", 214 + i * 16, 18, 1,
                 FP_HOLE, pins["hole"], mechanical=True)
    return sh


def emit(sh: Sheet) -> str:
    lines = []
    lines.append("(kicad_sch")
    lines.append("  (version 20260306)")
    lines.append('  (generator "eeschema")')
    lines.append('  (generator_version "10.0")')
    lines.append(f'  (uuid "{SHEET_UUID}")')
    lines.append('  (paper "A3")')
    lines.append("  (title_block")
    lines.append('    (title "Z80 Simple Educational Computer")')
    lines.append('    (date "2026-11-03")')
    lines.append('    (rev "1.1")')
    lines.append('    (company "Educational Project")')
    lines.append('    (comment 1 "Stock KiCad 10.0.7 symbols. Source drawing: z80.info z80test.gif")')
    lines.append('    (comment 2 "27C256 in place of 27C512. U2 RAM 8000-FFFF, U3 ROM 0000-7FFF")')
    lines.append('    (comment 3 "U7 symbol 74HC14 value 74HCT14. U8 symbol 74LS32 value 74HCT32")')
    lines.append('    (comment 4 "Through-hole. Sheet is wired. Board is not routed yet.")')
    lines.append("  )")
    lines.append("  (lib_symbols")
    for sym in sh.syms.values():
        dumped = dump(sym)
        lines.append("    " + dumped)
    lines.append("  )")
    for p in sh.placed:
        lines.append("  " + dump(emit_instance(p)))
    for (x1, y1), (x2, y2) in sh.wires:
        lines.append(
            "  (wire (pts (xy {0} {1}) (xy {2} {3})) "
            "(stroke (width 0) (type default)) (uuid \"{4}\"))".format(
                fmt(x1), fmt(y1), fmt(x2), fmt(y2), uuid.uuid4()
            )
        )
    for x, y in sh.junctions:
        lines.append(
            "  (junction (at {0} {1}) (diameter 0) (color 0 0 0 0) "
            "(uuid \"{2}\"))".format(fmt(x), fmt(y), uuid.uuid4())
        )
    for (x, y), net, justify in sh.labels:
        lines.append(
            "  (label \"{net}\" (at {x} {y} 0) "
            "(effects (font (size 1.27 1.27)) (justify {j})) "
            "(uuid \"{u}\"))".format(
                net=net.replace('"', '\\"'), x=fmt(x), y=fmt(y), j=justify, u=uuid.uuid4()
            )
        )
    for x, y in sh.ncs:
        lines.append(
            f'  (no_connect (at {fmt(x)} {fmt(y)}) (uuid "{uuid.uuid4()}"))'
        )
    for text, x, y, size in sh.texts:
        esc = text.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(
            f'  (text "{esc}" (exclude_from_sim no) (at {fmt(x)} {fmt(y)} 0) '
            f'(effects (font (size {fmt(size)} {fmt(size)})) (justify left bottom)) '
            f'(uuid "{uuid.uuid4()}"))'
        )
    lines.append("  (sheet_instances")
    lines.append('    (path "/" (page "1"))')
    lines.append("  )")
    lines.append("  (embedded_fonts no)")
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


def main():
    sh = build()
    OUT.write_text(emit(sh))
    print(f"wrote {OUT}")
    print(f"symbols {len(sh.syms)} placed {len(sh.placed)} wires {len(sh.wires)} labels {len(sh.labels)}")


if __name__ == "__main__":
    main()
