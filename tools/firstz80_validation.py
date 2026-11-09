"""Native DRC gate with explicit, reported removal of redundant dangling tails."""
import json
import shutil
import subprocess
import kicad_schematic as k


def drc(board_path,report):
    removed=[]
    for attempt in range(4):
        path=report/('drc.json'if not attempt else f'drc-cleanup-{attempt}.json')
        run=subprocess.run(['kicad-cli','pcb','drc','--schematic-parity','--severity-all','--all-track-errors','--format','json','-o',str(path),str(board_path)],capture_output=True,text=True)
        (report/f'drc-command-{attempt}.log').write_text(run.stdout+run.stderr)
        assert not run.returncode and path.exists(),run.stdout+run.stderr
        data=json.loads(path.read_text())
        tails={item['uuid'] for v in data['violations']if v['type']=='track_dangling' for item in v['items']}
        if not tails or attempt==3:
            if attempt:shutil.copy2(path,report/'drc.json')
            break
        if not removed:shutil.copy2(board_path,report/'before-tail-cleanup.kicad_pcb');shutil.copy2(path,report/'before-tail-cleanup-drc.json')
        tree=k.parse(board_path.read_text());deleted=[]
        keep=[]
        for node in tree:
            uid=k.kid(node,'uuid')if isinstance(node,list)else None
            if isinstance(node,list)and k.atom(node[0])=='segment'and uid and k.atom(uid[1])in tails:deleted.append(k.atom(uid[1]))
            else:keep.append(node)
        assert set(deleted)==tails
        removed.extend(deleted);board_path.write_text(k.dump(keep)+'\n')
    if removed:(report/'tail-cleanup.json').write_text(json.dumps(dict(removed_track_uuids=removed,reason='Native DRC dangling ends; connectivity is rechecked after removal.'),indent=2)+'\n')
    return data
