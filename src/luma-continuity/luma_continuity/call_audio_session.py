"""One explicitly consented call owns signaling, media and native/desktop PCM.

The UI/native-call owner supplies the established pinned transport and current
call observation. This class never discovers a peer, grants permission, dials,
answers, replays a call command, or reconnects a failed audio session.
"""
from .call_signaling import PairedAudioSignals
from .call_webrtc import WebRTCAudio
from .call_audio_lease import NativeAudioLease


class CallAudioSession:
    def __init__(self,stream,directory,peer,*,account,epoch,call,session,incoming,
                 current_call,consented,dispatch,failed,native_current_call=None,
                 lease_factory=NativeAudioLease,channel_factory=None,changed=lambda:None):
        self.call,self.current_call,self.consented=call,current_call,consented
        self.dispatch,self.failed,self.changed_callback=dispatch,failed,changed
        self.connected=False;self.muted=False;self.pending_signals=[]
        self.closed=False;self.started=False;self.ready=False
        self.engine=self.signals=self.pcm=self.lease=None
        self.stream=stream;self.native_current_call=native_current_call;self.lease_factory=lease_factory
        self.channel_factory=channel_factory
        self.arguments=(directory,peer)
        self.binding=dict(account=account,epoch=epoch,call=call,session=session,incoming=incoming)

    def valid(self):
        return not self.closed and self.consented() and self.current_call()==self.call

    def start(self,*,offer):
        if self.started or self.closed:raise RuntimeError('one audio session required')
        if not self.valid():self.close();raise PermissionError('explicit current-call audio consent required')
        self.started=True;self.offer=bool(offer)
        try:
            self.signals=PairedAudioSignals(self.stream,*self.arguments,**self.binding,authorized=self.valid,
                receive=self._receive_signal,failed=self._lost,dispatch=self.dispatch,
                channel_factory=self.channel_factory)
            self.engine=WebRTCAudio(authorized=self.valid,signal=self.signals.send,pcm=self._pcm,
                failed=self._lost,dispatch=self.dispatch,connected=self._connected)
            if self.native_current_call is not None:
                native_call=self.native_current_call()
                self.lease=self.lease_factory(authorized=self.valid,current_call=self.native_current_call,dispatch=self.dispatch)
                # Join the authenticated signaling channel before interactive native
                # consent. No pipeline or PCM lease is opened by signaling.
                self.signals.start()
                self.lease.start(native_call,self._native_ready,self._lost)
            else:
                # Microphone and speakers belong to the clocked media pipeline.
                self.engine.start(offer=self.offer,endpoint='desktop')
                self._desktop_ready()
        except Exception:
            self.close();raise

    def _receive_signal(self,message):
        if not self.valid():raise PermissionError('audio consent changed')
        if self.native_current_call is not None and not self.ready:
            if len(self.pending_signals)>=34:raise ValueError('pending audio signal bound')
            self.pending_signals.append(message)
        else:self.engine.receive(message)

    def _desktop_ready(self):
        if not self.valid():self.close();return
        try:self.ready=True;self.signals.start();self.changed_callback()
        except Exception:self._lost()

    def _connected(self):
        if not self.valid():return
        self.connected=True;self.dispatch(lambda:(self.changed_callback(),False)[1])

    def set_muted(self,muted):
        if type(muted) is not bool or not self.valid() or not self.connected:raise PermissionError('active call audio required')
        self.engine.set_muted(muted);self.muted=muted
        self.changed_callback()

    def _native_ready(self,_lease):
        if not self.valid():self.close();return
        try:
            # The leased datagram sockets stay owned by the lease; the engine is
            # stopped before the lease closes them.
            self.engine.start(offer=self.offer,endpoint=('native',self.lease.uplink,self.lease.downlink));self.ready=True
            pending=self.pending_signals;self.pending_signals=[]
            for message in pending:
                if not self.valid():raise PermissionError('audio consent changed')
                self.engine.receive(message)
        except Exception:self._lost()

    def _pcm(self,_frame):
        # Device endpoints never hand audio frames to Python.
        if not self.valid():self._lost()

    def _lost(self):
        # Content-free stage evidence: never include credentials, SDP, PCM or exception text.
        if not self.closed:
            import logging, sys
            caller=sys._getframe(1).f_code.co_name
            error=sys.exc_info()[0]
            logging.getLogger(__name__).warning('Call audio stopped: component=%s stage=%s exception=%s',
                __name__,caller,error.__name__ if error else 'none')
        if self.closed:return False
        self.close();self.failed();return False

    def changed(self):
        """Called by the owning account/call/permission lifecycle subscription."""
        if not self.valid():self._lost()

    def close(self):
        if self.closed:return
        self.closed=True;self.ready=False;self.connected=False;self.pending_signals.clear()
        if self.signals:self.signals.close()
        elif self.stream is not None:self.stream.close()
        if self.engine:self.engine.close()
        if self.lease:self.lease.close()
