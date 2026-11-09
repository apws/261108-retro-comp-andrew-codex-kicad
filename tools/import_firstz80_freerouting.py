#!/usr/bin/env python3
"""Import the new MCP result and verify against its own FirstZ80 checkpoint."""
import json
import shutil
import uuid
import pcbnew as pcb
import kicad_schematic as k
from prepare_firstz80 import ROOT,BASE,REPORT,NAME,geometry,digest,project_folder
from route_netgroup import metrics
from firstz80_validation import drc


def main():
    cp=BASE/'checkpoint';meta=json.loads((cp/'checkpoint.json').read_text())
    for name,h in meta['files_sha256'].items():assert digest(cp/name)==h
    session=REPORT/'03-freerouting/FirstZ80-attempt-02.ses';assert session.stat().st_size>1000
    stage=REPORT/'03-freerouting/native-attempt-02';project_folder(stage)
    for suffix in('.kicad_sch','.kicad_pro'):shutil.copy2(cp/(NAME+suffix),stage/(NAME+suffix))
    original=pcb.LoadBoard(str(cp/f'{NAME}.kicad_pcb'));board=pcb.LoadBoard(str(cp/f'{NAME}.kicad_pcb'))
    assert pcb.ImportSpecctraSES(board,str(session))
    originals={fp.GetReference():fp for fp in original.GetFootprints()};restored=[]
    for fp in board.GetFootprints():
        before=originals[fp.GetReference()];dx=fp.GetPosition().x-before.GetPosition().x;dy=fp.GetPosition().y-before.GetPosition().y
        da=(fp.GetOrientationDegrees()-before.GetOrientationDegrees()+180)%360-180
        assert abs(dx)<=100 and abs(dy)<=100 and abs(da)<1e-6
        if dx or dy:restored.append(dict(reference=fp.GetReference(),delta_nm=[dx,dy]))
        fp.SetPosition(before.GetPosition());fp.SetOrientationDegrees(before.GetOrientationDegrees())
    assert geometry(board)==geometry(original)
    target=stage/f'{NAME}.kicad_pcb';board.Save(str(target));findings=drc(target,stage)
    repair=None
    if len(findings['violations'])==1 and findings['violations'][0]['type']=='hole_clearance':
        # Explicit native-DRC correction for this imported ground segment.
        # Do not weaken the 0.25 mm hole rule to accept the router's result.
        violation=findings['violations'][0]
        item=next(i for i in violation['items']if i['description'].startswith('Track [GND] on F.Cu'))
        track_id=item['uuid']
        shutil.copy2(target,stage/'before-hole-repair.kicad_pcb')
        shutil.copy2(stage/'drc.json',stage/'before-hole-repair-drc.json')
        tree=k.parse(target.read_text());segment=next(s for s in k.kids(tree,'segment')if k.atom(k.kid(s,'uuid')[1])==track_id)
        a=tuple(float(k.atom(x))for x in k.kid(segment,'start')[1:]);b=tuple(float(k.atom(x))for x in k.kid(segment,'end')[1:])
        assert a==(132.5281,66.1198)and b==(117.8631,66.1198)
        aa=(a[0],66.0598);bb=(b[0],66.0598)
        tree.remove(segment)
        import copy
        for n,(x,y)in enumerate(zip((a,aa,bb),(aa,bb,b))):
            s=copy.deepcopy(segment)
            k.kid(s,'start')[1:]=[k.a(str(v))for v in x];k.kid(s,'end')[1:]=[k.a(str(v))for v in y]
            k.kid(s,'uuid')[1]=k.q(str(uuid.uuid5(uuid.NAMESPACE_URL,'firstz80/hole-repair/'+str(n))))
            tree.append(s)
        target.write_text(k.dump(tree)+'\n')
        repair=dict(reason=violation,offset_mm=[0,-.06],new_path_mm=[a,aa,bb,b],scope='Post-import track correction; no placement or rule changes.')
        (stage/'hole-repair.json').write_text(json.dumps(repair,indent=2)+'\n')
        findings=drc(target,stage)
    board=pcb.LoadBoard(str(target));assert geometry(board)==geometry(original)
    accepted=not any(findings[k]for k in('violations','unconnected_items','schematic_parity'))
    result=dict(status='validated'if accepted else'rejected',transport='official-local-MCP',optimizer_enabled=False,
                violations=len(findings['violations']),unconnected=len(findings['unconnected_items']),parity=len(findings['schematic_parity']),
                placement_and_pad_geometry_unchanged=True,bounded_import_rounding_restored=restored,
                checkpoint_sha256=meta['files_sha256'],session_sha256=digest(session),board_sha256=digest(target),**metrics(board))
    result['post_import_hole_clearance_repair']=bool(repair)
    (stage/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if not accepted:raise SystemExit('Native import rejected; result not promoted.')
    layout=BASE/'layouts/03-freerouting';assert digest(layout/target.name)==meta['files_sha256'][target.name]
    shutil.copy2(target,layout/target.name);shutil.copy2(stage/'summary.json',layout/'routing.json')


if __name__=='__main__':main()
