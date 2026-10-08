import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {NotificationExport} from './notificationExport.js';
let now = 0, locked = false, optedIn = false, actionOptedIn = false, activated = 0;
const source = {notifications: []};
const action = {label: 'Open', activate() {activated++;}};
const notification = {title: 'Synthetic', body: 'Private fixture', actions: [action], active: true};
source.notifications.push(notification);
const policy = new NotificationExport({randomId: () => randomBytes(16).toString('hex'),
    now: () => now, sourceAllowed: () => optedIn, actionAllowed: () => actionOptedIn,
    isLocked: () => locked, isActionable: n => n.active});
const owner = ':1.20', peer = 'a'.repeat(64), session = 'b'.repeat(32);
const snapshot = () => policy.snapshot(owner, peer, session, [source]);
const invoke = (token, id = 'c'.repeat(32)) => policy.invoke(owner, peer, session, token, id);
assert.throws(snapshot, /unauthorized/);
policy.bind(owner, peer, session);
assert.equal(snapshot().records.length, 0); // source default deny
optedIn = true;
assert.equal(snapshot().records[0].actions.length, 0); // separate action opt-in
assert.throws(() => policy.snapshot(':1.99', peer, session, [source]), /unauthorized/);
assert.throws(() => policy.snapshot(owner, 'd'.repeat(64), session, [source]), /unauthorized/);
actionOptedIn = true;
let token = snapshot().records[0].actions[0].id;
assert.equal(invoke(token), 'complete');
assert.equal(invoke(token), 'complete');
assert.equal(activated, 1);
assert.throws(() => invoke(token, 'e'.repeat(32)), /stale-action/);
token = snapshot().records[0].actions[0].id;
assert.throws(() => invoke(token), /request-conflict/);
policy.changed(); // replacement, even same source object/title
assert.throws(() => invoke(token, 'e'.repeat(32)), /stale-action/);
token = snapshot().records[0].actions[0].id;
now = 31;
assert.throws(() => invoke(token, 'e'.repeat(32)), /stale-action/);
token = snapshot().records[0].actions[0].id;
notification.actions = [];
assert.throws(() => invoke(token, 'e'.repeat(32)), /stale-action/);
notification.actions = [action];
token = snapshot().records[0].actions[0].id;
locked = true;
assert.throws(() => invoke(token, 'e'.repeat(32)), /locked/);
assert.equal(snapshot().records.length, 0);
locked = false;
assert.throws(() => invoke(token, 'e'.repeat(32)), /stale-action/);
token = snapshot().records[0].actions[0].id;
optedIn = false;
assert.throws(() => invoke(token, 'e'.repeat(32)), /stale-action/);
optedIn = true;
token = snapshot().records[0].actions[0].id;
policy.revoke();
assert.throws(() => invoke(token, 'e'.repeat(32)), /unauthorized/);
assert.equal(activated, 1);
console.log('PASS notification policy: default deny, owner/peer, action opt-in, one-shot/dedup, replacement, expiry, removal, lock, source revoke, session revoke');
