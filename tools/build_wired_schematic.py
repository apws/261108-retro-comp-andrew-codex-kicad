#!/usr/bin/env python3
"""Separate, single-A3 wired drawing of the verified circuit; no PCB changes.

Functional placement is explicit. A direction-aware orthogonal drawing planner
connects scalar signals around symbol bodies, allowing marked-free crossings.
Reuses the attributed KiCad serialization helpers in kicad_schematic.py.
"""
import argparse
import collections
import hashlib
import heapq
import json
import shutil
import uuid
from pathlib import Path

import kicad_schematic as k

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'schematics/02-wired-a3'
NAME = 'Z80_Simple_Computer'
GRID = 1.27
NS = uuid.uuid5(uuid.NAMESPACE_URL, 'andrew-codex-z80/wired-a3')
POS = {
    'U1': (45.72, 93.98), 'U3': (111.76, 86.36),
    'U2': (172.72, 91.44), 'U4': (242.57, 93.98),
    'U5': (351.79, 93.98), 'U6': (318.77, 215.90),
    'Y1': (50.80, 165.10), 'Y2': (284.48, 175.26),
    'R1': (24.13, 191.77), 'C1': (24.13, 213.36),
    'R2': (41.91, 219.71), 'SW1': (62.23, 232.41),
    'C20': (271.78, 196.85), 'C21': (360.68, 196.85),
    'C22': (391.16, 180.34), 'C23': (374.65, 208.28),
    'C24': (360.68, 218.44), 'J1': (394.97, 237.49),
    'J5': (389.89, 153.67),
}
GATES = {
    'U7': {1: (57.15, 203.20), 2: (90.17, 203.20),
           3: (134.62, 224.79), 4: (134.62, 182.88),
           5: (55.88, 247.65), 6: (88.90, 247.65), 7: (223.52, 262.89)},
    'U8': {1: (175.26, 204.47), 2: (175.26, 227.33),
           3: (175.26, 161.29), 4: (175.26, 184.15), 5: (248.92, 262.89)},
}


def ident(kind, index):
    return str(uuid.uuid5(NS, f'{kind}/{index}'))


def cell(point):
    return tuple(round(v / GRID) for v in point)


def world(point):
    return tuple(round(v * GRID, 4) for v in point)


def body_box(sym, inst):
    points = []
    bare = k.bare_name(inst['lib_id'])
    for unit in k.kids(sym, 'symbol'):
        number, style = k.unit_of(k.atom(unit[1]), bare)
        if number not in (0, inst['unit']) or style not in (0, 1):
            continue
        for obj in unit[2:]:
            if not isinstance(obj, list):
                continue
            kind = k.atom(obj[0])
            if kind == 'rectangle':
                for key in ('start', 'end'):
                    xy = k.kid(obj, key)
                    points.append((float(k.atom(xy[1])), float(k.atom(xy[2]))))
            elif kind == 'polyline':
                points += [(float(k.atom(p[1])), float(k.atom(p[2])))
                           for p in k.kids(k.kid(obj, 'pts'), 'xy')]
            elif kind == 'circle':
                c, radius = k.kid(obj, 'center'), float(k.atom(k.kid(obj, 'radius')[1]))
                x, y = float(k.atom(c[1])), float(k.atom(c[2]))
                points += [(x-radius, y-radius), (x+radius, y+radius)]
    if not points:
        return None
    return (inst['x']+min(p[0] for p in points), inst['y']-max(p[1] for p in points),
            inst['x']+max(p[0] for p in points), inst['y']-min(p[1] for p in points))


