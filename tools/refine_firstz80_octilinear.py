#!/usr/bin/env python3
"""Feasible orthogonal seed + native-DRC-gated 45 degree corner refinement.

The seed is computed afresh from the copper-free checkpoint by our own
router, not imported from Andrew or from the published Manhattan board.
Direct eight-direction attempts remain separately recorded as incomplete.
"""
import copy
import json
import math
import shutil
import time
import uuid
from collections import defaultdict
import pcbnew as pcb
import kicad_schematic as k
from prepare_firstz80 import ROOT,BASE,REPORT,NAME,geometry,digest,project_folder,command
from route_netgroup import metrics


def uid(node):return k.atom(k.kid(node,'uuid')[1])
def pt(node,name):return tuple(round(float(k.atom(v))*1000000)for v in k.kid(node,name)[1:])
def setpt(node,name,p):k.kid(node,name)[1:]=[k.a(f'{v/1000000:.6f}')for v in p]


def main():
    started=time.monotonic();seed=REPORT/'02-octilinear/attempt-92';summary=json.loads((seed/'summary.json').read_text())
    assert summary['status']=='validated'and summary['orthogonal_seed_for_refinement']
    original_path=seed/f'{NAME}.kicad_pcb';assert digest(original_path)==summary['board_sha256']
    original=pcb.LoadBoard(str(original_path));tree=k.parse(original_path.read_text());segments=k.kids(tree,'segment')
    stage=REPORT/'02-octilinear/refinement-92';assert not(stage/'summary.json').exists();project_folder(stage)
    for suffix in('.kicad_sch','.kicad_pro'):shutil.copy2(seed/(NAME+suffix),stage/(NAME+suffix))
    items={uid(s):s for s in segments};vertices=defaultdict(list)
    for s in segments:
        a,b=pt(s,'start'),pt(s,'end');layer=k.atom(k.kid(s,'layer')[1]);net=k.atom(k.kid(s,'net')[1])
        vertices[layer,net,a].append((uid(s),b));vertices[layer,net,b].append((uid(s),a))
    terminals={(p.GetPosition().x,p.GetPosition().y)for f in original.GetFootprints()for p in f.Pads()}
    terminals.update((v.GetPosition().x,v.GetPosition().y)for v in original.GetTracks()if v.Type()==pcb.PCB_VIA_T)
    corners=[]
    for (layer,net,p),ends in sorted(vertices.items()):
        if len(ends)!=2 or p in terminals:continue
        (i,a),(j,b)=ends
        da=(a[0]-p[0],a[1]-p[1]);db=(b[0]-p[0],b[1]-p[1])
        if not((da[0]==0) != (da[1]==0))or not((db[0]==0)!=(db[1]==0)):continue
        if da[0]*db[0]+da[1]*db[1]!=0:continue
        if k.dump(k.kid(items[i],'width'))!=k.dump(k.kid(items[j],'width')):continue
        cut=int(min(500000,math.hypot(*da)*.35,math.hypot(*db)*.35)//100000)*100000
        if cut<100000:continue
        aa=tuple(p[n]+(1 if da[n]>0 else -1 if da[n]<0 else 0)*cut for n in(0,1))
        bb=tuple(p[n]+(1 if db[n]>0 else -1 if db[n]<0 else 0)*cut for n in(0,1))
        corners.append(dict(layer=layer,net=net,point=p,tracks=[i,j],ends=[aa,bb],cut_nm=cut))
    assert corners
    active=set(range(len(corners)));rejected=set();attempts=[];target=stage/f'{NAME}.kicad_pcb'
    for attempt in range(1,20):
        replacements={};id_to_corners=defaultdict(set);diagonals=[]
        for n in sorted(active):
            c=corners[n]
            for id,new in zip(c['tracks'],c['ends']):
                replacements[id,c['point']]=new;id_to_corners[id].add(n)
            node=copy.deepcopy(items[c['tracks'][0]])
            setpt(node,'start',c['ends'][0]);setpt(node,'end',c['ends'][1])
            id=str(uuid.uuid5(uuid.NAMESPACE_URL,'firstz80/octilinear-corner/'+str(n)+'/'+digest(original_path)))
            k.kid(node,'uuid')[1]=k.q(id);id_to_corners[id].add(n);diagonals.append(node)
        candidate=copy.deepcopy(tree)
        for s in k.kids(candidate,'segment'):
            for end in('start','end'):
                key=uid(s),pt(s,end)
                if key in replacements:setpt(s,end,replacements[key])
        candidate.extend(diagonals);target.write_text(k.dump(candidate)+'\n')
        dpath=stage/f'drc-{attempt:02d}.json'
        command(['kicad-cli','pcb','drc','--schematic-parity','--severity-all','--all-track-errors','--format','json','-o',str(dpath),str(target)],stage/f'drc-{attempt:02d}.log')
        findings=json.loads(dpath.read_text());bad=set()
        for key in('violations','unconnected_items','schematic_parity'):
            for finding in findings[key]:
                related=set().union(*(id_to_corners.get(i['uuid'],set())for i in finding['items']))
                assert related,('A new finding is not attributable to the proposed corners',finding)
                bad|=related
        record=dict(iteration=attempt,proposed_corners=len(active),rejected_now=len(bad),violations=len(findings['violations']),unconnected=len(findings['unconnected_items']),parity=len(findings['schematic_parity']))
        attempts.append(record);print(json.dumps(record),flush=True)
        if not bad:
            shutil.copy2(dpath,stage/'drc.json');break
        active-=bad;rejected|=bad
    else:raise RuntimeError('Refinement did not converge')
    assert active and not any(findings[x]for x in('violations','unconnected_items','schematic_parity'))
    board=pcb.LoadBoard(str(target));assert geometry(board)==geometry(original)
    (stage/'corners.json').write_text(json.dumps(dict(proposals=corners,accepted=sorted(active),rejected=sorted(rejected),iterations=attempts),indent=2)+'\n')
    result=dict(status='validated',method='octilinear-hybrid',algorithm='Fresh Manhattan A* seed, then native-DRC-gated 45-degree corner refinement',
                direct_eight_direction_attempts_completed=False,seed_report=str(seed.relative_to(ROOT)),seed_board_sha256=digest(original_path),
                seed_routing_seconds=summary['runtime_seconds'],refinement_seconds=round(time.monotonic()-started,3),accepted_corners=len(active),rejected_corner_proposals=len(rejected),
                vias_preserved_from_own_seed=True,source_copper_reused=False,placement_and_pad_geometry_unchanged=True,
                violations=0,unconnected=0,parity=0,checkpoint_sha256=summary['checkpoint_sha256'],board_sha256=digest(target),**metrics(board))
    (stage/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    layout=BASE/'layouts/02-octilinear';cp=json.loads((BASE/'checkpoint/checkpoint.json').read_text())
    assert digest(layout/target.name)==cp['files_sha256'][target.name], 'Preserve a competing completed result'
    shutil.copy2(target,layout/target.name);shutil.copy2(stage/'summary.json',layout/'routing.json')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
