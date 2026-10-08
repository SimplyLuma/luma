"""One-shot account recovery driven by native lifecycle events.

No wake locks, transport watchdog or credential replay. Authorization lease
refresh remains required until the server supports revocation events. Timers
belong to this owner and are cancelled while asleep, offline or locked.
"""
import random
import time


class Recovery:
    def __init__(self, run, *, later, cancel, now=time.time, monotonic=time.monotonic, jitter=random.uniform):
        self.run,self.later,self.cancel,self.now,self.jitter=run,later,cancel,now,jitter
        self.monotonic=monotonic
        self.online=True;self.awake=True;self.closed=False
        self.timer=None;self.attempt=0;self.pending=False;self.running=False

    def _cancel(self):
        if self.timer is not None:self.cancel(self.timer);self.timer=None

    def environment(self, *, online=None, awake=None):
        if online is not None:self.online=bool(online)
        if awake is not None:self.awake=bool(awake)
        self._cancel()
        if self.online and self.awake:self.request()

    def request(self):
        if self.closed:return
        self._cancel();self.attempt=0
        if not self.online or not self.awake:return
        if self.running:self.pending=True;return
        self._arm(0.05)

    def _arm(self, delay):
        if self.closed or not self.online or not self.awake:return
        def fire():
            self.timer=None
            if self.closed or not self.online or not self.awake:return False
            self.running=True
            if self.run() is False:
                self.running=False;self.pending=True
            return False
        self.timer=self.later(delay,fire)

    def completed(self, state):
        self.running=False
        if self.closed:return
        self._cancel()
        if not self.awake or not self.online:return
        if self.pending:
            self.pending=False;self.request();return
        if state.get('account_status') in {'locked','reauth_required'}:return
        if state.get('account_status')=='signed_out' and not state.get('stale'):return
        if state.get('service_status')=='ready' and not state.get('stale'):
            self.attempt=0
            # The account owner supplies a bounded authorization lease. Refresh
            # at its expiry rather than waking every few seconds while idle.
            deadline=state.get('valid_until')
            if isinstance(deadline,(int,float)):
                remaining=deadline-self.now()
                if isinstance(state.get('lease_deadline_monotonic'),(int,float)):
                    remaining=min(remaining,state['lease_deadline_monotonic']-self.monotonic())
                # Leave room for a full refresh round trip, including token
                # rotation. The lease owner revokes live call channels at the
                # deadline, so a refresh that finishes late is a teardown.
                self._arm(max(1,remaining-3))
        else:
            # Continue at a bounded 48–60 second interval while awake/routable, so a server
            # recovery on an unchanged network does not require a manual nudge.
            delay=min(60,2**(self.attempt+1))*self.jitter(.8,1)
            self.attempt=min(5,self.attempt+1);self._arm(delay)

    def close(self):
        self.closed=True;self.pending=False;self._cancel()
