"""Maintained GStreamer WebRTC audio engine; no microphone or signaling server.

The owning paired session authenticates every SDP/ICE message and authorizes
this single audio stream. DTLS-SRTP, ICE, Opus and jitter handling remain in
GStreamer/libnice. Only bounded 16kHz mono PCM enters/leaves this adapter.
"""
import threading


class WebRTCAudio:
    def __init__(self,*,authorized,signal,pcm,failed,dispatch,connected=lambda:None):
        import gi
        gi.require_version('Gst','1.0');gi.require_version('GstSdp','1.0');gi.require_version('GstWebRTC','1.0')
        from gi.repository import Gst,GstSdp,GstWebRTC
        Gst.init(None)
        self.Gst,self.Sdp,self.WebRTC=Gst,GstSdp,GstWebRTC
        self.authorized,self.signal,self.pcm,self.failed,self.dispatch=authorized,signal,pcm,failed,dispatch
        self.connected=connected
        self.pipeline=None;self.rtc=None;self.source=None;self.closed=False;self.started=False
        self.remote_set=False;self.remote_pending=False;self.local_started=False;self.offerer=False;self.candidates=[];self.candidate_count=0;self.sent_candidates=0
        self.lock=threading.RLock();self.samples=0;self.next_pts=None;self.remainder=bytearray();self.sink_linked=False
        self.endpoint=None;self.watchdog=None;self.stats_timer=None;self.stats={};self.startup_timer=None;self.capturing=False
        self.state_worker=None;self.state_thread=None

    # A phone on Wi-Fi routinely delivers 100-250 ms bursts. The jitter buffer
    # absorbs them and reports losses so Opus conceals instead of going silent.
    LATENCY_MS=150
    RAW='audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved'

    def start(self,*,offer,endpoint=None):
        """endpoint=None exchanges PCM through push()/pcm callbacks.

        'desktop' opens the default PipeWire capture/playback streams inside
        this pipeline; ('native',uplink,downlink) reads and writes the leased
        640-byte datagram sockets. Both keep every audio frame in GStreamer's
        clocked streaming threads: no Python per-frame pacing or pipe budgets.
        """
        if self.started or self.closed:raise RuntimeError('one WebRTC session required')
        if not self.authorized():raise PermissionError('audio not approved')
        native=isinstance(endpoint,tuple) and len(endpoint)==3 and endpoint[0]=='native'
        if endpoint not in (None,'desktop') and not native:raise ValueError('explicit audio endpoint required')
        required=['webrtcbin','nicesrc','nicesink','dtlssrtpenc','dtlssrtpdec','opusenc','opusdec','rtpopuspay','rtpopusdepay','volume']
        required+={None:['appsrc','appsink'],'desktop':['pulsesrc','pulsesink']}.get(endpoint,['fdsrc','fdsink','rawaudioparse','audiobuffersplit'])
        if any(not self.Gst.ElementFactory.find(name) for name in required):raise RuntimeError('native WebRTC dependencies unavailable')
        self.started=True;self.offerer=bool(offer);self.endpoint='native' if native else endpoint
        if endpoint is None:
            source=('appsrc name=pcm_in is-live=true format=time do-timestamp=false block=false leaky-type=downstream max-bytes=6400 max-buffers=10 '
                    f'caps="{self.RAW}" ')
            sink='appsink name=pcm_out emit-signals=true sync=true max-buffers=10 drop=true'
        elif endpoint=='desktop':
            # The session's PulseAudio-compatible PipeWire server. GStreamer's
            # pipewiresrc starves while a clocked pipewiresink in the same
            # pipeline prerolls (measured: 0 capture buffers/s on the ThinkPad),
            # and pipewiresink treats a policy relink as fatal.
            source=f'pulsesrc name=mic buffer-time=40000 latency-time=10000 ! audioconvert ! audioresample ! {self.RAW} '
            sink='pulsesink name=speaker sync=true buffer-time=80000 latency-time=20000'
        else:
            source=f'fdsrc name=native_in blocksize=640 do-timestamp=true ! rawaudioparse use-sink-caps=false format=pcm pcm-format=s16le sample-rate=16000 num-channels=1 ! {self.RAW} '
            sink='audiobuffersplit output-buffer-duration=1/50 strict-buffer-size=true ! fdsink name=native_out sync=true'
        self.pipeline=self.Gst.parse_launch(
            f'webrtcbin name=rtc bundle-policy=max-bundle latency={self.LATENCY_MS} '
            f'{source}! volume name=mute ! queue leaky=downstream max-size-buffers=0 max-size-bytes=0 max-size-time=200000000 '
            '! audioconvert ! audioresample ! opusenc bitrate=24000 frame-size=20 audio-type=voice inband-fec=true packet-loss-percentage=10 '
            '! rtpopuspay pt=96 ! application/x-rtp,media=audio,encoding-name=OPUS,payload=96 ! rtc. '
            'queue name=receive leaky=downstream max-size-buffers=0 max-size-bytes=0 max-size-time=400000000 '
            '! rtpopusdepay ! opusdec plc=true use-inband-fec=true ! audioconvert ! audioresample '
            f'! {self.RAW} ! {sink}')
        self.rtc=self.pipeline.get_by_name('rtc');self.source=self.pipeline.get_by_name('pcm_in')
        rtpbin=self.rtc.get_by_name('rtpbin')
        if rtpbin is not None:rtpbin.connect('new-jitterbuffer',lambda _bin,buffer,*_:buffer.set_property('do-lost',True))
        if endpoint=='desktop':
            for name,node in (('mic','luma-call-capture'),('speaker','luma-call-playback')):
                self.pipeline.get_by_name(name).set_property('stream-properties',
                    self.Gst.Structure.new_from_string(f'props,media.role=phone,media.name={node},node.name={node}'))
        elif native:
            self.pipeline.get_by_name('native_in').set_property('fd',endpoint[2].fileno())
            self.pipeline.get_by_name('native_out').set_property('fd',endpoint[1].fileno())
        self.rtc.connect('on-ice-candidate',self._ice)
        self.rtc.connect('pad-added',self._pad)
        self.rtc.connect('notify::connection-state',lambda *_:self.dispatch(self._connection_state))
        if endpoint is None:self.pipeline.get_by_name('pcm_out').connect('new-sample',self._sample)
        self.bus=self.pipeline.get_bus();self.bus.add_signal_watch();self.bus_handler=self.bus.connect('message',self._message)
        if offer:self.rtc.connect('on-negotiation-needed',lambda *_:self.dispatch(self._create_offer))
        if endpoint=='desktop':
            # Audio server negotiation can wait tens of seconds when policy
            # cannot link a device. Never block the daemon's main context on it;
            # start and teardown are ordered on one dedicated state thread.
            import queue
            self.state_worker=queue.SimpleQueue();pipeline=self.pipeline;states=self.state_worker
            def run():
                while (state:=states.get()) is not None:
                    if pipeline.set_state(state)==self.Gst.StateChangeReturn.FAILURE and state==self.Gst.State.PLAYING:
                        self.dispatch(self._lost)
            self.state_thread=threading.Thread(target=run,name='connect-media-state',daemon=True);self.state_thread.start()
            states.put(self.Gst.State.PLAYING)
        elif self.pipeline.set_state(self.Gst.State.PLAYING)==self.Gst.StateChangeReturn.FAILURE:
            self._lost();raise RuntimeError('audio pipeline unavailable')
        if endpoint is None:
            # Prime fixed caps with one silent frame; no device is opened here.
            self.push(b'\0'*640)
        else:
            # Device streams run without a Python frame check, so a revoked
            # session is also enforced from the main context within 100 ms.
            from gi.repository import GLib
            self.watchdog=GLib.timeout_add(100,self._watch)
            self.stats_timer=GLib.timeout_add(5000,self._sample_stats)
            # A capture stream that never links (no device or policy) fails the
            # session instead of leaving a silent transfer. Observe one buffer.
            def first(_pad,_info):
                self.capturing=True;return self.Gst.PadProbeReturn.REMOVE
            self.pipeline.get_by_name('mute').get_static_pad('sink').add_probe(self.Gst.PadProbeType.BUFFER,first)
            # Desktop streams include PipeWire linking time.
            self._arm_startup(10000 if endpoint=='desktop' else 5000)

    def _arm_startup(self,milliseconds):
        if self.closed or self.startup_timer is not None:return False
        from gi.repository import GLib
        def startup():
            self.startup_timer=None
            if not self.closed and not self.capturing:self._lost()
            return False
        self.startup_timer=GLib.timeout_add(milliseconds,startup)
        return False

    def _watch(self):
        if self.closed:return False
        if not self.authorized():self._lost();return False
        return True

    def set_muted(self,muted):
        with self.lock:
            if self.closed or not self.started:raise PermissionError('audio unavailable')
            self.pipeline.get_by_name('mute').set_property('mute',bool(muted))

    def _sample_stats(self):
        if self.closed:return False
        def done(promise,*_):
            try:reply=promise.get_reply()
            except Exception:return
            values={}
            def each(_field,value,*_):
                try:
                    if value.get_name()=='inbound-rtp':
                        values.update(rx=value.get_value('packets-received'),lost=value.get_value('packets-lost'),jitter_ms=round(1000*value.get_value('jitter')))
                    elif value.get_name()=='outbound-rtp':values['tx']=value.get_value('packets-sent')
                except Exception:pass
                return True
            if reply is not None:reply.foreach(each)
            self.stats=values
        self.rtc.emit('get-stats',None,self.Gst.Promise.new_with_change_func(done,None,None))
        return True

    def _message(self,_bus,message):
        if message.type==self.Gst.MessageType.ERROR:
            import logging
            error,_debug=message.parse_error()
            logging.getLogger(__name__).warning('Call audio GStreamer error: domain=%s code=%s',error.domain,error.code)
            self._lost()
        elif message.type==self.Gst.MessageType.EOS:self._lost()

    def _connection_state(self):
        if self.closed:return False
        state=self.rtc.get_property('connection-state').value_nick
        if state in {'failed','disconnected','closed'}:self._lost()
        elif state=='connected' and self.authorized():self.connected()
        return False

    def _pad(self,_rtc,pad):
        if pad.direction!=self.Gst.PadDirection.SRC:return
        with self.lock:
            if self.closed:return
            if self.sink_linked:self.dispatch(self._lost);return
            sink=self.pipeline.get_by_name('receive').get_static_pad('sink')
            if pad.link(sink)!=self.Gst.PadLinkReturn.OK:self.dispatch(self._lost);return
            self.sink_linked=True

    def _sample(self,sink):
        sample=sink.emit('pull-sample')
        if sample is None:return self.Gst.FlowReturn.EOS
        buffer=sample.get_buffer()
        if buffer.get_size()>2560:self.dispatch(self._lost);return self.Gst.FlowReturn.ERROR
        data=buffer.extract_dup(0,buffer.get_size())
        with self.lock:
            if self.closed or not self.authorized():return self.Gst.FlowReturn.FLUSHING
            self.remainder.extend(data)
            if len(self.remainder)>3200:self.dispatch(self._lost);return self.Gst.FlowReturn.ERROR
            while len(self.remainder)>=640:
                frame=bytes(self.remainder[:640]);del self.remainder[:640]
                try:self.pcm(frame)
                except Exception:self.dispatch(self._lost);return self.Gst.FlowReturn.ERROR
        return self.Gst.FlowReturn.OK

    def push(self,frame):
        with self.lock:
            if self.closed or not self.started or not self.authorized():raise PermissionError('audio unavailable')
            if not isinstance(frame,bytes) or len(frame)!=640:raise ValueError('one PCM frame required')
            if self.endpoint is not None:raise RuntimeError('device endpoint owns capture')
            if self.samples and self.rtc.get_property('connection-state').value_nick!='connected':return
            # Live audio may arrive in a scheduling burst. Bounded leaky queues
            # discard old PCM instead of ending a still-authorized call.
            buffer=self.Gst.Buffer.new_allocate(None,640,None);buffer.fill(0,frame)
            # Keep adjacent PCM contiguous despite callback scheduling jitter.
            # Anchor after consent/ICE, and abandon stale audio after a pause.
            clock=self.pipeline.get_clock()
            now=max(0,clock.get_time()-self.pipeline.get_base_time()) if clock else 0
            if self.samples:
                if self.next_pts is None or self.next_pts<now-200000000:self.next_pts=now
                if self.next_pts>now+200000000:return
                buffer.pts=self.next_pts;self.next_pts+=20000000
            else:buffer.pts=now
            buffer.duration=20000000;self.samples+=1
            if self.source.emit('push-buffer',buffer)!=self.Gst.FlowReturn.OK:raise RuntimeError('audio stream ended')

    def _ice(self,_rtc,index,candidate):
        if self.closed:return
        if index!=0 or not isinstance(candidate,str) or len(candidate)>2048 or self.sent_candidates>=32:
            self.dispatch(self._lost);return
        self.sent_candidates+=1
        self.dispatch(lambda:self._send({'type':'ice','index':0,'candidate':candidate}))

    def _send(self,value):
        if not self.closed and self.authorized():
            try:self.signal(value)
            except Exception:self._lost()
        return False

    def _create_offer(self):
        if self.closed or self.local_started:return False
        self.local_started=True
        self._create_description('offer');return False

    def _create_description(self,kind):
        def created(promise,*_):
            try:
                reply=promise.get_reply();description=reply.get_value(kind)
                if description is not None:description=description.copy()
                if description is None:raise ValueError('missing SDP')
                self.dispatch(lambda:self._local_description(kind,description))
            except Exception:self.dispatch(self._lost)
        self.rtc.emit('create-'+kind,None,self.Gst.Promise.new_with_change_func(created,None,None))

    def _local_description(self,kind,description):
        if self.closed or not self.authorized():return False
        sdp=description.sdp.as_text()
        if len(sdp.encode())>16384:self._lost();return False
        def installed(promise,*_):
            try:promise.get_reply()
            except Exception:self.dispatch(self._lost);return
            self.dispatch(lambda:self._send({'type':kind,'sdp':sdp}))
        self.rtc.emit('set-local-description',description,self.Gst.Promise.new_with_change_func(installed,None,None))
        return False

    def receive(self,message):
        if self.closed or not self.authorized():raise PermissionError('audio not approved')
        if not isinstance(message,dict):raise ValueError('audio signal required')
        kind=message.get('type')
        if kind=='ice':
            if (set(message)!={'type','index','candidate'} or type(message['index']) is not int or message['index']!=0
                    or not isinstance(message['candidate'],str) or len(message['candidate'])>2048
                    or any(c in message['candidate'] for c in '\r\n') or self.candidate_count>=32):
                raise ValueError('invalid ICE candidate')
            self.candidate_count+=1
            if self.remote_set:self.rtc.emit('add-ice-candidate',0,message['candidate'])
            else:self.candidates.append(message['candidate'])
            return
        if (kind not in {'offer','answer'} or set(message)!={'type','sdp'} or self.remote_set or self.remote_pending
                or kind!=('answer' if self.offerer else 'offer')
                or not isinstance(message['sdp'],str) or len(message['sdp'].encode())>16384):
            raise ValueError('invalid audio description')
        result,sdp=self.Sdp.SDPMessage.new_from_text(message['sdp'])
        if result!=self.Sdp.SDPResult.OK or sdp.medias_len()!=1:raise ValueError('one audio stream required')
        media=sdp.get_media(0)
        if media.get_media()!='audio' or media.get_proto()!='UDP/TLS/RTP/SAVPF':raise ValueError('encrypted audio required')
        description=self.WebRTC.WebRTCSessionDescription.new(
            self.WebRTC.WebRTCSDPType.OFFER if kind=='offer' else self.WebRTC.WebRTCSDPType.ANSWER,sdp)
        def installed(promise,*_):
            try:promise.get_reply()
            except Exception:self.dispatch(self._lost);return
            def finish():
                if self.closed:return False
                self.remote_set=True
                for candidate in self.candidates:self.rtc.emit('add-ice-candidate',0,candidate)
                self.candidates.clear()
                if kind=='offer':self._create_description('answer')
                return False
            self.dispatch(finish)
        self.remote_pending=True
        self.rtc.emit('set-remote-description',description,self.Gst.Promise.new_with_change_func(installed,None,None))

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

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True;self.remainder.clear();self.candidates.clear()
            pipeline=self.pipeline
            timers=[t for t in (self.watchdog,self.stats_timer,self.startup_timer) if t is not None];self.watchdog=self.stats_timer=self.startup_timer=None
        if timers:
            from gi.repository import GLib
            context=GLib.MainContext.default()
            for timer in timers:
                source=context.find_source_by_id(timer)
                if source:source.destroy()
        if self.stats:
            import logging
            # Content-free media health for the next diagnosis: counters only.
            logging.getLogger(__name__).warning('Call audio stats: endpoint=%s %s',self.endpoint,
                ' '.join(f'{k}={v}' for k,v in sorted(self.stats.items())))
        # Never hold the PCM lock while joining streaming threads.
        if pipeline:
            self.bus.disconnect(self.bus_handler);self.bus.remove_signal_watch()
            if self.state_worker is not None:
                self.state_worker.put(self.Gst.State.NULL);self.state_worker.put(None)
            else:
                # Native sockets are closed by the lease right after this returns.
                pipeline.set_state(self.Gst.State.NULL)
        self.pipeline=self.rtc=self.source=None