class Drawing:
    def __init__(self, spec):
        self.sheet = k.Sheet()
        self.parts = {p['ref']: p for p in spec['components']}
        self.pins = {}
        self.buses = []
        self.entries = []
        self.edges = collections.defaultdict(set)
        self.bus_pins = set()
        self.obstacles = set()
        self.routes = []
        self.consumed = set()
        self.pin_owners = {}
        for i in range(2, 12):
            POS[f'C{i}'] = (17.78+(i-2)*19.05, 273.05)
        for i in range(1, 5):
            POS[f'H{i}'] = (266.70+(i-1)*10.16, 279.40)
        # Header pin 1 aligns with PA0, PB0, PC0 respectively.
        for ref, y in [('J2', 68.58), ('J3', 91.44), ('J4', 114.30)]:
            POS[ref] = (292.10, y)
        for p in self.parts.values():
            lib, name = p['lib_id'].split(':')
            sym = k.prepare(lib, name, p['parent'])
            pins = self.sheet.use(sym)
            assert set(pins) == set(p['pins']) | set(p['no_connect'])
            for unit, pos in GATES.get(p['ref'], {1: POS.get(p['ref'])}).items():
                inst = self.sheet.place(p['ref'], p['lib_id'], p['value'], *pos,
                                        unit, p['footprint'], pins,
                                        mechanical=p['ref'].startswith('H'))
                box = body_box(sym, inst)
                if box:
                    self.block_box(box, .25)
                for number, rec in inst['pins'].items():
                    self.pins[p['ref'], number] = rec
                    pt = cell(rec['world'])
                    self.pin_owners[pt] = p['pins'].get(number, 'NC:'+p['ref']+number)
        # Reserve every pin's approach before routing any signal, so an early
        # route cannot occupy the endpoint of a later pin's escape stub.
        for (ref,number),rec in self.pins.items():
            net=self.parts[ref]['pins'].get(number)
            if net is None:continue
            px,py=cell(rec['world']);dx,dy=self.sheet._outward(rec['ang'])
            for distance in (1,2):
                tip=(px+int(dx)*distance,py+int(dy)*distance)
                old=self.pin_owners.get(tip)
                assert old in (None,net),(ref,number,old,net)
                self.pin_owners[tip]=net
        for name in ('+5V', 'GND', 'PWR_FLAG'):
            self.sheet.use(k.prepare('power', name))
        # Keep drawing out of the title block and sheet margin.
        self.block_box((298.45, 251.46, 415.29, 293.37))

    def block_box(self, box, margin=0):
        x0, y0, x1, y1 = box
        for x in range(5, 325):
            if x0-margin <= x*GRID <= x1+margin:
                for y in range(24, 228):
                    if y0-margin <= y*GRID <= y1+margin:
                        self.obstacles.add((x, y))

    def pin(self, ref, number):
        return self.pins[ref, str(number)]

    def net(self, ref, number):
        return self.parts[ref]['pins'][str(number)]

    def line(self, net, points):
        points = [cell(p) for p in points]
        for a, b in zip(points, points[1:]):
            assert a[0] == b[0] or a[1] == b[1], (net, a, b)
            dx = (b[0]>a[0])-(b[0]<a[0])
            dy = (b[1]>a[1])-(b[1]<a[1])
            p = a
            while p != b:
                q = (p[0]+dx, p[1]+dy)
                self.edges[net].add(tuple(sorted((p, q))))
                p = q

    def bus(self, name, points):
        self.line('BUS:'+name,points)
        self.sheet.labels.append((points[0], name, 'left bottom'))
        self.pin_owners[cell(points[0])]='BUS:'+name

    def bus_bank(self, ref, names, x, trunk_y):
        records = [(number, net, self.pin(ref, number))
                   for number, net in self.parts[ref]['pins'].items() if net in names]
        assert records
        end_ys = []
        for number, net, rec in records:
            px, py = rec['world']
            direction = 1 if x > px else -1
            end = (round(x-direction*GRID, 4), py)
            tap = (x, round(py-GRID, 4))
            self.line(net, [(px, py), end])
            self.entries.append((end, (tap[0]-end[0], tap[1]-end[1])))
            label_at = (round(px+direction*2.54,4), py)
            self.sheet.labels.append((label_at, net, 'left bottom' if direction>0 else 'right bottom'))
            self.pin_owners[cell(label_at)]=net
            self.pin_owners[cell(end)]=net
            self.pin_owners[cell(tap)]='BUS:'+('A[0..15]' if net.startswith('A') else 'D[0..7]')
            end_ys.append(tap[1])
            self.bus_pins.add((ref, number))
        lo, hi = min(end_ys+[trunk_y]), max(end_ys+[trunk_y])
        name='A[0..15]' if records[0][1].startswith('A') else 'D[0..7]'
        self.line('BUS:'+name,[(x,lo),(x,hi)])

    def initial_wiring(self):
        s = self.sheet
        # Continuous, separately named address and data bus backbones.
        self.bus('A[0..15]', [(73.66, 31.75), (307.34, 31.75)])
        self.bus('D[0..7]', [(73.66, 144.78), (313.69, 144.78)])
        for ref, ax, dx in [('U1',73.66,73.66),('U3',91.44,133.35),
                           ('U2',151.13,195.58),('U4',213.36,207.01),
                           ('U5',307.34,313.69)]:
            self.bus_bank(ref, {f'A{i}' for i in range(16)}, ax,31.75)
            self.bus_bank(ref, {f'D{i}' for i in range(8)}, dx,144.78)
        # PIO headers use 24 direct pin-to-pin wires, in order.
        for prefix, ref in [('PA','J2'),('PB','J3'),('PC','J4')]:
            for i in range(8):
                src = next(n for n,v in self.parts['U4']['pins'].items() if v==f'{prefix}{i}')
                a,b = self.pin('U4',src)['world'], self.pin(ref,i+1)['world']
                assert abs(a[1]-b[1])<.001,(a,b)
                self.line(prefix+str(i),[a,b])
                s.labels.append(((a[0]+3.81,a[1]), prefix+str(i),'left bottom'))
                self.consumed.update({('U4',src),(ref,str(i+1))})
        # Compact supply straps remain ordinary wires and standard power ports.
        for ref, nums, rail, length in [
            ('U1',[16,17],'+5V',7.62), ('U5',[12,13],'+5V',7.62),
            ('U5',[19,22,25],'GND',10.16), ('U5',[38,39],'+5V',10.16)]:
            before=len(s.wires)
            s.bus_to_power([self.pin(ref,n) for n in nums],rail,length)
            for a,b in s.wires[before:]: self.line(rail,[a,b])
            self.consumed.update((ref,str(n)) for n in nums)
        # Common decoupling rails are drawn across the complete capacitor row.
        for i in range(2,12):
            ref=f'C{i}'
            for n,rail,y in [(1,'+5V',262.89),(2,'GND',281.94)]:
                self.line(rail,[self.pin(ref,n)['world'],(POS[ref][0],y)])
                self.consumed.add((ref,str(n)))
        for rail,y in [('+5V',262.89),('GND',281.94)]:
            self.line(rail,[(17.78,y),(189.23,y)])
            s.power_at(17.78,y,rail)
        for ref,p in self.parts.items():
            for number in p['no_connect']:
                s.ncs.append(tuple(self.pin(ref,number)['world']))
            for number,net in p['pins'].items():
                if net not in ('+5V','GND') or (ref,number) in self.consumed:
                    continue
                rec=self.pin(ref,number)
                a=rec['world']; dx,dy=s._outward(rec['ang'])
                length=3.81
                if ref=='U1': length=7.62
                if ref=='U3' and number=='1': length=6.35
                end=(round(a[0]+dx*length,4),round(a[1]+dy*length,4))
                self.line(net,[a,end]); s.power_at(*end,net)
                self.consumed.add((ref,number))
        for x,rail in [(377.19,'+5V'),(401.32,'GND')]:
            flag=s.flag(x,158.75)
            end=(x,154.94 if rail=='+5V' else 162.56)
            s.power_at(*end,rail)
            self.line(rail,[flag['pins']['1']['world'],end])
        s.wires=[]

    def occupancy(self):
        occ=collections.defaultdict(dict)
        for net,edges in self.edges.items():
            adj=collections.defaultdict(set)
            for a,b in edges:
                adj[a].add(b);adj[b].add(a)
            for pt,ns in adj.items():
                horizontal=all(p[1]==pt[1] for p in ns)
                vertical=all(p[0]==pt[0] for p in ns)
                occ[pt][net]=(1 if horizontal else 2 if vertical else 3,len(ns))
        return occ

    def route(self, net, terminals):
        # Pin stubs enforce departure from the correct side of a symbol.
        tips=[]
        for ref,number in terminals:
            rec=self.pin(ref,number); dx,dy=self.sheet._outward(rec['ang'])
            a=rec['world']; end=(round(a[0]+dx*2.54,4),round(a[1]+dy*2.54,4))
            self.line(net,[a,end]); tips.append(cell(end))
        first=self.pin(*terminals[0]);dx,dy=self.sheet._outward(first['ang'])
        self.sheet.labels.append((world(tips[0]),net,'right bottom' if dx<0 else 'left bottom'))
        remaining=set(tips[1:])
        while remaining:
            occ=self.occupancy()
            # Existing disjoint stubs must not be mistaken for the grown tree.
            adj=collections.defaultdict(set)
            for a,b in self.edges[net]:adj[a].add(b);adj[b].add(a)
            todo=[tips[0]]; tree=set(todo)
            while todo:
                for p in adj[todo.pop()]:
                    if p not in tree:tree.add(p);todo.append(p)
            remaining-=tree
            if not remaining:break
            start=min(remaining,key=lambda p:min(abs(p[0]-t[0])+abs(p[1]-t[1]) for t in tree))
            path=self.search(net,start,tree,occ)
            self.line(net,[world(p) for p in path])
            self.routes.append(dict(net=net,grid_steps=len(path)-1))

    def search(self,net,start,targets,occ):
        dirs=[(1,0),(-1,0),(0,1),(0,-1)]
        minx,maxx=min(p[0] for p in targets),max(p[0] for p in targets)
        miny,maxy=min(p[1] for p in targets),max(p[1] for p in targets)
        def h(p):return max(minx-p[0],0,p[0]-maxx)+max(miny-p[1],0,p[1]-maxy)
        queue=[(h(start),0,start,4)]; best={(start,4):0}; prev={}
        end=None
        while queue:
            _,cost,p,heading=heapq.heappop(queue)
            if best.get((p,heading))!=cost:continue
            if p in targets:end=(p,heading);break
            at_other={n:v for n,v in occ.get(p,{}).items() if n!=net}
            for d,(dx,dy) in enumerate(dirs):
                q=(p[0]+dx,p[1]+dy)
                if not (8<=q[0]<=320 and 27<=q[1]<=225):continue
                if q in self.obstacles and q not in targets:continue
                if q in self.pin_owners and self.pin_owners[q]!=net:continue
                # A crossing must be straight through the interior of an old
                # straight wire, never a corner, pin, branch or endpoint.
                if at_other and d!=heading:continue
                others={n:v for n,v in occ.get(q,{}).items() if n!=net}
                orientation=1 if dx else 2
                if others and (q in self.pin_owners or any(o==3 or degree!=2 or o==orientation for o,degree in others.values())):continue
                step=1+(3 if heading!=4 and d!=heading else 0)+18*len(others)
                # Keep close parallel runs apart where the page has room.
                for side in ((0,1),(0,-1)) if dx else ((1,0),(-1,0)):
                    near=(q[0]+side[0],q[1]+side[1])
                    step+=sum(1 for n,v in occ.get(near,{}).items() if n!=net and v[0]==orientation)
                new=cost+step;state=(q,d)
                if new<best.get(state,10**12):
                    best[state]=new;prev[state]=(p,heading)
                    heapq.heappush(queue,(new+h(q),new,q,d))
        if end is None:raise RuntimeError(f'No drawing path for {net} from {world(start)}')
        path=[]
        while end!=(start,4):path.append(end[0]);end=prev[end]
        return [start]+list(reversed(path))

    def finish(self):
        nets=collections.defaultdict(list)
        for ref,p in self.parts.items():
            for number,net in p['pins'].items():
                key=(ref,number)
                if key not in self.consumed and key not in self.bus_pins and net not in ('+5V','GND'):
                    nets[net].append(key)
        # Direct local support first, then the shared control signals.
        order=['A7','A15','C1P','C1M','C2P','C2M','VS_PLUS','VS_MINUS','BAUDOUT',
               'RESET_RC','SW_RESET','~{A15}','~{A7}','CLK','CLK_UART',
               'TX_RS232','RX_RS232','DTR_RS232','DSR_RS232',
               'TX_TTL','RX_TTL','DTR_TTL','DSR_TTL',
               '~{ROMCS}','~{RAMCS}','~{PIOCS}','~{UARTCS}',
               '~{MREQ}','~{IORQ}','~{RD}','~{WR}','RESET_H','~{RESET}']
        for net in order+sorted(set(nets)-set(order)):
            if net not in nets:continue
            terminals=nets[net]
            # Address taps for the decoder are taken from the address bus.
            if net in ('A7','A15'):
                for ref,num in terminals:
                    if ref=='U7':
                        self.bus_bank(ref,{net},116.84,156.21)
                        self.line('BUS:A[0..15]',[(91.44,31.75),(91.44,156.21),(116.84,156.21)])
                    else:
                        self.bus_bank(ref,{net},151.13,31.75)
                continue
            if len(terminals)>1:
                print('Drawing',net,flush=True)
                self.route(net,terminals)
        # Every straight run stays whole at crossings; split only for joins
        # belonging to the same net. KiCad then renders crossings without dots.
        self.sheet.wires=[];self.sheet.junctions=[];self.buses=[]
        connection_points={cell(rec['world']) for p in self.sheet.placed for rec in p['pins'].values()}
        connection_points.update(cell(pt) for pt,net,justify in self.sheet.labels)
        connection_points.update(cell(pt) for pt,size in self.entries)
        for net,edges in self.edges.items():
            adj=collections.defaultdict(set)
            for a,b in edges:adj[a].add(b);adj[b].add(a)
            # A branch can join at a pin before reaching its reserved stub
            # tip. Remove that unused tail rather than leave a dangling wire.
            if not net.startswith('BUS:'):
                leaves=[p for p,ns in adj.items() if len(ns)==1 and p not in connection_points]
                while leaves:
                    p=leaves.pop()
                    if len(adj[p])!=1 or p in connection_points:continue
                    q=next(iter(adj[p]));adj[q].remove(p);del adj[p]
                    edges.discard(tuple(sorted((p,q))))
                    if len(adj[q])==1 and q not in connection_points:leaves.append(q)
            vertices={p for p,ns in adj.items() if len(ns)!=2 or
                      not (len({q[0] for q in ns}|{p[0]})==1 or len({q[1] for q in ns}|{p[1]})==1)}
            vertices.update(connection_points & set(adj))
            seen=set()
            for a in sorted(vertices):
                for b in sorted(adj[a]):
                    if tuple(sorted((a,b))) in seen:continue
                    p,q=a,b;seen.add(tuple(sorted((p,q))))
                    while q not in vertices:
                        z=next(x for x in adj[q] if x!=p)
                        p,q=q,z;seen.add(tuple(sorted((p,q))))
                    if net.startswith('BUS:'):
                        self.buses.append((world(a),world(q)))
                    else:self.sheet.wires.append((world(a),world(q)))
            self.sheet.junctions += [world(p) for p,ns in adj.items() if len(ns)>2]
        self.sheet.wires.sort();self.sheet.junctions=sorted(set(self.sheet.junctions))
        return self


