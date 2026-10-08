# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the opt-in compositor-native-buffer probe; no GTK window shown."""
import argparse
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import quote
from engine_pipe import EnginePipe
p=argparse.ArgumentParser()
p.add_argument('--engine',type=Path,required=True)
p.add_argument('--work-root',type=Path,required=True)
a=p.parse_args();a.work_root.mkdir(parents=True,exist_ok=True)
profile=Path(tempfile.mkdtemp(prefix='dmabuf-',dir=a.work_root))
e=EnginePipe(a.engine,profile,lambda event:None,native_frame_probe=True)
try:
 t=e.call('Target.createTarget',{'url':'about:blank','newWindow':True,'background':True,'windowState':'minimized'})['targetId']
 s=e.call('Target.attachToTarget',{'targetId':t,'flatten':True})['sessionId']
 fixture='<html><body><h1>GPU transport measurement</h1><div style="width:80vw;height:60vh;background:linear-gradient(blue,cyan);animation:move 1s infinite alternate"></div><style>@keyframes move{to{transform:translateX(30px);filter:hue-rotate(180deg)}}</style></body></html>'
 e.call('Page.navigate',{'url':'data:text/html,'+quote(fixture)},s)
 time.sleep(5)
 gpu=e.call('SystemInfo.getInfo')['gpu']
 targets=e.call('Target.getTargets')['targetInfos']
 report={'classification':'native GPU-buffer feasibility probe; NOT a GTK browser','gpu':gpu.get('devices',[]),'target_types':[x['type'] for x in targets]}
finally:e.close()
lines=(profile.parent/(profile.name+'.log')).read_text(errors='replace').splitlines()
report['native_frame_probe']=[x.split('VIOLA_NATIVE_FRAME_PROBE',1)[1] for x in lines if 'VIOLA_NATIVE_FRAME_PROBE' in x]
report['native_buffer_observed']=any('dmabuf planes=' in x for x in report['native_frame_probe'])
report['gtk_import_and_frame_release']='not implemented'
(a.work_root/'native-capture-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
