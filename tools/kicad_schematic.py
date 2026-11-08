#!/usr/bin/env python3
"""Low-level KiCad symbol and S-expression helpers adapted from Grok/AGY.

Source: reference/grok-agy/tools/build_schematic.py (2026-11-06 snapshot).
Circuit connectivity and sheet layout are defined separately by Codex.

Load stock KiCad 10.0.7 symbols.

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
SHEET_UUID = str(uuid.uuid5(uuid.NAMESPACE_URL, "andrew-codex-z80/sheet"))

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
            "uuid": str(uuid.uuid5(uuid.UUID(SHEET_UUID), f"symbol/{ref}/{unit}")),
            "pins": {},
        }
        for rec in chosen:
            world = self._world(inst, rec)
            inst["pins"][rec["num"]] = {**rec, "world": world, "uuid": str(uuid.uuid5(uuid.UUID(inst["uuid"]), "pin/" + rec["num"]))}
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

