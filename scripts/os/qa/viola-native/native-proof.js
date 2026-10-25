// Disposable owned-Shell diagnostic. No engine CDP calls or input-adapter calls.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

const sleep=ms=>new Promise(resolve=>GLib.timeout_add(GLib.PRIORITY_DEFAULT,ms,()=>{resolve();return GLib.SOURCE_REMOVE;}));
const read=path=>JSON.parse(new TextDecoder().decode(GLib.file_get_contents(path)[1]));
async function capture(launcher,argv) {
    const process=launcher.spawnv(argv);
    const [stdout,stderr]=await process.communicate_utf8_async(null,null);
    return {ok:process.get_successful(),stdout,stderr};
}
function geometry(window,native) {
    const [width,height]=native.window_size;
    const candidates=[window.get_frame_rect(),window.get_buffer_rect()]
        .filter(rect=>Math.abs(rect.width-width)<=1 && Math.abs(rect.height-height)<=1);
    const origins=new Set(candidates.map(rect=>`${rect.x},${rect.y}`));
    if (!candidates.length || origins.size!==1) throw Error('GTK and compositor window geometry do not have one proven origin');
    const rect=candidates[0], [x,y,w,h]=native.page_bounds;
    return {window:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},page:[rect.x+x,rect.y+y,w,h]};
}

