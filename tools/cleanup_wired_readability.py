#!/usr/bin/env python3
"""Local wire edits on the reviewed human layout; never regenerate symbols.

Only selected existing net paths and fields change. Save the exact input,
retain untouched native text blocks, and require separate native validation.
"""
import copy
import hashlib
import json
import shutil
import uuid
from pathlib import Path
import kicad_schematic as k
from check_wired_schematic import graph,xy,point,native

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'schematics/02-wired-a3/Z80_Simple_Computer.kicad_sch'
REPORT=ROOT/'reports/schematic-wired-a3/cleanup-261109-2330'
EXPECTED='b5ab1a59ff58a9e4e2632a093ed8b1fc5d4799a8a48e9c9ada20fd35b38152fd'
NS=uuid.uuid5(uuid.NAMESPACE_URL,'andrew-codex-z80/readability-cleanup')


def edit_native(text, changes, additions):
    spans=[];depth=0;quoted=False;escaped=False;start=None
    for i,c in enumerate(text):
        if quoted:
            if escaped:escaped=False
            elif c=='\\':escaped=True
            elif c=='"':quoted=False
        elif c=='"':quoted=True
        elif c=='(':
            if depth==1:start=i
            depth+=1
        elif c==')':
            depth-=1
            if depth==1:
                node=k.parse(text[start:i+1]);u=k.kid(node,'uuid')
                if u and k.atom(u[1])in changes:spans.append((start,i+1,changes[k.atom(u[1])]))
    for a,b,value in reversed(spans):text=text[:a]+value+text[b:]
    end=text.rfind(')')
    return text[:end]+'\n'+''.join('  '+k.dump(n)+'\n'for n in additions)+text[end:]


