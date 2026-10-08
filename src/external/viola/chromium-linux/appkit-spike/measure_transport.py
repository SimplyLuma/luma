# SPDX-License-Identifier: GPL-3.0-only
"""Measure a deliberately non-production frame transport before choosing it."""
import argparse
import base64
import json
from pathlib import Path
import statistics
import tempfile
import time
from urllib.parse import quote
from engine_pipe import EnginePipe

parser = argparse.ArgumentParser()
parser.add_argument('--engine', type=Path, required=True)
parser.add_argument('--work-root', type=Path, required=True)
args = parser.parse_args()
args.work_root.mkdir(parents=True, exist_ok=True)
profile = Path(tempfile.mkdtemp(prefix='transport-', dir=args.work_root))
engine = EnginePipe(args.engine, profile, lambda event: None)
try:
    target = engine.call('Target.createTarget', {'url': 'about:blank'})['targetId']
    session = engine.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
    fixture = '<html><body style="margin:0;font:20px sans-serif;background:#fafafa"><h1>Chromium transport measurement</h1><input placeholder="IME and text input probe"><select><option>Native popup probe</option><option>Second option</option></select><div style="width:100%;height:3000px;background:repeating-linear-gradient(white,#ddd 60px,white 120px)">Scrollable page content</div></body></html>'
    engine.call('Page.navigate', {'url': 'data:text/html,' + quote(fixture)}, session)
    engine.call('Runtime.evaluate', {'expression': 'document.readyState'}, session)
    results = []
    for width, height, scale in [(1024,600,1),(1180,820,1.25),(2560,1440,1)]:
        engine.call('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':scale,'mobile':False},session)
        timings=[]; sizes=[]
        for index in range(12):
            engine.call('Runtime.evaluate', {'expression':f'window.scrollTo(0,{index*30})'},session)
            started=time.monotonic()
            frame=engine.call('Page.captureScreenshot', {'format':'png','captureBeyondViewport':False},session,timeout=30)
            timings.append((time.monotonic()-started)*1000)
            sizes.append(len(base64.b64decode(frame['data'])))
        results.append({'logical_size':[width,height],'scale':scale,'samples':len(timings),'median_capture_ms':round(statistics.median(timings),2),'max_capture_ms':round(max(timings),2),'mean_encoded_bytes':round(statistics.mean(sizes))})
    engine.call('Accessibility.enable', {},session)
    ax=engine.call('Accessibility.getFullAXTree', {},session)
    gpu=engine.call('SystemInfo.getInfo')['gpu']
    report={'gpu_devices':gpu.get('devices',[]),'gpu_feature_status':gpu.get('featureStatus',{}),'classification':'architecture probe; NOT browser acceptance','engine':str(args.engine),'profile':str(profile),'transport':'anonymous DevTools pipe / CPU PNG','measurements':results,'engine_ax_nodes':len(ax['nodes']),'gtk_page_ax_bridge':'not implemented; release blocker','gpu_transport':'not implemented; CPU PNG is rejected for production','ime_popups_dnd_drm_webrtc':'not qualified'}
    output=args.work_root/'transport-report.json';output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
finally:
    engine.close()
