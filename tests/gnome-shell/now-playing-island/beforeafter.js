// Before/after: the now-playing island left mid-animation (translucent, held at
// the width of its artwork and title) the way Nick's shelf showed it, then
// cropped three seconds later.
import GLib from 'gi://GLib';
import St from 'gi://St';
const log = (m, o) => console.log(`[ba] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
export default async function ({Main, shot}) {
    const shelf = Main.shelf, tag = GLib.getenv('B_TAG');
    for (let i = 0; i < 120 && !shelf?._media?.eligible; i++) await sleep(500);
    if (!St.Settings.get().enable_animations) St.Settings.get().uninhibit_animations();
    await sleep(3000);
    const island = shelf._mediaIsland, m = shelf._media;
    const crop = async name => {
        const g = shelf._group.get_transformed_extents();
        await shot(`${name}-${tag}`, g.get_x() - 12, g.get_y() - 14, g.get_width() + 24, g.get_height() + 28);
    };
    await crop('normal');
    const identity = Math.round(m._identity.get_preferred_width(-1)[1] + 16);
    island.opacity = 150;
    island.width = identity;
    await sleep(3000);
    const [, natural] = island.get_preferred_width(-1);
    log('three seconds later', {opacity: island.opacity, width: Math.round(island.width), natural: Math.round(natural),
        controls: Math.round(m._controls.get_allocation_box().get_width()), guard: Boolean(shelf._mediaGuard)});
    await crop('stuck-plus-3s');
}
