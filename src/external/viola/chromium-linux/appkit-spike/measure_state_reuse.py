# SPDX-License-Identifier: GPL-3.0-only
"""Verify the native-host probe can retain Viola's existing state/commands."""
import argparse
import json
from pathlib import Path
import tempfile
import time
from engine_pipe import EnginePipe
p=argparse.ArgumentParser();p.add_argument('--engine',type=Path,required=True);p.add_argument('--work-root',type=Path,required=True);a=p.parse_args();a.work_root.mkdir(parents=True,exist_ok=True)
profile=Path(tempfile.mkdtemp(prefix='state-',dir=a.work_root))
e=EnginePipe(a.engine,profile,lambda event:None)
try:
 t=e.call('Target.createTarget',{'url':'about:blank'})['targetId']
 shell=None
 for _ in range(40):
  targets=e.call('Target.getTargets')['targetInfos']
  shell=next((x for x in targets if x['url']=='chrome://viola/'),None)
  if shell:break
  time.sleep(.1)
 if not shell:raise RuntimeError('Existing trusted Viola shell did not initialize')
 session=e.call('Target.attachToTarget',{'targetId':shell['targetId'],'flatten':True})['sessionId']
 def evaluate(code):
  r=e.call('Runtime.evaluate',{'expression':code,'returnByValue':True},session)
  if r.get('exceptionDetails'):raise RuntimeError(str(r['exceptionDetails']))
  return r['result'].get('value')
 state=None
 for _ in range(40):
  state=evaluate('typeof S !== "undefined" && S ? ({keys:Object.keys(S),tabs:[...(S.favorites||[]),...(S.pinned||[]),...(S.today||[])].map(t=>({id:t.id,title:t.title})),workspaces:S.spaces?.length,settings:!!S.settings}) : null')
  if state:break
  time.sleep(.1)
 if not state:raise RuntimeError('Existing Viola render state is unavailable')
 tabs_expr = '[...(S.favorites||[]),...(S.pinned||[]),...(S.today||[])]'
 tab_id = state['tabs'][0]['id']
 muted_expr = tabs_expr + '.find(t=>t.id===' + json.dumps(tab_id) + ').muted'
 before = evaluate(muted_expr)
 command = 'vela.send("tab:toggleMute", {tabId:' + json.dumps(tab_id) + '}); true'
 evaluate(command)
 for _ in range(40):
  after = evaluate(muted_expr)
  if after != before:break
  time.sleep(.1)
 evaluate(command)
 for _ in range(40):
  restored = evaluate(muted_expr)
  if restored == before:break
  time.sleep(.1)
 report={'classification':'existing browser service reuse probe; not UI acceptance','state_keys':state['keys'],'initial_tabs':len(state['tabs']),'existing_command':'tab:toggleMute','before':before,'after':after,'restored':restored,'existing_command_reused':after!=before and restored==before,'new_database_or_tab_model':False}
 (a.work_root/'state-reuse-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
finally:e.close()
