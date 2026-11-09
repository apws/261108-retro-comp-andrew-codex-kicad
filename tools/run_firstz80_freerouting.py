#!/usr/bin/env python3
"""Drive an owned local MCP session; retain every request and response."""
import json
import argparse
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports/firstz80/03-freerouting'
INBOX=REPORT/'mcp/inbox'
SEQUENCE_OFFSET=0


def rpc(n,name,arguments):
    request=INBOX/f'{n+SEQUENCE_OFFSET:03d}-{name}.request.json'
    response=request.with_name(request.name.replace('.request.','.response.'))
    assert not request.exists(), 'Do not replay an existing session'
    request.write_text(json.dumps(dict(method='tools/call',params=dict(name=name,arguments=arguments)),indent=2)+'\n')
    deadline=time.monotonic()+90
    while not response.exists():
        assert time.monotonic()<deadline,name
        time.sleep(.2)
    data=json.loads(response.read_text())
    assert 'error' not in data,data
    result=data['result'];assert not result.get('isError'),result
    value=json.loads(result['content'][0]['text']);assert value['status']<300,value
    return value.get('body',value)


def main():
    global SEQUENCE_OFFSET
    parser=argparse.ArgumentParser();parser.add_argument('--attempt',type=int,default=1);parser.add_argument('--no-optimize',action='store_true');args=parser.parse_args()
    SEQUENCE_OFFSET=(args.attempt-1)*100
    session=json.loads(json.loads((INBOX/'001-session.response.json').read_text())['result']['content'][0]['text'])['body']['id']
    job=rpc(2,'enqueue_job',{'body':{'session_id':session,'name':'FirstZ80'}})['id']
    print('MCP job',job,flush=True)
    rpc(3,'upload_job_input_from_local_file',{'jobId':job,'filePath':str(REPORT/'FirstZ80.dsn')})
    settings=dict(autorouter=dict(enabled=True,max_passes=100,max_threads=1,ignore_net_classes=[]),
                  fanout=dict(enabled=False),optimizer=dict(enabled=not args.no_optimize,max_passes=20,max_threads=1,improvement_threshold=1.0),
                  automatic_neckdown=False,copper_to_edge_clearance_um=200,hole_clearance_um=250,
                  clearance_tolerance_um=0,strict_drc=True,plane_nets=[],max_threads=1,job_timeout='00:10:00')
    rpc(4,'update_job_settings',{'path':{'jobId':job},'body':settings})
    rpc(5,'get_effective_settings',{'path':{'jobId':job}})
    rpc(6,'start_job',{'path':{'jobId':job}})
    for n in range(7,80):
        details=rpc(n,'get_job_details',{'path':{'jobId':job},'query':{'compact':True}})
        print(json.dumps(details)[:1600],flush=True)
        state=str(details.get('state',details.get('status',''))).upper()
        if state in ('COMPLETED','FINISHED','FAILED','CANCELLED','TIMED_OUT','TERMINATED'):
            break
        time.sleep(10)
    rpc(n+1,'download_job_output_to_local_file',{'jobId':job,'filePath':str(REPORT/f'FirstZ80-attempt-{args.attempt:02d}.ses')})
    result=rpc(n+2,'get_job_details',{'path':{'jobId':job},'query':{'compact':False}})
    (REPORT/f'job-result-{args.attempt:02d}.json').write_text(json.dumps(result,indent=2)+'\n')
    (REPORT/'mcp/STOP').write_text('FirstZ80 MCP job collected. Do not replay this inbox.\n')


if __name__=='__main__':main()
