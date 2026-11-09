#!/usr/bin/env python3
"""Independent native gates for all three completed fixed-placement boards."""
import json
from collections import Counter
from pathlib import Path
import pcbnew as pcb
from prepare_firstz80 import ROOT,BASE,REPORT,NAME,geometry,digest,command
from check_wired_schematic import native
from route_netgroup import metrics


def outline(board):
    return sorted((int(x.GetShape()),x.GetStart().x,x.GetStart().y,x.GetEnd().x,x.GetEnd().y,x.GetWidth())for x in board.GetDrawings()if x.GetLayer()==pcb.Edge_Cuts)


def main():
    cp=BASE/'checkpoint';meta=json.loads((cp/'checkpoint.json').read_text())
    for name,h in meta['files_sha256'].items():assert digest(cp/name)==h
    for name,h in meta['source_files'].items():assert digest(ROOT/'reference/firstz80/66a5a63'/name)==h
    original=pcb.LoadBoard(str(cp/f'{NAME}.kicad_pcb'));source=pcb.LoadBoard(str(ROOT/'reference/firstz80/66a5a63'/f'{NAME}.kicad_pcb'))
    assert not original.GetTracks()and not list(original.Zones())
    assert geometry(original)==geometry(source)and outline(original)==outline(source)
    assert original.GetCopperLayerCount()==2
    expected,parts=native(REPORT/'normalized-netlist.xml');assert len(expected)==371 and len(parts)==63
    erc=json.loads((REPORT/'source-normalized-erc.json').read_text());warnings=[v for s in erc['sheets']for v in s['violations']]
    assert all(v['severity']=='warning'for v in warnings)
    library_hashes={str(p.relative_to(BASE)):digest(p)for p in(BASE/'libs').rglob('*')if p.is_file()}
    results={}
    source_copper_uuids={t.m_Uuid.AsString()for t in source.GetTracks()}
    for method in('01-manhattan','02-octilinear','03-freerouting'):
        folder=BASE/'layouts'/method;path=folder/f'{NAME}.kicad_pcb';summary=json.loads((folder/'routing.json').read_text());assert summary['status']=='validated'
        assert digest(path)==summary['board_sha256']
        for ext in('.kicad_sch','.kicad_pro'):assert digest(folder/(NAME+ext))==meta['files_sha256'][NAME+ext]
        report=REPORT/method/'final';report.mkdir(parents=True,exist_ok=True)
        command(['kicad-cli','pcb','drc','--schematic-parity','--severity-all','--all-track-errors','--format','json','-o',str(report/'drc.json'),str(path)],report/'drc-command.log')
        drc=json.loads((report/'drc.json').read_text());assert not any(drc[k]for k in('violations','unconnected_items','schematic_parity'))
        b=pcb.LoadBoard(str(path));assert geometry(b)==geometry(original)and outline(b)==outline(original)
        assert {f.GetReference():f.GetFPID().GetUniStringLibId()for f in b.GetFootprints()}=={f.GetReference():f.GetFPID().GetUniStringLibId()for f in original.GetFootprints()}
        actual={(f.GetReference(),p.GetNumber()):p.GetNetname()for f in b.GetFootprints()for p in f.Pads()if p.GetNetname()}
        assert actual==expected
        assert b.GetCopperLayerCount()==2 and not list(b.Zones())
        assert not source_copper_uuids.intersection(t.m_Uuid.AsString()for t in b.GetTracks())
        widths=Counter()
        for item in b.GetTracks():
            if item.Type()==pcb.PCB_VIA_T:
                assert item.GetDrillValue()==pcb.FromMM(.4)
                assert item.GetWidth(pcb.F_Cu)==pcb.FromMM(.8)and item.GetWidth(pcb.B_Cu)==pcb.FromMM(.8)
            else:
                assert item.GetLayer()in(pcb.F_Cu,pcb.B_Cu)
                width=1.0 if item.GetNetname()in('GND','VCC','/9VDC','/VI','/VCC_PP')else .25
                assert item.GetWidth()==pcb.FromMM(width),(method,item.GetNetname(),pcb.ToMM(item.GetWidth()))
                widths[str(width)]+=1
        result=dict(status='pass',components=63,pin_assignments=371,placement_pad_geometry_outline_and_circuit_unchanged=True,
                    source_copper_reused=False,violations=0,unconnected=0,schematic_parity=0,copper_layers=2,zones=0,
                    widths_mm=dict(widths),clearance_mm=.2,via_diameter_mm=.8,via_drill_mm=.4,hole_clearance_mm=.25,
                    inherited_schematic_warnings=len(warnings),inherited_schematic_warning_types=dict(Counter(v['type']for v in warnings)),
                    board_sha256=digest(path),**metrics(b))
        (report/'verification.json').write_text(json.dumps(result,indent=2)+'\n');results[method]=result
    combined=dict(status='pass',source_commit=meta['source_commit'],checkpoint_sha256=meta['files_sha256'],library_sha256=library_hashes,
                  historical_reference_metrics=metrics(source),results=results,
                  scope='Fixed placement/rules comparison; inherited circuit choices retained, no hardware, thermal or SI qualification.')
    (REPORT/'verification.json').write_text(json.dumps(combined,indent=2)+'\n');print(json.dumps({m:{k:v for k,v in r.items()if k in('status','vias','track_length_mm','violations','unconnected')}for m,r in results.items()},indent=2))


if __name__=='__main__':main()
