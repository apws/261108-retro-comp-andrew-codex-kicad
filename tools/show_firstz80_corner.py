#!/usr/bin/env python3
"""Show actual native track geometry at one of the hybrid's 45-degree corners."""
import html
import json
import math
import subprocess
from pathlib import Path
import pcbnew as pcb
from prepare_firstz80 import ROOT,BASE,REPORT,NAME,digest


def main():
    report=REPORT/'02-octilinear/refinement-92'
    data=json.loads((report/'corners.json').read_text())
    index=next(i for i in data['accepted']if data['proposals'][i]['cut_nm']==500000)
    corner=data['proposals'][index];cx,cy=[v/1000000 for v in corner['point']]
    layer=pcb.B_Cu if corner['layer']=='B.Cu'else pcb.F_Cu
    bounds=(cx-2.2,cy-2.2,cx+2.2,cy+2.2);scale=118
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1240" height="780" viewBox="0 0 1240 780">',
         '<rect width="1240" height="780" fill="white"/>',
         '<style>text{font-family:DejaVu Sans,sans-serif;fill:#252525} .grid{stroke:#e5e9ee;stroke-width:1}</style>',
         '<text x="30" y="38" font-size="26">The hybrid changes small corners, not the routing topology</text>',
         '<text x="30" y="72" font-size="18">A00 on B.Cu, enlarged equally in both panels. Actual native track widths and coordinates.</text>']
    hashes={}
    for n,(method,title)in enumerate([('01-manhattan','Manhattan: right-angle bend'),('02-octilinear','Hybrid: short 45-degree diagonal')]):
        path=BASE/'layouts'/method/f'{NAME}.kicad_pcb';hashes[method]=digest(path)
        board=pcb.LoadBoard(str(path));x=30+n*610;y=140;size=4.4*scale
        svg.append(f'<text x="{x}" y="112" font-size="21">{html.escape(title)}</text>')
        svg.append(f'<defs><clipPath id="panel{n}"><rect x="{x}" y="{y}" width="{size}" height="{size}"/></clipPath></defs>')
        for step in range(9):
            xx=x+step*.5*scale;yy=y+step*.5*scale
            svg.append(f'<line class="grid" x1="{xx}" y1="{y}" x2="{xx}" y2="{y+size}"/>')
            svg.append(f'<line class="grid" x1="{x}" y1="{yy}" x2="{x+size}" y2="{yy}"/>')
        def xy(p):return x+(pcb.ToMM(p.x)-bounds[0])*scale,y+(pcb.ToMM(p.y)-bounds[1])*scale
        svg.append(f'<g clip-path="url(#panel{n})">')
        tracks=list(board.GetTracks());tracks.sort(key=lambda t:t.GetNetname()=='/A00');holes=[]
        for track in tracks:
            color='#2459a6'if track.GetNetname()=='/A00'else'#c4cbd3'
            if track.Type()==pcb.PCB_VIA_T:
                xx,yy=xy(track.GetPosition());radius=pcb.ToMM(track.GetWidth(layer))*scale/2
                if x-radius<=xx<=x+size+radius and y-radius<=yy<=y+size+radius:
                    svg.append(f'<circle cx="{xx}" cy="{yy}" r="{radius}" fill="{color}"/>')
                    holes.append(f'<circle cx="{xx}" cy="{yy}" r="{pcb.ToMM(track.GetDrillValue())*scale/2}" fill="white"/>')
            elif track.GetLayer()==layer:
                a,b=xy(track.GetStart()),xy(track.GetEnd())
                svg.append(f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="{color}" stroke-width="{pcb.ToMM(track.GetWidth())*scale}" stroke-linecap="round"/>')
        svg.extend(holes);svg.append('</g>')
        svg.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="none" stroke="#9aa7b5"/>')
        # The orange ring is an annotation, not additional PCB copper.
        if n:
            midpoint=[sum(v)/2/1000000 for v in zip(*corner['ends'])]
            xx=x+(midpoint[0]-bounds[0])*scale;yy=y+(midpoint[1]-bounds[1])*scale
            svg.append(f'<circle cx="{xx}" cy="{yy}" r="60" fill="none" stroke="#d1751a" stroke-width="2.5" stroke-dasharray="7 5"/>')
        svg.append(f'<text x="{x}" y="{y+size+34}" font-size="17">Grid: 0.5 mm. Tracks and vias only; component pads omitted.</text>')
        assert digest(path)==hashes[method]
    svg.extend(['<text x="30" y="728" font-size="20">This cut removes 0.5 mm from each leg: diagonal length 0.707 mm at 45 degrees.</text>',
                '<text x="30" y="758" font-size="18">Across the board: 160 small corner cuts; 424 vias retained; copper length reduced by only 0.264%.</text>','</svg>'])
    out=ROOT/'output/firstz80';path=out/'octilinear-corner-detail.svg';path.write_text('\n'.join(svg)+'\n')
    subprocess.run(['rsvg-convert','-w','1860','-o',str(out/'octilinear-corner-detail.png'),str(path)],check=True)
    (out/'octilinear-corner-detail.json').write_text(json.dumps(dict(board_sha256=hashes,accepted_corner_index=index,corner=corner,view_bounds_mm=bounds,
                      note='Native copper geometry; component pads omitted; orange dashed circle is annotation only.'),indent=2)+'\n')
    print(path)


if __name__=='__main__':main()
