#!/usr/bin/env python3
"""Freeze the historical FirstZ80 placement; normalize proven name-only changes.

Native source files remain untouched. Embedded legacy library geometry is
snapshotted locally rather than substituted with today's stock libraries.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
import pcbnew as pcb
import kicad_schematic as k
from check_wired_schematic import native

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'reference/firstz80/66a5a63'
BASE = ROOT / 'firstz80'
REPORT = ROOT / 'reports/firstz80'
NAME = 'FirstZ80'


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def geometry(board):
    return {fp.GetReference(): [fp.GetPosition().x, fp.GetPosition().y,
            fp.GetOrientationDegrees(), fp.GetValue(),
            sorted([p.GetNumber(), p.GetPosition().x, p.GetPosition().y,
                    p.GetSize().x, p.GetSize().y, p.GetDrillSize().x,
                    p.GetDrillSize().y, p.GetOrientationDegrees(), int(p.GetShape()),
                    p.GetLayerSet().FmtHex()] for p in fp.Pads())]
            for fp in board.GetFootprints()}


def project_folder(folder):
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / 'libs'
    if not link.exists():
        link.symlink_to(os.path.relpath(BASE / 'libs', folder), target_is_directory=True)
    for kind, table in [('sym', 'sym_lib_table'), ('fp', 'fp_lib_table')]:
        uri = '${KIPRJMOD}/libs/FirstZ80.kicad_sym' if kind == 'sym' else '${KIPRJMOD}/libs/FirstZ80.pretty'
        (folder / f'{kind}-lib-table').write_text(f'({table}\n (lib (name "FirstZ80")(type "KiCad")(uri "{uri}")(options "")(descr "Historical FirstZ80 embedded library snapshot"))\n)\n')


def command(args, log=None):
    run = subprocess.run(args, capture_output=True, text=True)
    if log:
        log.write_text(run.stdout + run.stderr)
    assert run.returncode == 0, run.stdout + run.stderr


def main():
    checkpoint = BASE / 'checkpoint'
    assert not (checkpoint / 'checkpoint.json').exists(), 'Existing frozen input: do not overwrite'
    REPORT.mkdir(parents=True, exist_ok=True)
    libs = BASE / 'libs'
    libs.mkdir(parents=True, exist_ok=True)
    (libs / 'FirstZ80.pretty').mkdir(exist_ok=True)
    source_board = pcb.LoadBoard(str(SOURCE / f'{NAME}.kicad_pcb'))
    before = geometry(source_board)
    source_xml = REPORT / 'source-netlist.xml'
    command(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(source_xml),str(SOURCE / f'{NAME}.kicad_sch')])
    expected, components = native(source_xml)
    datasheets={c.attrib['ref']:c.findtext('datasheet','') for c in ET.parse(source_xml).findall('.//components/comp')}
    pads = {(fp.GetReference(),p.GetNumber()):p.GetNetname()
            for fp in source_board.GetFootprints() for p in fp.Pads()}
    assert set(expected) <= set(pads), set(expected)-set(pads)
    mapping = {}
    for key, new in expected.items():
        old = pads[key]
        assert old, key
        assert mapping.setdefault(old,new) == new, (key,old,new)
    reverse = {}
    for old,new in mapping.items():
        assert reverse.setdefault(new,old) == old, (old,new)
    # This bijection proves equal partitions, not merely similar net counts.
    extras = {str(key):net for key,net in pads.items() if key not in expected and net}
    for net in source_board.GetNetsByNetcode().values():
        if net.GetNetname() in mapping:
            net.SetNetname(mapping[net.GetNetname()])
    fpnames = {}
    for fp in source_board.GetFootprints():
        old = fp.GetFPID().GetUniStringLibId()
        new = old.replace(':','__')
        fpnames[old] = 'FirstZ80:' + new
        sample = pcb.FOOTPRINT(fp)
        sample.SetOrientationDegrees(0)
        sample.SetPosition(pcb.VECTOR2I(0,0))
        sample.SetFPID(pcb.LIB_ID('FirstZ80',new))
        pcb.PCB_IO_MGR.FindPlugin(pcb.PCB_IO_MGR.KICAD_SEXP).FootprintSave(str(libs/'FirstZ80.pretty'),sample)
        fp.SetFPID(pcb.LIB_ID('FirstZ80',new))
        field=fp.GetField(pcb.FIELD_T_DATASHEET)
        if field:field.SetText(datasheets.get(fp.GetReference(),''))
    sch = k.parse((SOURCE / f'{NAME}.kicad_sch').read_text())
    definitions = k.kid(sch,'lib_symbols')
    symbol_names = {}
    library = []
    for definition in k.kids(definitions,'symbol'):
        old = k.atom(definition[1]); bare = old.split(':')[1]
        new = bare
        symbol_names[old] = 'FirstZ80:' + new
        def rename(node):
            if not isinstance(node,list): return
            if node and k.atom(node[0])=='symbol':
                name = k.atom(node[1])
                if name.startswith(bare+'_'):node[1]=('str',new+name[len(bare):])
            for child in node:rename(child)
        rename(definition)
        definition[1] = ('str','FirstZ80:'+new)
        libcopy = copy.deepcopy(definition);libcopy[1]=('str',new)
        library.append(libcopy)
    (libs/'FirstZ80.kicad_sym').write_text('(kicad_symbol_lib (version 20211014) (generator "codex_snapshot")\n'+'\n'.join(k.dump(s) for s in library)+'\n)\n')
    for sym in k.kids(sch,'symbol'):
        lid=k.kid(sym,'lib_id');lid[1]=('str',symbol_names[k.atom(lid[1])])
        for prop in k.kids(sym,'property'):
            if k.atom(prop[1])=='Footprint' and k.atom(prop[2]):
                prop[2]=('str',fpnames[k.atom(prop[2])])
    def update_legacy_instances(node):
        if not isinstance(node,list):return
        if node and k.atom(node[0])=='footprint' and k.atom(node[1]) in fpnames:
            node[1]=('str',fpnames[k.atom(node[1])])
        for child in node:update_legacy_instances(child)
    update_legacy_instances(sch)
    project_folder(checkpoint)
    (checkpoint/f'{NAME}.kicad_sch').write_text(k.dump(sch)+'\n')
    shutil.copy2(SOURCE/f'{NAME}.kicad_pro',checkpoint/f'{NAME}.kicad_pro')
    original_track_count=len(source_board.GetTracks())
    original_vias=sum(t.Type()==pcb.PCB_VIA_T for t in source_board.GetTracks())
    # Save the normalized routed reference, too: useful as a migration control.
    reference=BASE/'reference-normalized';project_folder(reference)
    for suffix in ('.kicad_sch','.kicad_pro'):
        shutil.copy2(checkpoint/(NAME+suffix),reference/(NAME+suffix))
    source_board.Save(str(reference/f'{NAME}.kicad_pcb'))
    # Strip serialized copper, avoiding ownership transfer of 1,496 SWIG items.
    board_tree=k.parse((reference/f'{NAME}.kicad_pcb').read_text())
    board_tree[:]=[item for item in board_tree if not (isinstance(item,list) and k.atom(item[0]) in ('segment','via','zone','arc'))]
    (checkpoint/f'{NAME}.kicad_pcb').write_text(k.dump(board_tree)+'\n')
    source_board=pcb.LoadBoard(str(checkpoint/f'{NAME}.kicad_pcb'))
    assert geometry(source_board)==before
    source_board.Save(str(checkpoint/f'{NAME}.kicad_pcb'))
    result = dict(source_commit='66a5a63fd8338d12c115bc2bee5dff2f435afae4',
                  source_files={p.name:digest(p) for p in SOURCE.iterdir() if p.suffix in ('.kicad_sch','.kicad_pcb','.kicad_pro','.jpg','.rules')},
                  source_original_tracks=original_track_count-original_vias,source_original_vias=original_vias,
                  components=len(components),pin_assignments=len(expected),extra_board_pads=extras,
                  net_partition_identical=True,net_name_changes={a:b for a,b in mapping.items() if a!=b},
                  footprint_geometry=before,nominal_outline_mm=[60,60,220,160],copper_layers=2,
                  source_placement_and_pad_geometry_unchanged=True,
                  stripped_all_copper=True,
                  files_sha256={NAME+s:digest(checkpoint/(NAME+s)) for s in ('.kicad_sch','.kicad_pcb','.kicad_pro')})
    (checkpoint/'checkpoint.json').write_text(json.dumps(result,indent=2)+'\n')
    for method in ('01-manhattan','02-octilinear','03-freerouting'):
        folder=BASE/'layouts'/method;project_folder(folder)
        for suffix in ('.kicad_sch','.kicad_pcb','.kicad_pro'):
            shutil.copy2(checkpoint/(NAME+suffix),folder/(NAME+suffix))
    command(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(REPORT/'normalized-netlist.xml'),str(checkpoint/f'{NAME}.kicad_sch')])
    actual, parts=native(REPORT/'normalized-netlist.xml')
    assert actual==expected
    assert {ref:val[0]for ref,val in parts.items()}=={ref:val[0]for ref,val in components.items()}
    assert all(parts[ref][1]==fpnames[val[1]] for ref,val in components.items())
    print(json.dumps({key:result[key] for key in ('components','pin_assignments','net_partition_identical','source_original_tracks','source_original_vias','extra_board_pads')},indent=2))


if __name__=='__main__':main()
