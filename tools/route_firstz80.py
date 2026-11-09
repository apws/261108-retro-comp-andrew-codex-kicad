#!/usr/bin/env python3
"""All-net, two-layer routing of the frozen historical FirstZ80 placement.

Uses the prior attributed pad rasterizer/copper writer, with per-net widths,
native pad geometry, current-width-aware track masks, and explicit offset
restoration. No original routed copper is reused. Candidates are preserved
whether or not native DRC accepts them.
"""
import argparse
import ctypes
import json
import math
import shutil
import subprocess
import time
from collections import defaultdict
from pathlib import Path
import pcbnew as pcb
import route_netgroup as shared
from prepare_firstz80 import ROOT,BASE,NAME,REPORT,geometry,digest,project_folder
from firstz80_validation import drc as native_drc

POWER={'GND','VCC','/9VDC','/VI','/VCC_PP'}
OFFSET=pcb.VECTOR2I(pcb.FromMM(60),pcb.FromMM(60))


def mask_line(grids,no_via,layer,a,b,width,current_width):
    radius=width/2+.2+current_width/2+.06
    vr=width/2+.2+.4+.06
    reach=max(radius,vr)
    steps=max(1,math.ceil(math.dist(a,b)/.05))
    for n in range(steps+1):
        x=a[0]+(b[0]-a[0])*n/steps;y=a[1]+(b[1]-a[1])*n/steps
        for iy in range(max(0,int((y-reach)/.1)),min(shared.NY-1,int((y+reach)/.1))+1):
            dy=(iy*.1-y)**2;row=iy*shared.NX
            for ix in range(max(0,int((x-reach)/.1)),min(shared.NX-1,int((x+reach)/.1))+1):
                d=(ix*.1-x)**2+dy
                if d<=radius*radius:grids[layer][row+ix]=1
                if d<=vr*vr:no_via[row+ix]=1


