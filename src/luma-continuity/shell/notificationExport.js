// SPDX-License-Identifier: Apache-2.0
// Experimental policy hosted by the existing Shell notification owner.
// No network transport, bus ownership, actor scraping or producer tokens.
export class NotificationExport {
    constructor({randomId, now, sourceAllowed = () => false,
        actionAllowed = () => false, isLocked = () => true,
        isActionable = () => false}) {
        this.randomId = randomId;
        this.now = now;
        this.sourceAllowed = sourceAllowed;
        this.actionAllowed = actionAllowed;
        this.isLocked = isLocked;
        this.isActionable = isActionable;
        this.generation = 0;
        this.records = new Map();
        this.actions = new Map();
        this.receipts = new Map();
        this.owner = null;
        this.peer = null;
        this.session = null;
    }

    bind(owner, peer, session) {
        if (!/^:[0-9]+\.[0-9]+$/.test(owner) || !/^[0-9a-f]{64}$/.test(peer) ||
            !/^[0-9a-f]{32}$/.test(session))
            throw new Error('invalid-session');
        this.revoke();
        this.owner = owner;
        this.peer = peer;
        this.session = session;
    }

    revoke() {
        this.generation++;
        this.records.clear();
        this.actions.clear();
        this.receipts.clear();
        this.owner = null;
        this.peer = null;
        this.session = null;
    }

    changed() {
        // Called synchronously on replacement (even identical properties),
        // removal, policy/lock changes and action mutation by the source owner.
        const removed = [...this.records.keys()];
        this.generation++;
        this.records.clear();
        this.actions.clear();
        return {generation: this.generation, removed};
    }

    _authenticate(owner, peer, session) {
        if (!this.owner || owner !== this.owner || peer !== this.peer || session !== this.session)
            throw new Error('unauthorized');
    }

    snapshot(owner, peer, session, sources) {
        this._authenticate(owner, peer, session);
        const revoked = this.changed();
        if (this.isLocked())
            return {...revoked, records: []};
        const records = [];
        for (const source of sources.slice(0, 128)) {
            if (!this.sourceAllowed(source, peer))
                continue;
            for (const notification of source.notifications.slice(0, 64)) {
                if (records.length >= 100)
                    break;
                if (!this.isActionable(notification))
                    continue;
                const id = this.randomId();
                const actions = [];
                for (const action of notification.actions.slice(0, 4)) {
                    if (!this.actionAllowed(source, notification, action, peer))
                        continue;
                    const token = this.randomId();
                    this.actions.set(token, {source, notification, action,
                        generation: this.generation, expires: this.now() + 30});
                    actions.push({id: token, label: String(action.label).slice(0, 128)});
                }
                // Export text only. A receiver must render it as plain text.
                const record = {id, title: String(notification.title ?? '').slice(0, 512),
                    body: String(notification.body ?? '').slice(0, 4096), actions};
                this.records.set(id, notification);
                records.push(record);
            }
        }
        return {...revoked, records};
    }

    invoke(owner, peer, session, token, requestId) {
        this._authenticate(owner, peer, session);
        if (this.isLocked()) {
            this.changed();
            throw new Error('locked');
        }
        if (!/^[0-9a-f]{32}$/.test(requestId))
            throw new Error('invalid-request');
        const previous = this.receipts.get(requestId);
        if (previous) {
            if (previous.token !== token)
                throw new Error('request-conflict');
            return previous.state;
        }
        const entry = this.actions.get(token);
        if (!entry || entry.generation !== this.generation || entry.expires <= this.now() ||
            !this.sourceAllowed(entry.source, peer) ||
            !entry.source.notifications.includes(entry.notification) ||
            !this.isActionable(entry.notification) ||
            !entry.notification.actions.includes(entry.action) ||
            !this.actionAllowed(entry.source, entry.notification, entry.action, peer))
            throw new Error('stale-action');
        if (this.receipts.size >= 1000)
            throw new Error('session-quota');
        this.actions.delete(token);
        const receipt = {token, state: 'unknown'};
        this.receipts.set(requestId, receipt);
        // Native owner Action.activate() owns lifecycle and callback dispatch.
        entry.action.activate();
        receipt.state = 'complete';
        return receipt.state;
    }
}
