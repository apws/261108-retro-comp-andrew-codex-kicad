#!/usr/bin/env python3
"""Export the checked comparison without changing native board geometry."""
import argparse
import json
import subprocess
from prepare_firstz80 import ROOT,BASE,REPORT,NAME,digest
from route_netgroup import metrics
import pcbnew as pcb


def run(command):
    r=subprocess.run(command,capture_output=True,text=True);assert not r.returncode,r.stdout+r.stderr


def main():
    p=argparse.ArgumentParser();p.add_argument('--partial',action='store_true');a=p.parse_args()
    for method in ('reference-normalized','01-manhattan','02-octilinear','03-freerouting'):
        folder=BASE/method if method=='reference-normalized'else BASE/'layouts'/method
        if method!='reference-normalized'and not(folder/'routing.json').exists():
            assert a.partial,'An experiment is not yet validated';continue
        board=folder/f'{NAME}.kicad_pcb';before=digest(board)
        out=ROOT/'output/firstz80'/method;out.mkdir(parents=True,exist_ok=True)
        for view,layers,mirror in [('signals','F.Cu,B.Cu,F.Silkscreen,Edge.Cuts',False),('top','F.Cu,F.Silkscreen,Edge.Cuts',False),('bottom','B.Cu,Edge.Cuts',True),('placement','F.Fab,F.Silkscreen,Edge.Cuts',False)]:
            command=['kicad-cli','pcb','export','svg','--mode-single','--layers',layers,'--page-size-mode','2','--exclude-drawing-sheet','-o',str(out/f'{view}.svg')]
            if mirror:command.append('--mirror')
            run(command+[str(board)])
            # Darken silk only in the exported review image for contrast on
            # white; the native PCB and its fabrication graphics are intact.
            svg=out/f'{view}.svg';svg.write_text(svg.read_text().replace('#F2EDA1','#595959'))
            run(['rsvg-convert','-w','1800','-b','white','-o',str(out/f'{view}.png'),str(out/f'{view}.svg')])
        assert digest(board)==before
        b=pcb.LoadBoard(str(board));(out/'provenance.json').write_text(json.dumps(dict(board=str(board.relative_to(ROOT)),board_sha256=before,bottom_view='Mirrored, viewed from underside.',**metrics(b)),indent=2)+'\n')
        print(method,flush=True)
    if not a.partial:
        from PIL import Image,ImageDraw,ImageFont
        try:font=ImageFont.truetype('/usr/share/fonts/TTF/DejaVuSans.ttf',28)
        except OSError:font=ImageFont.load_default(size=28)
        panel_w=1200;panel_h=810;margin=24
        sheet=Image.new('RGB',(panel_w*2+margin*3,panel_h*2+margin*3+60),'white');draw=ImageDraw.Draw(sheet)
        for i,(method,title)in enumerate([('reference-normalized','Historical reference'),('01-manhattan','Manhattan A*'),('02-octilinear','Octilinear hybrid'),('03-freerouting','FreeRouting + native DRC correction')]):
            out=ROOT/'output/firstz80'/method;data=json.loads((out/'provenance.json').read_text())
            x=margin+(i%2)*(panel_w+margin);y=margin+(i//2)*(panel_h+margin)
            draw.text((x,y),title,fill='#202020',font=font)
            draw.text((x,y+36),f"{data['vias']} vias | {data['track_length_mm']/1000:.3f} m of copper",fill='#404040',font=font)
            image=Image.open(out/'signals.png').convert('RGB');image.thumbnail((panel_w,panel_h-90),Image.Resampling.LANCZOS);sheet.paste(image,(x,y+88))
        draw.text((margin,panel_h*2+margin*3),'Same historical placement and circuit. Hybrid: own orthogonal seed + 45-degree refinement.',fill='#404040',font=font)
        sheet.save(ROOT/'output/firstz80/comparison.png')


if __name__=='__main__':main()