class Planner:
    def __init__(self,method,folder):
        binary=folder/'cache/planner.so';binary.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['g++','-std=c++17','-O3','-shared','-fPIC',str(ROOT/'tools/firstz80_router.cpp'),'-o',str(binary)],check=True)
        self.lib=ctypes.CDLL(str(binary));name='find_manhattan_route'if method=='manhattan'else'find_base_octilinear_route'
        self.fn=getattr(self.lib,name)
        self.fn.argtypes=[ctypes.c_int]*4+[ctypes.c_void_p]*4+[ctypes.c_int]*2+[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_uint64),ctypes.c_int]
        self.fn.restype=ctypes.c_int
    def search(self,start,goals,blocked,no_via,via,bend):
        def ptr(b):return ctypes.addressof((ctypes.c_uint8*len(b)).from_buffer(b))
        out=(ctypes.c_uint32*100000)();expanded=ctypes.c_uint64()
        n=self.fn(shared.NX,shared.NY,*start,ptr(blocked[0]),ptr(blocked[1]),ptr(no_via),ptr(goals),via,bend,out,100000,ctypes.byref(expanded),15000000)
        if n<0:raise RuntimeError(f'planner status {n}; expanded {expanded.value}')
        path=[]
        for state in out[:n]:
            layer,cell=divmod(state,shared.STRIDE);y,x=divmod(cell,shared.NX);path.append((x,y,layer))
        return path,expanded.value


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('method',choices=['manhattan','octilinear'])
    ap.add_argument('--attempt',type=int,default=1)
    ap.add_argument('--priority',action='append',default=[])
    ap.add_argument('--via-cost',type=int)
    ap.add_argument('--candidate-only',action='store_true')
    ap.add_argument('--reserve-escapes',action='store_true')
    ap.add_argument('--orthogonal-seed',action='store_true',help='Build a complete seed for the separate DRC-gated octilinear refinement')
    args=ap.parse_args()
    label='01-manhattan'if args.method=='manhattan'else'02-octilinear'
    report=REPORT/label/f'attempt-{args.attempt:02d}';assert not report.exists();project_folder(report)
    cp=BASE/'checkpoint';meta=json.loads((cp/'checkpoint.json').read_text())
    for name,h in meta['files_sha256'].items():assert digest(cp/name)==h
    for ext in ('.kicad_sch','.kicad_pro'):shutil.copy2(cp/(NAME+ext),report/(NAME+ext))
    board=pcb.LoadBoard(str(cp/f'{NAME}.kicad_pcb'));assert not board.GetTracks()
    original=geometry(board)
    # Local coordinates exist only in memory during planning. Native saved
    # output restores the exact source footprint origins and pad positions.
    for fp in board.GetFootprints():fp.Move(-OFFSET)
    shared.CLEARANCE=shared.POWER_CLEARANCE=.2;shared.VIA_DIA=.8;shared.VIA_RAD=.4;shared.VIA_DRILL=.4;shared.MARGIN=.06
    nets=defaultdict(list)
    for pad in board.GetPads():
        if pad.GetNetname() and not pad.GetNetname().startswith('unconnected-'):
            nets[pad.GetNetname()].append((pcb.ToMM(pad.GetPosition().x),pcb.ToMM(pad.GetPosition().y)))
    def rank(name):
        if name in args.priority:return -1,args.priority.index(name)
        if name in POWER:return 0,['/9VDC','/VI','/VCC_PP','VCC','GND'].index(name)
        if 'CLK' in name or 'RESET'in name:return 1,name
        if name.startswith('/D0'):return 3,name
        if name.startswith('/A'):return 4,name
        if name.startswith(('/PA','/PB','/PC')):return 5,name
        return 2,name
    order=sorted([n for n,c in nets.items()if len(set(c))>1],key=rank)
    planner=Planner('manhattan'if args.orthogonal_seed else args.method,report);started=time.monotonic();results=[];failures=[]
    # Separate track masks for both routing widths; each accepted net updates
    # both masks with its actual copper width. No width-dependent underclearance.
    masks={w:([bytearray(shared.STRIDE),bytearray(shared.STRIDE)],bytearray(shared.STRIDE))for w in (.25,1.)}
    future=set(order)
    for net in order:
        width=1. if net in POWER else .25;shared.TRACK_W=width
        tracks,track_vias=masks[width]
        pads,pad_vias,_,_=shared.build_obstacle_grids(board,current_net=net,include_tracks=False)
        if args.reserve_escapes:
            # Protect one front-layer escape for every not-yet-routed foreign
            # terminal. This is planning space, never copied/pre-routed copper.
            for fp in board.GetFootprints():
                fpads=list(fp.Pads());xx=[pcb.ToMM(p.GetPosition().x)for p in fpads];yy=[pcb.ToMM(p.GetPosition().y)for p in fpads]
                if not xx:continue
                cx=(min(xx)+max(xx))/2;cy=(min(yy)+max(yy))/2
                for pad in fpads:
                    name=pad.GetNetname()
                    if name==net or name not in future:continue
                    x,y=pcb.ToMM(pad.GetPosition().x),pcb.ToMM(pad.GetPosition().y)
                    # DIP/IDC rows use the side of their pad column. A single
                    # vertical header escapes horizontally toward open space.
                    if max(xx)-min(xx)>.5:
                        dx=1 if x>=cx else -1;dy=0
                    elif max(yy)-min(yy)>2:
                        dx=1 if x>80 else -1;dy=0
                    else:
                        dx=0;dy=1 if y>=cy else -1
                    target_width=1. if name in POWER else .25
                    mask_line(pads,pad_vias,0,(x,y),(x+dx*2,y+dy*2),target_width,width)
        blocked=[bytearray(a|b for a,b in zip(pads[l],tracks[l]))for l in (0,1)]
        no_via=bytearray(a|b for a,b in zip(pad_vias,track_vias))
        goals=bytearray(2*shared.STRIDE)
        coords=sorted(set(nets[net]));_,ia,ib=min((math.dist(a,b),i,j)for i,a in enumerate(coords)for j,b in enumerate(coords)if j>i)
        connected=[coords[ib]];remaining=[coords[ia]]+[c for i,c in enumerate(coords)if i not in (ia,ib)]
        gx,gy=[round(v/.1)for v in coords[ib]];goals[gy*shared.NX+gx]=goals[shared.STRIDE+gy*shared.NX+gx]=1
        added=[];states=0;netstart=time.monotonic()
        try:
            while remaining:
                remaining.sort(key=lambda c:(min(math.dist(c,v)for v in connected),c))
                coord=remaining.pop(0)
                path,count=planner.search(tuple(round(v/.1)for v in coord),goals,blocked,no_via,args.via_cost or(1500 if args.method=='manhattan'else 3000),0 if args.method=='manhattan'or args.orthogonal_seed else 180)
                states+=count
                end=path[-1][0]*.1,path[-1][1]*.1
                added.extend(shared.add_path_to_board(board,board.FindNet(net),path,coord,end))
                for x,y,l in path:goals[l*shared.STRIDE+y*shared.NX+x]=1
                gx,gy=[round(v/.1)for v in coord];goals[gy*shared.NX+gx]=goals[shared.STRIDE+gy*shared.NX+gx]=1
                connected.append(coord)
        except RuntimeError as error:failures.append(dict(net=net,error=str(error),unrouted_pads=1+len(remaining)))
        for item in added:
            for current,(grids,nv) in masks.items():
                if item[0]=='track':
                    _,l,x,y,xx,yy=item;mask_line(grids,nv,l,(x,y),(xx,yy),width,current)
                else:
                    _,x,y=item
                    for l in (0,1):mask_line(grids,nv,l,(x,y),(x,y),.8,current)
        result=dict(net=net,pads=len(coords),width_mm=width,seconds=round(time.monotonic()-netstart,3),expanded=states)
        results.append(result);print(json.dumps(result),flush=True)
        future.remove(net)
    for fp in board.GetFootprints():fp.Move(OFFSET)
    for track in board.GetTracks():track.Move(OFFSET)
    assert geometry(board)==original
    target=report/f'{NAME}.kicad_pcb';board.Save(str(target))
    drc=native_drc(target,report);board=pcb.LoadBoard(str(target));assert geometry(board)==original
    accepted=not failures and not any(drc[k]for k in ('violations','unconnected_items','schematic_parity'))
    summary=dict(method=args.method,status='validated'if accepted else'rejected',runtime_seconds=round(time.monotonic()-started,3),failures=failures,order=order,net_results=results,
                 violations=len(drc['violations']),unconnected=len(drc['unconnected_items']),parity=len(drc['schematic_parity']),geometry_unchanged=True,orthogonal_seed_for_refinement=args.orthogonal_seed,reserved_future_front_pad_escapes=args.reserve_escapes,via_cost=args.via_cost or(1500 if args.method=='manhattan'else 3000),checkpoint_sha256=meta['files_sha256'],board_sha256=digest(target),**shared.metrics(board))
    (report/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items()if k not in ('order','net_results','checkpoint_sha256')},indent=2),flush=True)
    if accepted and not args.candidate_only:
        layout=BASE/'layouts'/label
        assert digest(layout/f'{NAME}.kicad_pcb')==meta['files_sha256'][f'{NAME}.kicad_pcb']
        shutil.copy2(target,layout/target.name);shutil.copy2(report/'summary.json',layout/'routing.json')


if __name__=='__main__':main()
