// Actual input into this disposable GNOME compositor, never app adapters.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

const sleep=ms=>new Promise(resolve=>GLib.timeout_add(GLib.PRIORITY_DEFAULT,ms,()=>{resolve();return GLib.SOURCE_REMOVE;}));
export class OwnedCompositorInput {
    constructor(out) {
        if (GLib.get_user_name()!=='luma-gate-browser-opens' ||
            GLib.get_home_dir()!=='/home/luma-gate-browser-opens' ||
            GLib.getenv('WAYLAND_DISPLAY')!=='viola-qa-shm-r1' ||
            !/^\/tmp\/luma-gate-browser-opens\.[A-Za-z0-9]+\/out$/.test(out) ||
            GLib.getenv('BROWSER_GATE_OUT')!==out)
            throw Error('Owned virtual input is restricted to the disposable Shell fixture');
        this.out=out;this.sequence=0;
        const seat=global.stage.context.get_backend().get_default_seat();
        this.pointer=seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this.keyboard=seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    }
    save(receipt) {
        GLib.file_set_contents(`${this.out}/compositor-input-${++this.sequence}.json`,JSON.stringify(receipt,null,2));
    }
    async click(x,y) {
        if (![x,y].every(Number.isFinite) || x<0 || y<0 || x>=1600 || y>=1000)
            throw Error('Owned compositor click is outside the virtual monitor');
        const receipt={scope:'REAL_COMPOSITOR_VIRTUAL_INPUT_NOT_PAGE_ACCEPTANCE',action:'click',
            compositor:GLib.get_prgname(),requested:[x,y],before:global.get_pointer(),
            focus_before:global.display.focus_window?.get_pid()??null};
        if (!this.motionProbed) {
            this.motionProbed=true;
            const diagnostic={scope:'BOUNDED_REAL_COMPOSITOR_MOTION_DIAGNOSTIC_NO_BUTTONS',
                before:global.get_pointer(),samples:[]};
            for (const [probeX,probeY] of [[200,200],[800,500],[1200,800]]) {
                this.pointer.notify_absolute_motion(GLib.get_monotonic_time(),probeX,probeY);
                await sleep(250);
                const sample={requested:[probeX,probeY],actual:global.get_pointer()};
                try {
                    const [point,mods]=global.backend.get_cursor_tracker().get_pointer();
                    sample.cursor_tracker={x:point.x,y:point.y,mods};
                } catch(error) {sample.cursor_tracker_error=String(error);}
                diagnostic.samples.push(sample);
            }
            GLib.file_set_contents(`${this.out}/compositor-motion-probe.json`,JSON.stringify(diagnostic,null,2));
        }
        this.pointer.notify_absolute_motion(GLib.get_monotonic_time(),x,y);
        const deadline=Date.now()+2000;
        while (Date.now()<deadline) {
            const [actualX,actualY]=global.get_pointer();
            if (Math.abs(actualX-x)<=1 && Math.abs(actualY-y)<=1) break;
            await sleep(20);
        }
        receipt.after_motion=global.get_pointer();
        const [actualX,actualY]=receipt.after_motion;
        receipt.position_verified=Math.abs(actualX-x)<=1 && Math.abs(actualY-y)<=1;
        this.save(receipt);
        if (!receipt.position_verified) throw Error('Actual compositor pointer did not reach requested coordinates');
        await sleep(100);
        this.pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.PRESSED);
        try {await sleep(80);} finally {
            this.pointer.notify_button(GLib.get_monotonic_time(),Clutter.BUTTON_PRIMARY,Clutter.ButtonState.RELEASED);
        }
        await sleep(150);
        receipt.after_click=global.get_pointer();
        receipt.focus_after=global.display.focus_window?.get_pid()??null;
        receipt.button_sent=true;this.save(receipt);return receipt;
    }
    async keyA() {
        const receipt={scope:'REAL_COMPOSITOR_VIRTUAL_INPUT_NOT_PAGE_ACCEPTANCE',action:'key-a',
            compositor:GLib.get_prgname(),pointer:global.get_pointer(),focus_before:global.display.focus_window?.get_pid()??null};
        this.keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_a,Clutter.KeyState.PRESSED);
        try {await sleep(80);} finally {
            this.keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_a,Clutter.KeyState.RELEASED);
        }
        await sleep(150);receipt.key_sent=true;this.save(receipt);return receipt;
    }
    async navigateFixture(window,url) {
        if (url!==GLib.getenv('BROWSER_GATE_URL') ||
            !/^http:\/\/127\.0\.0\.1:[0-9]+\/native-shm-([a-f0-9]{32})\?token=\1$/.test(url))
            throw Error('Virtual address typing requires this owned fixture URL');
        const focused=()=>global.display.focus_window===window && window.has_focus() &&
            window.get_gtk_application_id()==='com.rhyme.viola';
        if (!focused()) throw Error('Primary signed browser must own compositor focus before address typing');
        const receipt={scope:'REAL_COMPOSITOR_ADDRESS_TYPING_NOT_PAGE_ACCEPTANCE',
            action:'navigate-fixture',url,pid:window.get_pid(),focus_before:global.display.focus_window.get_pid(),
            keys:[],characters_sent:0,return_sent:false};
        const key=(value,state)=>{
            const time=GLib.get_monotonic_time();
            this.keyboard.notify_keyval(time,value,state);
            receipt.keys.push({keyval:value,state,time_us:time});
        };
        const stroke=async value=>{
            if (!focused()) throw Error('Primary browser lost compositor focus while typing');
            key(value,Clutter.KeyState.PRESSED);
            try {await sleep(25);} finally {key(value,Clutter.KeyState.RELEASED);}
            await sleep(25);
        };
        const control=async value=>{
            if (!focused()) throw Error('Primary browser lost compositor focus before shortcut');
            key(Clutter.KEY_Control_L,Clutter.KeyState.PRESSED);
            try {await sleep(80);await stroke(value);} finally {
                key(Clutter.KEY_Control_L,Clutter.KeyState.RELEASED);
            }
            await sleep(150);
        };
        try {
            await control(Clutter.KEY_l);
            await control(Clutter.KEY_a);
            for (const character of url) {
                // This exact fixture URL is printable ASCII; Mutter resolves
                // each keysym's keycode and synthesizes its Shift level.
                await stroke(character.charCodeAt(0));receipt.characters_sent++;
            }
            await sleep(300);
            await stroke(Clutter.KEY_Return);receipt.return_sent=true;
            await sleep(150);
            receipt.focus_after=global.display.focus_window?.get_pid()??null;
            return receipt;
        } finally {this.save(receipt);}
    }
}
