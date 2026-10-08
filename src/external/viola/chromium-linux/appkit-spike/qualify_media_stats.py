# SPDX-License-Identifier: GPL-3.0-only
"""Read WebRTC sender diagnostics from the disposable media fixture only."""

def sample(engine, target):
    session = engine.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
    contexts = []
    previous = engine.on_event
    def event(message):
        if message.get('sessionId') == session and message.get('method') == 'Runtime.executionContextCreated':
            contexts.append(message['params']['context']['id'])
        previous(message)
    engine.on_event = event
    try:
        engine.call('Runtime.enable', session=session)
        for context in list(contexts):
            result = engine.call('Runtime.evaluate', {'contextId': context, 'returnByValue': True,
                'awaitPromise': True, 'expression': """(async()=>{
                  const s=window.__violaNativePreviewSource;if(!s)return null;
                  const stats=s.pc?Array.from((await s.pc.getStats()).values()).filter(x=>['outbound-rtp','media-source','remote-inbound-rtp'].includes(x.type)):[];
                  return {status:s.status,error:s.error,parametersError:s.parametersError,
                    parameters:s.pc?.getSenders()[0]?.getParameters(),stats,
                    video:s.video?{time:s.video.currentTime,quality:s.video.getVideoPlaybackQuality(),
                      width:s.video.videoWidth,height:s.video.videoHeight,paused:s.video.paused,
                      visibility:document.visibilityState}:null};
                })()"""}, session=session)
            value = result.get('result', {}).get('value')
            if value:
                return value
        return {'source': 'absent'}
    finally:
        engine.on_event = previous
        engine.call('Target.detachFromTarget', {'sessionId': session})
