# SPDX-License-Identifier: GPL-3.0-only
"""Populate a disposable real browser model for native layout qualification."""
from urllib.parse import quote
import json
import os


def seed(services, origin, download_directory, media=False):
    report = {'classification': 'disposable browser-service layout fixture', 'tabs': {}, 'groups': []}
    space = services.state['spaces'][0]['id']
    def create(title, new=True):
        url = origin + '/layout?title=' + quote(title)
        services.navigate(url, new_tab=new)
        state = services.wait_state(lambda state: state.get('activeUrl') == url and any(
            tab.get('id') == state.get('activeTabId') and tab.get('title') == title
            for kind in ('today', 'pinned', 'favorites') for tab in state.get(kind, [])))
        report['tabs'][title] = state['activeTabId']
        return state['activeTabId']
    main = create('Main page — layout fixture', new=False)
    for index, title in enumerate(('Mail fixture', 'Calendar fixture', 'Music fixture', 'Maps fixture')):
        tab = create(title)
        services.send('sidebar', 'tab:reorder', {'tabId': tab, 'spaceId': space,
                                               'section': 'favorite', 'index': index})
        services.wait_state(lambda state: any(t['id'] == tab for t in state.get('favorites', [])))
    create('Shopping — long tab title layout fixture')
    for title, names in (('Social Media', ('Community fixture', 'Marketplace fixture', 'Professional fixture')),
                         ('Reading', ('Article one fixture', 'Article two fixture'))):
        tabs = [create(name) for name in names]
        services.send('sidebar', 'tabGroup:groupTab', {'tabId': tabs[1], 'targetTabId': tabs[0],
            'groupId': None, 'spaceId': space, 'index': 1})
        state = services.wait_state(lambda state: any(set(tabs[:2]).issubset(g.get('tabIds', []))
                                                     for g in state.get('tabGroups', [])))
        group = next(g['id'] for g in state['tabGroups'] if set(tabs[:2]).issubset(g.get('tabIds', [])))
        for index, tab in enumerate(tabs[2:], 2):
            services.send('sidebar', 'tabGroup:groupTab', {'tabId': tab, 'targetTabId': None,
                'groupId': group, 'spaceId': space, 'index': index})
        services.send('sidebar', 'tabGroup:rename', {'groupId': group, 'spaceId': space, 'title': title})
        services.wait_state(lambda state: any(g['id'] == group and g.get('title') == title
                                              for g in state.get('tabGroups', [])))
        if title == 'Social Media':
            description = services.open_menu('tabGroup:menu', {'groupId': group, 'spaceId': space})
            row = next(row for row in description['items'] if row['label'] == 'Keep expanded')
            if not row.get('checked'):
                services.activate_menu(description['nonce'], [row['index']])
            else:
                services.dismiss_menu(description['nonce'])
        report['groups'].append(group)
    services.send('sidebar', 'tab:activate', {'tabId': main})
    services.wait_state(lambda state: state.get('activeTabId') == main)
    report['favorite_count'] = len(services.state.get('favorites', []))
    download_directory.mkdir(parents=True, exist_ok=True)
    services.engine.call('Browser.setDownloadBehavior', {
        'behavior': 'allow', 'downloadPath': str(download_directory), 'eventsEnabled': True})
    # Unique loopback URLs identify this fixture only; production input uses
    # the exact target identity carried with each captured browser frame.
    target = next(t['targetId'] for t in services.engine.call('Target.getTargets')['targetInfos']
                  if t['type'] == 'page' and t['url'] == services.state['activeUrl'])
    session = services.engine.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
    services.engine.call('Runtime.evaluate', {'expression': 'location.assign(' + json.dumps(origin + '/download') + ')',
                                              'userGesture': True}, session=session)
    services.wait_state(lambda state: bool(state.get('notifications')))
    report['notification_count'] = len(services.state['notifications'])
    services.engine.call('Target.detachFromTarget', {'sessionId': session})
    if media:
        services.send('sidebar', 'tab:activate', {'tabId': report['tabs']['Music fixture']})
        services.wait_state(lambda state: state.get('activeTabId') == report['tabs']['Music fixture'])
        music_url = origin + '/layout?title=' + quote('Music fixture')
        target = next(t['targetId'] for t in services.engine.call('Target.getTargets')['targetInfos']
                      if t['type'] == 'page' and t['url'] == music_url)
        session = services.engine.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        script = """(async () => {
            const video = document.createElement('video'); video.controls = true;
            video.loop = true; video.src = '/fixture.webm'; document.body.replaceChildren(video);
            navigator.mediaSession.metadata = new MediaMetadata({title:'Native media fixture', artist:'Chromium test media'});
            navigator.mediaSession.setActionHandler('play', () => video.play());
            navigator.mediaSession.setActionHandler('pause', () => video.pause());
            await video.play(); navigator.mediaSession.playbackState = 'playing'; return true;
        })()"""
        if os.environ.get('VIOLA_QA_MEDIA_IFRAME') == '1':
            script = """(async () => {
                const frame=document.createElement('iframe');
                frame.style='width:100%;height:600px;border:0';
                frame.allow='autoplay; picture-in-picture';
                const loaded=new Promise(resolve => frame.onload=resolve);
                frame.srcdoc='<body style="margin:0"></body>';
                document.body.replaceChildren(frame); await loaded;
                return await frame.contentWindow.eval(""" + json.dumps(script) + """);
            })()"""
            report['media_embedded'] = True
        result = services.engine.call('Runtime.evaluate', {'expression': script,
            'userGesture': True, 'awaitPromise': True}, session=session)
        if result.get('exceptionDetails'):
            raise RuntimeError('Existing Chromium media fixture did not play')
        try:
            services.wait_state(lambda state: bool(state.get('media')))
        except TimeoutError as error:
            diagnostics = services.engine.call('Runtime.evaluate', {'expression':
                "(() => { const v=document.querySelector('video'); return {paused:v.paused, time:v.currentTime, duration:v.duration, muted:v.muted, volume:v.volume, ready:v.readyState, visibility:document.visibilityState, playback:navigator.mediaSession.playbackState}; })()",
                'returnByValue': True}, session=session)
            raise RuntimeError('Media service unavailable: ' + json.dumps(diagnostics)) from error
        report['media_target'] = target
        report['media_title'] = services.state['media'].get('title')
        if os.environ.get('VIOLA_QA_MEDIA_FOREGROUND') != '1':
            services.send('sidebar', 'tab:activate', {'tabId': main})
            services.wait_state(lambda state: state.get('activeTabId') == main)
        report['media_foreground'] = services.state.get('activeTabId') == services.state['media']['tabId']
        services.engine.call('Target.detachFromTarget', {'sessionId': session})
    return report