export async function proveNativeWindow(window,launcher,out,url) {
    const token=/[?&]token=([a-f0-9]{32})(?:&|$)/.exec(url)?.[1];
    if (!token) throw Error('unique native page token is absent');
    const observer=GLib.getenv('BROWSER_GATE_NATIVE_OBSERVER');
    const validator=GLib.getenv('BROWSER_GATE_PIXEL_VALIDATOR');
    const input=GLib.getenv('BROWSER_GATE_INPUT_HELPER');
    if (![observer,validator,input].every(value=>typeof value==='string' && value.length)) throw Error('native fixture helper paths are missing');
    const pid=window.get_pid(), records=[], failures=[];
    let firstStart=null, previousFrame=null;
    const save=()=>GLib.file_set_contents(`${out}/native-mechanism.json`,JSON.stringify({scope:'UNSIGNED_FORCED_SHM_MECHANISM_ONLY_NOT_RELEASE_QUALIFICATION',pid,url,records,failures},null,2));
    async function observe() {
        const reply=await capture(launcher,['/usr/bin/python3','-B',observer,`${pid}`,url]);
        if (!reply.ok) throw Error(`native identity observation failed: ${reply.stderr}`);
        const state=JSON.parse(reply.stdout);
        if (firstStart!==null && firstStart!==state.pid_start) throw Error('native browser PID identity changed');
        firstStart??=state.pid_start;
        if (state.pending_leases<0 || state.pending_leases>3) throw Error('native frame lease bound failed');
        return state;
    }
    async function pixels(label,phase,requireNewFrame) {
        const deadline=Date.now()+120000;
        let lastError='no observation';
        for (let attempt=0;attempt<40 && Date.now()<deadline;attempt++) {
            try {
                const state=await observe();
                const frame=state.native.displayed_frame.id;
                if (requireNewFrame && frame===previousFrame) throw Error('page state has no new displayed SHM frame');
                const bound=geometry(window,state.native);
                const path=`${out}/native-${label}-${attempt}.png`;
                const shot=await capture(launcher,['/usr/bin/python3','-B',input,'capture','--output',path]);
                if (!shot.ok) throw Error(`owned compositor PNG capture failed: ${shot.stderr}`);
                const screenshot=JSON.parse(shot.stdout);
                const reply=await capture(launcher,['/usr/bin/python3','-B',validator,path,'--phase',`${phase}`,'--rectangle',...bound.page.map(String)]);
                if (!reply.ok) throw Error(`native screenshot color proof failed: ${reply.stdout} ${reply.stderr}`);
                const proof=JSON.parse(reply.stdout);
                if (!proof.ok) throw Error('native screenshot validator did not confirm pixels');
                records.push({label,phase,frame,observation:state,geometry:bound,screenshot,pixels:proof});
                previousFrame=frame;save();return state;
            } catch(error) {lastError=String(error);}
            await sleep(1000);
        }
        failures.push({label,phase,error:lastError});save();throw Error(`${label}: ${lastError}`);
    }
    async function pageState(phase) {
        const deadline=Date.now()+30000;
        const path=`${out}/page-phase-${phase}.json`;
        while (!GLib.file_test(path,GLib.FileTest.EXISTS)) {
            if (Date.now()>deadline) throw Error(`no trusted requested-page state ${phase}`);
            await sleep(200);
        }
        const state=read(path);
        if (state.phase!==phase || state.href!==url || state.token!==token) throw Error('unique requested page state does not match');
        return state;
    }
    async function pageViewport(phase,native) {
        const deadline=Date.now()+30000;
        const samePage=state=>state.phase===phase && state.href===url && state.token===token;
        const [,,width,height]=native.page_bounds;
        while (Date.now()<deadline) {
            const snapshots=[await pageState(phase)];
            const latest=`${out}/page-viewport-latest.json`;
            if (GLib.file_test(latest,GLib.FileTest.EXISTS)) snapshots.push(read(latest));
            const match=snapshots.find(state=>samePage(state) && Math.abs(state.width-width)<=1 && Math.abs(state.height-height)<=1);
            if (match) {records.push({action:'bound-page-viewport',phase,state:match});save();return match;}
            await sleep(200);
        }
        throw Error('fresh same-phase DOM and GTK viewport do not match');
    }
    try {
        window.activate(global.get_current_time());
        await pageState(0);
        const start=await pixels('loaded',0,false);
        const rectangle=geometry(window,start.native).page;
        await pageViewport(0,start.native);
        const click=await capture(launcher,['/usr/bin/python3','-B',input,'click','--x',`${Math.round(rectangle[0]+rectangle[2]*.25)}`,'--y',`${Math.round(rectangle[1]+rectangle[3]*.25)}`]);
        if (!click.ok) throw Error(`owned compositor click failed: ${click.stderr}`);
        records.push({action:'real-compositor-click',receipt:JSON.parse(click.stdout),page:await pageState(1)});save();
        await pageViewport(1,(await pixels('trusted-pointer',1,true)).native);
        const key=await capture(launcher,['/usr/bin/python3','-B',input,'key-a']);
        if (!key.ok) throw Error(`owned compositor key failed: ${key.stderr}`);
        records.push({action:'real-compositor-key',receipt:JSON.parse(key.stdout),page:await pageState(2)});save();
        await pageViewport(2,(await pixels('trusted-key',2,true)).native);
        const before=window.get_frame_rect();
        const width=Math.max(600,before.width-100),height=Math.max(500,before.height-60);
        window.move_resize_frame(false,before.x,before.y,width,height);
        await sleep(1000);
        const resized=await pixels('resized',2,true);
        await pageViewport(2,resized.native);
        if (Math.abs(resized.native.window_size[0]-width)>2 || Math.abs(resized.native.window_size[1]-height)>2) throw Error('native window did not accept requested size');
        window.minimize();
        const hiddenDeadline=Date.now()+15000;
        while (window.showing_on_its_workspace()) {if(Date.now()>hiddenDeadline)throw Error('native window did not hide');await sleep(100);}
        window.unminimize();window.activate(global.get_current_time());
        await pixels('visible-again',2,false);
        records.push({result:'NATIVE_SHM_PAGE_TRUSTED_INPUT_RESIZE_VISIBILITY_PROVEN_CLOSE_STILL_REQUIRED'});save();
        return records;
    } catch(error) {failures.push({error:String(error),stack:error.stack});save();throw error;}
}
