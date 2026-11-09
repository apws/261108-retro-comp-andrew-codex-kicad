#!/usr/bin/env python3
"""Independent geometry audit: wires must not cross visible component pins.

Tests the open pin stroke, allowing contact at the electrical anchor. This
is additional to native ERC and netlist equality, not a text/font audit.
"""
import argparse
import json
import math
from pathlib import Path
import kicad_schematic as k


def inspect(path):
    tree=k.parse(path.read_text());library={k.atom(s[1]):s for s in k.kids(k.kid(tree,'lib_symbols'),'symbol')}
    wires=[(tuple(tuple(float(k.atom(v))for v in p[1:])for p in k.kids(k.kid(w,'pts'),'xy')),k.atom(k.kid(w,'uuid')[1]))for w in k.kids(tree,'wire')]
    hits=[];pin_count=0
    for symbol in k.kids(tree,'symbol'):
        ref=next(k.atom(p[2])for p in k.kids(symbol,'property')if k.atom(p[1])=='Reference')
        if ref.startswith('#'):continue
        at=k.kid(symbol,'at');x,y,rotation=[float(k.atom(v))for v in at[1:]];angle=math.radians(rotation)
        assert not k.kid(symbol,'mirror'),'Mirror transform needs explicit support'
        unit=int(k.atom(k.kid(symbol,'unit')[1]));definition=library[k.atom(k.kid(symbol,'lib_id')[1])];bare=k.atom(definition[1]).split(':')[1]
        def world(px,py):return round(x+math.cos(angle)*px-math.sin(angle)*py,5),round(y-math.sin(angle)*px-math.cos(angle)*py,5)
        for sub in k.kids(definition,'symbol'):
            suffix=k.atom(sub[1])[len(bare)+1:];u,style=map(int,suffix.split('_'))
            if u not in(0,unit)or style not in(0,1):continue
            for pin in k.kids(sub,'pin'):
                if any(k.atom(v)=='hide'for v in pin if isinstance(v,tuple)):continue
                p=k.kid(pin,'at');px,py,heading=[float(k.atom(v))for v in p[1:]];length=float(k.atom(k.kid(pin,'length')[1]));theta=math.radians(heading)
                a=world(px,py);b=world(px+length*math.cos(theta),py+length*math.sin(theta));pin_count+=1
                for (c,d),uid in wires:
                    eps=.0001;hit=False
                    if abs(a[0]-b[0])<eps:
                        if abs(c[1]-d[1])<eps:
                            hit=min(c[0],d[0])-eps<=a[0]<=max(c[0],d[0])+eps and min(a[1],b[1])+eps<c[1]<max(a[1],b[1])-eps
                        elif abs(c[0]-d[0])<eps and abs(c[0]-a[0])<eps:
                            hit=min(max(c[1],d[1]),max(a[1],b[1]))-max(min(c[1],d[1]),min(a[1],b[1]))>eps
                    else:
                        if abs(c[0]-d[0])<eps:
                            hit=min(c[1],d[1])-eps<=a[1]<=max(c[1],d[1])+eps and min(a[0],b[0])+eps<c[0]<max(a[0],b[0])-eps
                        elif abs(c[1]-d[1])<eps and abs(c[1]-a[1])<eps:
                            hit=min(max(c[0],d[0]),max(a[0],b[0]))-max(min(c[0],d[0]),min(a[0],b[0]))>eps
                    if hit:hits.append(dict(reference=ref,pin=k.atom(k.kid(pin,'number')[1]),wire_uuid=uid,wire_mm=[c,d],pin_mm=[a,b]))
    return dict(schematic=str(path),checked_component_pin_strokes=pin_count,wire_pin_stroke_intersections=len(hits),findings=hits,scope='Geometry of non-power-symbol pin strokes. Text and complete symbol-body clearance also require rendered review.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('schematic',type=Path);parser.add_argument('--output',type=Path);a=parser.parse_args();result=inspect(a.schematic)
    if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if result['findings']:raise SystemExit(1)


if __name__=='__main__':main()
