#!/usr/bin/env python3
"""PSU extension of the cleaned human drawing, in its own project only."""
import copy
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
import kicad_schematic as k

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'schematics/02-wired-a3'
DEST=ROOT/'schematics/03-schematic-7805'
NAME='schematic-7805'
NS=uuid.uuid5(uuid.NAMESPACE_URL,'andrew-codex-z80/7805-extension')


def ident(text):return str(uuid.uuid5(NS,text))


def main():
    target=DEST/(NAME+'.kicad_sch');assert not target.exists(),'Preserve existing edited variant'
    DEST.mkdir(parents=True,exist_ok=True)
    t=k.parse((BASE/'Z80_Simple_Computer.kicad_sch').read_text())
    source=k.parse((ROOT/'firstz80/libs/FirstZ80.kicad_sym').read_text())
    names=('LM7805_TO220','1N5819','Barrel_Jack_MountingPin')
    psulib=DEST/'PSU.kicad_sym'
    psulib.write_text('(kicad_symbol_lib (version 20211014) (generator "codex_snapshot")\n'+'\n'.join(k.dump(d)for d in k.kids(source,'symbol')if k.atom(d[1])in names)+'\n)\n')
    subprocess.run(['kicad-cli','sym','upgrade',str(psulib)],check=True)
    source=k.parse(psulib.read_text())
    defs=k.kid(t,'lib_symbols');library={k.atom(d[1]):d for d in k.kids(defs,'symbol')}
    for d in k.kids(source,'symbol'):
        d[1]=k.q('PSU:'+k.atom(d[1]));defs.append(copy.deepcopy(d));library[k.atom(d[1])]=d
    # Existing +5V input connector becomes an output/test connector; the
    # regulator now drives the rail, so the former +5V PWR_FLAG is removed.
    remove=set();symbols=k.kids(t,'symbol')
    for s in symbols:
        ref=next(k.atom(p[2])for p in k.kids(s,'property')if k.atom(p[1])=='Reference')
        if ref in('#FLG01','#PWR045'):remove.add(k.atom(k.kid(s,'uuid')[1]))
        if ref=='J5':
            next(p for p in k.kids(s,'property')if k.atom(p[1])=='Value')[2]=k.q('+5V OUTPUT')
        for inst in k.kids(s,'instances'):
            for project in k.kids(inst,'project'):project[1]=k.q(NAME)
    for w in k.kids(t,'wire'):
        pts=[tuple(float(k.atom(v))for v in p[1:])for p in k.kids(k.kid(w,'pts'),'xy')]
        if all(x==378.46 and 31.75<=y<=35.56 for x,y in pts):remove.add(k.atom(k.kid(w,'uuid')[1]))
    t[:]=[n for n in t if not(isinstance(n,list)and k.kid(n,'uuid')and k.atom(k.kid(n,'uuid')[1])in remove)]
    def symbol(ref,lid,value,x,y,footprint='',angle=0,fields=None):
        definition=library[lid];pins=k.collect_pins(definition)
        records={n:r[0]for n,r in pins.items()}
        inst=dict(ref=ref,lib_id=lid,value=value,x=x,y=y,unit=1,footprint=footprint,bom=not ref.startswith('#'),on_board=not ref.startswith('#'),pos=not ref.startswith('#'),power=ref.startswith('#'),uuid=ident(ref),
                  pins={n:dict(r,world=(x+r['x'],y-r['y']),uuid=ident(ref+'/'+n))for n,r in records.items()})
        node=k.emit_instance(inst);k.kid(node,'at')[3]=k.a(str(angle))
        project=k.kid(k.kid(node,'instances'),'project');project[1]=k.q(NAME)
        k.kid(project,'path')[1]=k.q('/'+k.atom(k.kid(t,'uuid')[1]))
        for p in k.kids(node,'property'):
            name=k.atom(p[1]);font=k.kid(k.kid(p,'effects'),'font');font[1]=[k.a('size'),k.a('1'),k.a('1')]
            if fields and name in fields:
                xx,yy,just=fields[name];k.kid(p,'at')[1:3]=[k.a(str(xx)),k.a(str(yy))]
                if just!='center':k.kid(p,'effects').append([k.a('justify'),k.a(just)])
            if name=='Value' and ref.startswith('#') and value=='+5V':
                p[:]=[n for n in p if not(isinstance(n,list)and k.atom(n[0])=='hide')]
                k.kid(p,'at')[1:3]=[k.a(str(x)),k.a(str(y-2.54))]
        t.append(node)
    psu_start=len(t)
    symbol('U9','PSU:LM7805_TO220','LM7805',302.26,19.05,'FirstZ80:Package_TO_SOT_THT__TO-220-3_Horizontal_TabDown',fields={'Reference':(302.26,11.43,'center'),'Value':(302.26,13.97,'center')})
    symbol('D1','PSU:1N5819','1N5819',276.86,19.05,'FirstZ80:Diode_THT__D_DO-41_SOD81_P10.16mm_Horizontal',angle=180,fields={'Reference':(276.86,12.7,'center'),'Value':(276.86,15.24,'center')})
    symbol('J6','PSU:Barrel_Jack_MountingPin','9V DC INPUT',243.84,20.32,'FirstZ80:Connector_BarrelJack__BarrelJack_Horizontal',fields={'Reference':(243.84,11.43,'center'),'Value':(243.84,13.97,'center')})
    for ref,value,x,lid,fp,just in [('C30','100nF',260.35,'Device:C',k.FP_C,'left'),('C31','10uF / 25V',289.56,'Device:C_Polarized',k.FP_CP,'right'),('C32','10uF / 16V',321.31,'Device:C_Polarized',k.FP_CP,'left')]:
        xx=x+3.81 if just=='left'else x-3.81
        symbol(ref,lid,value,x,24.13,fp,fields={'Reference':(xx,22.86,just),'Value':(xx,25.4,just)})
    def wire(a,b):
        assert a!=b and(a[0]==b[0]or a[1]==b[1])
        t.append(k.parse(f'(wire(pts(xy {a[0]} {a[1]})(xy {b[0]} {b[1]}))(stroke(width 0)(type default))(uuid "{ident(str((a,b)))}"))'))
    def poly(points):
        for a,b in zip(points,points[1:]):wire(a,b)
    poly([(251.46,17.78),(254.,17.78),(254.,19.05),(260.35,19.05),(273.05,19.05)])
    poly([(280.67,19.05),(289.56,19.05),(294.64,19.05)])
    poly([(309.88,19.05),(321.31,19.05),(331.47,19.05)])
    for x in(260.35,289.56,321.31):wire((x,19.05),(x,20.32));wire((x,27.94),(x,29.21))
    poly([(243.84,27.94),(243.84,29.21),(254.,29.21),(260.35,29.21),(289.56,29.21),(302.26,29.21),(321.31,29.21),(331.47,29.21)])
    poly([(251.46,22.86),(254.,22.86),(254.,29.21)])
    wire((302.26,26.67),(302.26,29.21))
    symbol('#PWR090','power:+5V','+5V',331.47,19.05)
    symbol('#PWR091','power:GND','GND',331.47,29.21)
    symbol('#FLG03','power:PWR_FLAG','PWR_FLAG',266.7,19.05)
    # The protected input also needs a power-output declaration for U9 VI.
    symbol('#FLG04','power:PWR_FLAG','PWR_FLAG',312.42,19.05)
    for x,y in[(254.,29.21),(260.35,19.05),(260.35,29.21),(266.7,19.05),(312.42,19.05),(289.56,19.05),(289.56,29.21),(302.26,29.21),(321.31,19.05),(321.31,29.21)]:
        t.append(k.parse(f'(junction(at {x} {y})(diameter 0)(color 0 0 0 0)(uuid "{ident("dot"+str((x,y)))}"))'))
    for name,pt in [('VIN_9V',(255.27,19.05)),('VIN_PROTECTED',(283.21,19.05))]:
        t.append(k.parse(f'(label "{name}"(at {pt[0]} {pt[1]} 0)(effects(font(size .8 .8))(justify left bottom))(uuid "{ident(name)}"))'))
    # Widen the protected-input span so its flag and name have clear space.
    xmap={294.64:320.04,302.26:327.66,309.88:335.28,321.31:346.71,325.12:350.52,331.47:356.87}
    def widen(node):
        if not isinstance(node,list):return
        if node and k.atom(node[0])in('at','xy'):
            x=float(k.atom(node[1]));node[1]=k.a(str(xmap.get(x,x)))
        for child in node:widen(child)
    for node in t[psu_start:]:widen(node)
    title=k.kid(t,'title_block');k.kid(title,'title')[1]=k.q('Codex Z80 / schematic-7805');k.kid(title,'rev')[1]=k.q('PSU1')
    for text in k.kids(t,'text'):
        if k.atom(text[1])=="ANDREW'S Z80 / WIRED A3 COMPARISON":text[1]=k.q('CODEX Z80 / WIRED A3 + 7805 SUPPLY')
        elif k.atom(text[1]).startswith('Same circuit and footprints;'):text[1]=k.q('Original logic preserved; protected 9V input added. One wire per pin; explicit external branches.')
    for c in k.kids(title,'comment'):
        if k.atom(c[1])=='1':c[2]=k.q('Own wired drawing / protected 9V input and 7805 supply')
        if k.atom(c[1])=='3':c[2]=k.q('4MHz CPU / 1.8432MHz UART / J5 is +5V OUTPUT')
    DEST.mkdir(parents=True,exist_ok=True)
    if not (DEST/'libs').exists():(DEST/'libs').symlink_to('../../libs',target_is_directory=True)
    shutil.copy2(BASE/'Z80_Simple_Computer.kicad_pro',DEST/(NAME+'.kicad_pro'))
    for name in('sym-lib-table','fp-lib-table'):
        data=(BASE/name).read_text();pos=data.rfind(')');extension='kicad_sym'if name.startswith('sym')else'pretty'
        data=data[:pos]+f' (lib(name "FirstZ80")(type "KiCad")(uri "${{KIPRJMOD}}/../../firstz80/libs/FirstZ80.{extension}")(options "")(descr "Historical PSU symbol/footprint snapshot"))\n'+data[pos:]
        tree=k.parse(data)
        if name.startswith('sym'):tree.append(k.parse('(lib (name "PSU") (type "KiCad") (uri "${KIPRJMOD}/PSU.kicad_sym") (options "") (descr "Native-upgraded historical PSU symbols"))'))
        (DEST/name).write_text(k.dump(tree)+'\n')
    target.write_text(k.dump(t)+'\n')
    expected_new={'U9':{'1':'/VIN_PROTECTED','2':'GND','3':'+5V'},'D1':{'1':'/VIN_PROTECTED','2':'/VIN_9V'},'J6':{'1':'/VIN_9V','2':'GND','MP':'GND'},'C30':{'1':'/VIN_9V','2':'GND'},'C31':{'1':'/VIN_PROTECTED','2':'GND'},'C32':{'1':'+5V','2':'GND'}}
    (DEST/'provenance.json').write_text(json.dumps(dict(source='schematics/02-wired-a3',source_sha256=hashlib.sha256((BASE/'Z80_Simple_Computer.kicad_sch').read_bytes()).hexdigest(),expected_added_pin_assignments=expected_new,expected_changed_component_values={'J5':'+5V OUTPUT'},removed_power_symbols=['#FLG01','#PWR045'],source_circuit_pin_assignments_preserved=True),indent=2)+'\n')
    print('Wrote separate schematic-7805; native verification required.')


if __name__=='__main__':main()
