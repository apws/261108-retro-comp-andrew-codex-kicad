#!/usr/bin/env python3
"""Retry a fresh grid experiment with explicitly recorded failed-net priorities."""
import argparse
import json
import subprocess
import sys
from prepare_firstz80 import ROOT,REPORT


def main():
    p=argparse.ArgumentParser();p.add_argument('method',choices=['manhattan','octilinear']);p.add_argument('--start',type=int,default=3);p.add_argument('--last',type=int,default=8);a=p.parse_args()
    folder=REPORT/('01-manhattan'if a.method=='manhattan'else'02-octilinear')
    priorities=[]
    for file in sorted(folder.glob('attempt-*/summary.json')):
        data=json.loads(file.read_text())
        if data['status']=='validated':print('Already validated:',file);return
        failed=[f['net']for f in data['failures']]
        priorities=failed+[n for n in priorities if n not in failed]
    for attempt in range(a.start,a.last+1):
        command=[sys.executable,str(ROOT/'tools/route_firstz80.py'),a.method,'--attempt',str(attempt)]
        for net in priorities:command+=['--priority',net]
        print('Starting',attempt,'priorities',priorities,flush=True)
        with(folder/f'attempt-{attempt:02d}.log').open('w')as out:
            subprocess.run(command,cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True)
        data=json.loads((folder/f'attempt-{attempt:02d}/summary.json').read_text())
        print('Finished',attempt,data['status'],'unconnected',data['unconnected'],'violations',data['violations'],flush=True)
        if data['status']=='validated':return
        failed=[f['net']for f in data['failures']]
        priorities=failed+[n for n in priorities if n not in failed]
    raise SystemExit('Attempts remain rejected; inspect evidence before further changes.')


if __name__=='__main__':main()
