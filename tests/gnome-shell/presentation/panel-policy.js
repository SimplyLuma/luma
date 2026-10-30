// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import {panelProgress, panelSnap, notificationAccess} from '../../../src/luma-shell-presentation/layout.js';
assert.equal(panelProgress(0, 150, 300), .5);
assert.equal(panelProgress(0, 60, 300), .2, 'reversing the same gesture reverses the panel');
assert.equal(panelProgress(1, -180, 300), .4);
assert.equal(panelProgress(1, -30, 300), .9, 'an aborted dismissal returns toward open');
assert.equal(panelProgress(0, -5, 300), 0);
assert.equal(panelProgress(0, 600, 300), 1);
assert.equal(panelSnap(.2), 0); assert.equal(panelSnap(.5), 1);
assert.equal(panelSnap(.2, 800), 1); assert.equal(panelSnap(.8, -800), 0);
const state={mode:'mobile',locked:true,greeter:false,hasNotifications:false,showOnLock:true};
assert.deepEqual(notificationAccess(state),{visible:true,details:true,activate:false});
for (const mode of ['tablet','desktop',undefined])
    assert.equal(notificationAccess({...state,mode}).visible,false,`${mode} lock screen must stay unchanged`);
for (const hidden of [{showOnLock:false},{sourceOnLock:false},{sourceEnabled:false},{greeter:true}])
    assert.deepEqual(notificationAccess({...state,...hidden}),{visible:false,details:false,activate:false});
assert.deepEqual(notificationAccess({...state,detailsOnLock:false}),{visible:true,details:false,activate:false});
assert.deepEqual(notificationAccess({...state,mode:'desktop',locked:false,hasNotifications:true}),
    {visible:true,details:true,activate:true});
console.log('PASS reversible panel progress, release thresholds/flings, Mobile-only lock visibility and privacy');
