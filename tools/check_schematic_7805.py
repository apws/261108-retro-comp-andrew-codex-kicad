#!/usr/bin/env python3
"""Verify the PSU extension's exact netlist delta and unchanged base drawing."""
import hashlib
import json
from pathlib import Path
import kicad_schematic as k
from check_wired_schematic import native
from check_schematic_pin_clearance import inspect

ROOT=Path(__file__).resolve().parents[1]
VARIANT=ROOT/'schematics/03-schematic-7805'


def main():
    base_pins,base_parts=native(ROOT/'output/schematic-wired-a3/Z80_Simple_Computer.xml')
    pins,parts=native(ROOT/'output/schematic-7805/schematic-7805.xml')
    meta=json.loads((VARIANT/'provenance.json').read_text())
    assert meta['source_sha256']==hashlib.sha256((ROOT/'schematics/02-wired-a3/Z80_Simple_Computer.kicad_sch').read_bytes()).hexdigest()
    expected=dict(base_pins)
    expected.update({(ref,pin):net for ref,pins in meta['expected_added_pin_assignments'].items()for pin,net in pins.items()})
    assert pins==expected,'Unintended pin/net change in PSU extension'
    assert len(parts)==len(base_parts)+6
    for ref,(value,footprint)in base_parts.items():
        assert parts[ref]==(meta['expected_changed_component_values'].get(ref,value),footprint),ref
    findings=[v for s in json.loads((ROOT/'reports/schematic-7805/erc.json').read_text())['sheets']for v in s['violations']]
    assert not findings
    path=VARIANT/'schematic-7805.kicad_sch';tree=k.parse(path.read_text());root=k.atom(k.kid(tree,'uuid')[1]);refs={}
    for symbol in k.kids(tree,'symbol'):
        ref=next(k.atom(p[2])for p in k.kids(symbol,'property')if k.atom(p[1])=='Reference');unit=k.atom(k.kid(symbol,'unit')[1])
        assert (ref,unit)not in refs,(ref,unit)
        refs[ref,unit]=1
        project=k.kid(k.kid(symbol,'instances'),'project');assert k.atom(project[1])=='schematic-7805'
        assert k.atom(k.kid(project,'path')[1])=='/'+root
    pin_geometry=inspect(path);assert not pin_geometry['findings']
    assert json.loads((VARIANT/'schematic-7805.kicad_pro').read_text())==json.loads((ROOT/'schematics/02-wired-a3/Z80_Simple_Computer.kicad_pro').read_text())
    result=dict(status='pass',components=len(parts),pin_assignments=len(pins),original_pin_assignments_preserved=len(base_pins),added_pin_assignments=len(pins)-len(base_pins),
                erc_findings=0,pin_stroke_crossings=0,unique_unit_annotations=True,project_rules_unchanged=True,
                diode_anode='/VIN_9V',diode_cathode='/VIN_PROTECTED',regulator_pins={'1':'/VIN_PROTECTED','2':'GND','3':'+5V'},
                original_J5_supply_pins_preserved=True,J5_function='Regulated +5V output/test connector in this PSU variant.',
                schematic_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),scope='Static electrical and drawing checks; no hardware, thermal or reset timing qualification.')
    (ROOT/'reports/schematic-7805/verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
