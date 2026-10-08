import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
        const process=Gio.Subprocess.new(['/usr/bin/python3',GLib.getenv('LUMA_CONNECT_UI_FIXTURE')],Gio.SubprocessFlags.NONE);
        process.wait_check_async(null, (p,result) => {
            try { p.wait_check_finish(result);console.log('PASS native Connect runner'); }
            catch(error) {console.error(error.message);}
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() {await new Promise(() => {});}
