#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual native output-module protocol, PCM and cancellation."""
import os, pathlib, queue, struct, subprocess, sys, tempfile, threading, time
binary,model,config,library = map(pathlib.Path, sys.argv[1:])
with tempfile.TemporaryDirectory() as folder:
    cfg=pathlib.Path(folder)/'piper.conf'
    cfg.write_text(f'ModelPath "{model.resolve()}"\nConfigPath "{config.resolve()}"\nESpeakNGDataDirPath "/usr/share/espeak-ng-data"\n')
    env=dict(os.environ, LD_LIBRARY_PATH=str(library.resolve())+':'+os.environ.get('LD_LIBRARY_PATH',''))
    process=subprocess.Popen([str(binary.resolve()),str(cfg)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
    lines=queue.Queue()
    def read():
        for line in iter(process.stdout.readline,b''): lines.put(line)
        lines.put(None)
    threading.Thread(target=read,daemon=True).start()
    def send(data): process.stdin.write(data); process.stdin.flush()
    def until(marker, timeout=35):
        deadline=time.monotonic()+timeout; found=[]
        while time.monotonic()<deadline:
            line=lines.get(timeout=max(.001,deadline-time.monotonic()))
            assert line is not None, f'module exited ({process.poll()})'
            found.append(line)
            if line.startswith(marker): return found
        raise AssertionError(f'module never reached {marker!r}')
    def samples(output):
        pcm=bytearray()
        for line in output:
            if not line.startswith(b'705-AUDIO\0'): continue
            encoded=line[10:-1];index=0
            while index < len(encoded):
                value=encoded[index];index+=1
                if value==0x7d:
                    assert index < len(encoded), 'truncated escaped PCM'
                    value=encoded[index]^0x20;index+=1
                pcm.append(value)
        counts=sum(int(line.split(b'=',1)[1]) for line in output if line.startswith(b'705-num_samples='))
        assert len(pcm)==counts*2 and counts>1000, 'incorrect native PCM framing/count'
        values=struct.unpack('<'+'h'*counts,pcm)
        assert max(abs(value) for value in values)>100, 'silent native PCM'
        assert b'705-sample_rate=22050\n' in output, 'wrong model/audio rate'
        return len(pcm)
    try:
        send(b'INIT\n'); until(b'299 OK LOADED SUCCESSFULLY')
        send(b'LIST VOICES\n'); voices=until(b'200 OK VOICE LIST SENT')
        assert any(b'en_US-ljspeech-medium\ten-US\t' in line for line in voices), voices
        send(b'LIST VOICES en\n'); english=until(b'200 OK VOICE LIST SENT')
        assert any(b'en_US-ljspeech-medium' in line for line in english), english
        # Speech Dispatcher 0.12.1 returns 304 for an empty filtered list.
        send(b'LIST VOICES fr\n'); french=until(b'304 CANT LIST VOICES')
        assert not any(b'en_US-ljspeech-medium' in line for line in french), french
        send(b'AUDIO\n');until(b'207 ')
        send(b'audio_output_method=server\n.\n');until(b'203 OK AUDIO INITIALIZED')
        for phrase in (b'Welcome to Luma. This is a natural voice.', b"It is safe to read quotes, apostrophes, dollar signs, and ordinary punctuation."):
            send(b'SPEAK\n');until(b'202 ')
            send(phrase+b'\n.\n');output=until(b'702 END')
            assert b'701 BEGIN\n' in output
            print('NATIVE PIPER SSIP PCM PASS',samples(output))
        send(b'SPEAK\n');until(b'202 ')
        send(b'A longer sentence gives the listener enough time to stop before the next paragraph starts. '+b'Keep reading this natural English voice. '*8+b'\n.\n')
        until(b'705-AUDIO\0');started=time.monotonic();send(b'STOP\n');output=until(b'703 STOP',10)
        assert not any(line.startswith(b'702 END') for line in output), 'cancel reported as completion'
        print('NATIVE PIPER SSIP CANCEL PASS',round(time.monotonic()-started,3))
        send(b'QUIT\n');process.wait(timeout=5);assert process.returncode==0
    finally:
        if process.poll() is None: process.kill();process.wait()