def render(d):
    s=d.sheet
    lines=['(kicad_sch','  (version 20260306)','  (generator "eeschema")',
           '  (generator_version "10.0")',f'  (uuid "{k.SHEET_UUID}")',
           '  (paper "A3")',
           '  (title_block (title "Andrew Z80 / wired A3 variant") (date "2026-11-09")',
           '    (rev "W1") (company "Falken / Codex")',
           '    (comment 1 "Same verified circuit / independent drawing")',
           '    (comment 2 "A and D buses; continuous scalar signal wiring")',
           '    (comment 3 "4MHz CPU / 1.8432MHz UART / regulated +5V")',
           '    (comment 4 "Compare with design/ and the original source GIF"))',
           '  (lib_symbols']
    lines += ['    '+k.dump(sym) for sym in s.syms.values()]+['  )']
    for p in s.placed:
        node=k.emit_instance(p)
        box=body_box(s.syms[p['lib_id']],p)
        for prop in k.kids(node,'property'):
            name=k.atom(prop[1])
            if name=='Description' and p['ref'] in d.parts:
                prop[2]=k.q(d.parts[p['ref']]['role'])
            if name not in ('Reference','Value'):continue
            font=k.kid(k.kid(prop,'effects'),'font')
            font[1]=[k.a('size'),k.a('1.0'),k.a('1.0')]
            if p['power']:
                if p['value']=='+5V' and name=='Value':
                    prop[:]=[x for x in prop if not isinstance(x,list) or k.atom(x[0])!='hide']
                    k.kid(prop,'at')[1:3]=[k.a(k.fmt(p['x'])),k.a(k.fmt(p['y']-2.54))]
                continue
            if p['ref'].startswith(('R','C')):
                x,y=p['x']+3.81,p['y']+(-1.27 if name=='Reference' else 1.27)
                k.kid(prop,'effects').append([k.a('justify'),k.a('left')])
            elif p['ref'] in GATES and p['unit']!=max(GATES[p['ref']]):
                x,y=p['x'],p['y']+(-4.445 if name=='Reference' else 4.445)
                font[1]=[k.a('size'),k.a('.9'),k.a('.9')]
            elif p['ref'] in ('J2','J3','J4'):
                x,y=p['x']+6.35,p['y']+(-1.27 if name=='Reference' else 1.27)
                k.kid(prop,'effects').append([k.a('justify'),k.a('left')])
            elif p['ref']=='J1':
                x,y=389.89,(box[1] if box else p['y'])+(-5.08 if name=='Reference' else -2.54)
                font[1]=[k.a('size'),k.a('.9'),k.a('.9')]
            elif p['ref'].startswith('J'):
                x,y=p['x']+1.27, (box[1] if box else p['y'])+(-5.08 if name=='Reference' else -2.54)
            elif box:
                x,y=p['x']+8.89,box[1]+(-5.08 if name=='Reference' else -2.54)
            else:continue
            k.kid(prop,'at')[1:3]=[k.a(k.fmt(x)),k.a(k.fmt(y))]
        lines.append('  '+k.dump(node))
    for kind,segments in [('wire',s.wires),('bus',d.buses)]:
        for i,(a,b) in enumerate(segments):
            if a==b:continue
            lines.append(f'  ({kind} (pts (xy {k.fmt(a[0])} {k.fmt(a[1])}) (xy {k.fmt(b[0])} {k.fmt(b[1])})) (stroke (width 0) (type default)) (uuid "{ident(kind,i)}"))')
    for i,(a,size) in enumerate(d.entries):
        lines.append(f'  (bus_entry (at {k.fmt(a[0])} {k.fmt(a[1])}) (size {k.fmt(size[0])} {k.fmt(size[1])}) (stroke (width 0) (type default)) (uuid "{ident("entry",i)}"))')
    for i,(x,y) in enumerate(s.junctions):
        lines.append(f'  (junction (at {k.fmt(x)} {k.fmt(y)}) (diameter 0) (color 0 0 0 0) (uuid "{ident("junction",i)}"))')
    for i,((x,y),net,justify) in enumerate(s.labels):
        lines.append(f'  (label {k.dump(k.q(net))} (at {k.fmt(x)} {k.fmt(y)} 0) (effects (font (size .95 .95)) (justify {justify})) (uuid "{ident("label",i)}"))')
    for i,(x,y) in enumerate(s.ncs):
        lines.append(f'  (no_connect (at {k.fmt(x)} {k.fmt(y)}) (uuid "{ident("nc",i)}"))')
    for i,(text,x,y,size) in enumerate([
        ("ANDREW'S Z80 / WIRED A3 COMPARISON",15.24,15.24,1.8),
        ('Same circuit and footprints; separate schematic drawing. Crossings connect only at dots.',15.24,22.86,1.0),
        ('RESET / CLOCK / ADDRESS DECODE',15.24,152.40,1.1),
        ('RS-232 / MAX232',300.99,166.37,1.1),
        ('100nF: C2 U1 / C3 U2 / C4 U3 / C5 U4 / C6 U5 / C7 U6 / C8 U7 / C9 U8 / C10 Y1 / C11 Y2',15.24,255.27,.95),
    ]):
        lines.append(f'  (text {k.dump(k.q(text))} (exclude_from_sim no) (at {x} {y} 0) (effects (font (size {size} {size})) (justify left bottom)) (uuid "{ident("text",i)}"))')
    lines += ['  (sheet_instances (path "/" (page "1")))','  (embedded_fonts no)',')','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    if not args.write:parser.error('Use --write to generate the separate variant')
    spec=json.loads((ROOT/'design/circuit.json').read_text())
    d=Drawing(spec);d.initial_wiring();d.finish()
    DEST.mkdir(parents=True,exist_ok=True)
    if list(DEST.glob('*.lck')):raise SystemExit('Close KiCad before regenerating this variant')
    path=DEST/(NAME+'.kicad_sch');meta=DEST/'provenance.json'
    if path.exists() and meta.exists():
        old=json.loads(meta.read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest()!=old['generated_sha256']:
            raise SystemExit('Wired variant has local edits; refusing to overwrite')
    path.write_text(render(d))
    for name in (NAME+'.kicad_pro','sym-lib-table','fp-lib-table'):
        target=DEST/name
        if not target.exists():shutil.copy2(ROOT/'design'/name,target)
    if not (DEST/'libs').exists():(DEST/'libs').symlink_to('../../libs',target_is_directory=True)
    meta.write_text(json.dumps(dict(status='generated-awaiting-native-checks',
        circuit_source='design/circuit.json',generator='tools/build_wired_schematic.py',
        circuit_spec_sha256=hashlib.sha256((ROOT/'design/circuit.json').read_bytes()).hexdigest(),
        source_schematic_sha256=hashlib.sha256((ROOT/'design'/(NAME+'.kicad_sch')).read_bytes()).hexdigest(),
        placement_method='Explicit functional placement; constrained orthogonal scalar-wire planner',
        generated_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        wire_segments=len(d.sheet.wires),bus_segments=len(d.buses),bus_entries=len(d.entries),
        component_count=len(d.parts),routes=d.routes),indent=2)+'\n')
    print(f'Wrote {path}: {len(d.sheet.wires)} wires, {len(d.entries)} bus entries')


if __name__=='__main__':main()