def main():
    raw=PATH.read_text();assert hashlib.sha256(PATH.read_bytes()).hexdigest()==EXPECTED
    REPORT.mkdir(parents=True,exist_ok=True);saved=REPORT/'before';saved.mkdir(exist_ok=True)
    for suffix in ('.kicad_sch','.kicad_pro'):shutil.copy2(PATH.with_suffix(suffix),saved/PATH.with_suffix(suffix).name)
    t=k.parse(raw);lib={k.atom(s[1]):s for s in k.kids(k.kid(t,'lib_symbols'),'symbol')}
    actual,_=native(ROOT/'output/schematic-wired-a3/Z80_Simple_Computer.xml');pins={}
    for s in k.kids(t,'symbol'):
        ref=next(k.atom(p[2])for p in k.kids(s,'property')if k.atom(p[1])=='Reference')
        at=k.kid(s,'at');x,y=map(lambda v:float(k.atom(v)),at[1:3]);unit=int(k.atom(k.kid(s,'unit')[1]))
        for n,recs in k.collect_pins(lib[k.atom(k.kid(s,'lib_id')[1])]).items():
            sel=[r for r in recs if r['unit']in(0,unit)and r['style']in(0,1)]
            if sel and(ref,n)in actual:
                r=min(sel,key=lambda r:r['style']!=1);pins[ref,n]=point(x+r['x'],y-r['y'])
    wires=k.kids(t,'wire');segments=[tuple(xy(p)for p in k.kids(k.kid(w,'pts'),'xy'))for w in wires]
    junctions={xy(k.kid(j,'at'))for j in k.kids(t,'junction')}
    groups=graph(segments,set(pins.values())|junctions)
    names={groups[p]:actual[key]for key,p in pins.items()if p in groups}
    net_wires={}
    for w,(a,b)in zip(wires,segments):net_wires.setdefault(names.get(groups.get(a)),[]).append(w)
    changes={};additions=[];log=[]
    def uid(n):return k.atom(k.kid(n,'uuid')[1])
    def wire(a,b,net):
        assert a!=b and(a[0]==b[0]or a[1]==b[1]),(net,a,b)
        additions.append(k.parse(f'(wire (pts (xy {a[0]} {a[1]})(xy {b[0]} {b[1]}))(stroke(width 0)(type default))(uuid "{uuid.uuid5(NS,str((net,a,b)))}"))'))
    def poly(net,points):
        for a,b in zip(points,points[1:]):wire(a,b,net)
    def replace_net(net,paths,dots=()):
        for w in net_wires[net]:changes[uid(w)]=''
        for j in k.kids(t,'junction'):
            p=xy(k.kid(j,'at'))
            if names.get(groups.get(p))==net:changes[uid(j)]=''
        for path in paths:poly(net,path)
        for p in dots:
            additions.append(k.parse(f'(junction(at {p[0]} {p[1]})(diameter 0)(color 0 0 0 0)(uuid "{uuid.uuid5(NS,str((net,p)))}"))'))
        log.append({'net':net,'paths':paths,'junctions':dots})
    replace_net('/CLK', [[(33.02,63.5),(15.24,63.5),(15.24,162.56),(59.69,162.56),(59.69,148.59),(54.61,148.59)]])
    replace_net('/~{ROMCS}', [[(101.6,106.68),(97.79,106.68),(97.79,147.32),(185.42,147.32),(185.42,161.29),(182.88,161.29)]])
    replace_net('/~{PIOCS}', [[(224.79,68.58),(212.09,68.58),(212.09,204.47),(182.88,204.47)]])
    replace_net('/CLK_UART', [[(326.39,99.06),(312.42,99.06),(312.42,175.26),(292.1,175.26)]])
    replace_net('/RESET_H',[
        [(224.79,60.96),(215.9,60.96),(215.9,146.05),(228.6,146.05),(320.04,146.05),(320.04,129.54),(326.39,129.54)],
        [(228.6,146.05),(228.6,209.55),(80.01,209.55),(80.01,203.2),(64.77,203.2)],
        [(80.01,203.2),(82.55,203.2)]],[(228.6,146.05),(80.01,203.2)])
    replace_net('/~{MREQ}',[
        [(33.02,76.2),(24.13,76.2),(24.13,158.75),(163.83,158.75),(167.64,158.75)],
        [(163.83,158.75),(163.83,181.61),(167.64,181.61)]],[(163.83,158.75)])
    replace_net('/~{IORQ}',[
        [(33.02,78.74),(21.59,78.74),(21.59,171.45),(95.25,171.45),(95.25,201.93),(161.29,201.93),(167.64,201.93)],
        [(161.29,201.93),(161.29,224.79),(167.64,224.79)]],[(161.29,201.93)])
    # Move selected bends while preserving the remaining reviewed control paths.
    replacements={
        '4d5c6355-76c2-5d20-9339-6a3a6acfb436':[(80.01,44.45),(80.01,104.14)],
        '59436177-d78f-5935-bd3b-3e8d9c73d37c':[(29.21,44.45),(80.01,44.45)],
        'a3b24446-1fbb-5efc-ae5e-4ce547504368':[(29.21,44.45),(29.21,81.28)],
        '06301464-c25a-5048-8c72-9bb3e5c40006':[],
        '8ac3bfb9-27c8-510f-ba0b-289f34b2e6f8':[(220.98,45.72),(294.64,45.72)],
        'ba2ac1f5-1880-5ad5-8b54-48fd1406fee6':[(220.98,45.72),(220.98,71.12)],
        'cf8286df-deb0-5ad9-9956-567cc4fe53a5':[(294.64,45.72),(294.64,121.92)],
        '1ae44d29-55d5-50f2-9ab6-ccec149741bc':[(317.5,48.26),(317.5,96.52)],
        'f400b1fd-bef6-5d8c-86e1-8baab379957d':[(218.44,48.26),(317.5,48.26)],
        '5f817349-1226-53f8-be98-7384f4b6e0a3':[(317.5,96.52),(322.58,96.52)]}
    for id,pts in replacements.items():
        assert any(uid(w)==id for w in wires);changes[id]='';poly('local-control',pts)
    for net,start,right,y,left,end in [
        ('TX_TTL',(377.19,101.6),381.,151.13,270.51,(298.45,218.44)),
        ('RX_TTL',(377.19,99.06),383.54,153.67,267.97,(298.45,228.6)),
        ('DTR_TTL',(377.19,81.28),386.08,156.21,265.43,(298.45,223.52)),
        ('DSR_TTL',(377.19,68.58),389.89,158.75,262.89,(298.45,233.68))]:
        replace_net('/'+net,[[start,(right,start[1]),(right,y),(left,y),(left,end[1]),end]])
    replace_net('/TX_RS232',[[(339.09,218.44),(341.63,218.44),(341.63,226.06),(379.73,226.06),(379.73,237.49),(387.35,237.49)]])
    replace_net('/DTR_RS232',[[(339.09,223.52),(346.71,223.52),(346.71,228.6),(377.19,228.6),(377.19,242.57),(387.35,242.57)]])
    replace_net('/RX_RS232',[[(339.09,228.6),(341.63,228.6),(341.63,231.14),(382.27,231.14),(382.27,232.41),(387.35,232.41)]])
    replace_net('/DSR_RS232',[[(339.09,233.68),(344.17,233.68),(344.17,234.95),(384.81,234.95),(384.81,229.87),(387.35,229.87)]])
    labels={'~{MREQ}':(80.01,158.75),'~{IORQ}':(50.8,171.45),'~{RD}':(125.73,115.57),'~{WR}':(278.13,48.26),
            '~{PIOCS}':(190.5,204.47),'CLK_UART':(302.26,175.26),'RESET_H':(245.11,146.05),
            'TX_TTL':(278.13,218.44),'RX_TTL':(278.13,228.6),'DTR_TTL':(278.13,223.52),'DSR_TTL':(278.13,233.68),
            'TX_RS232':(358.14,226.06),'DTR_RS232':(358.14,228.6),'RX_RS232':(358.14,231.14),'DSR_RS232':(358.14,234.95)}
    for lab in k.kids(t,'label'):
        name=k.atom(lab[1])
        if name in labels:
            at=k.kid(lab,'at');at[1:]=[('atom',str(v))for v in (*labels[name],0)]
            effects=k.kid(lab,'effects');just=k.kid(effects,'justify');just[1:]=[('atom','left'),('atom','bottom')]
            changes[uid(lab)]=k.dump(lab)
    # Repair short supply stubs pointing inward along the moved oscillator pins.
    for w in wires:
        ps=tuple(tuple(v/10000 for v in p)for p in segments[wires.index(w)])
        if ps==((46.99,142.24),(46.99,140.97)):
            changes[uid(w)]='';poly('+5V-Y1',[(52.07,138.43),(46.99,138.43),(46.99,140.97)])
        if ps==((46.99,154.94),(46.99,156.21)):
            changes[uid(w)]='';poly('GND-Y1',[(46.99,156.21),(46.99,160.02)])
    for s in k.kids(t,'symbol'):
        ref=next(k.atom(p[2])for p in k.kids(s,'property')if k.atom(p[1])=='Reference')
        at=k.kid(s,'at');pos=tuple(float(k.atom(v))for v in at[1:3])
        if ref.startswith('#')and pos in((46.99,142.24),(46.99,154.94)):
            dy=-3.81 if pos[1]==142.24 else 5.08
            dx=5.08 if pos[1]==142.24 else 0
            at[1]=('atom',str(round(pos[0]+dx,4)))
            at[2]=('atom',str(round(pos[1]+dy,4)))
            for prop in k.kids(s,'property'):
                a=k.kid(prop,'at');a[2]=('atom',str(round(float(k.atom(a[2]))+dy,4)))
                a[1]=('atom',str(round(float(k.atom(a[1]))+dx,4)))
            changes[uid(s)]=k.dump(s)
        if ref=='#PWR002':
            prop=next(p for p in k.kids(s,'property')if k.atom(p[1])=='Value')
            a=k.kid(prop,'at');a[1:3]=[('atom','322.58'),('atom','87.63')]
            changes[uid(s)]=k.dump(s)
    for text in k.kids(t,'text'):
        if 'Crossings connect only at dots.' in k.atom(text[1]):
            text[1]=('str',k.atom(text[1]).replace('Crossings connect only at dots.','Unjoined crossings have no dot; branches are shown outside pins.'))
            changes[uid(text)]=k.dump(text)
    PATH.write_text(edit_native(raw,changes,additions))
    (REPORT/'edits.json').write_text(json.dumps(dict(input_sha256=EXPECTED,output_sha256=hashlib.sha256(PATH.read_bytes()).hexdigest(),changed_existing_objects=len(changes),new_objects=len(additions),rerouted_nets=log),indent=2)+'\n')
    print('Local cleanup written; run native ERC, netlist and drawn-path checks.')


if __name__=='__main__':main()
