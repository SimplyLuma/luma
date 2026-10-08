// SPDX-License-Identifier: GPL-2.0-or-later
import GLib from 'gi://GLib';
const {MediaSelection, isEligible, pickPlayer, shelfGaps, anchoredToStatus} = await import(
    GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));
const assert = {
    equal(a, b) { if (a !== b) throw new Error(`Expected ${b}, got ${a}`); },
    deepEqual(a, b) { this.equal(JSON.stringify(a), JSON.stringify(b)); },
};
const a = {canPlay:true,status:'Paused'}, b = {canPlay:true,status:'Paused'};
const s = new MediaSelection();
assert.equal(s.update(a), a);
s.update(b); assert.equal(s.selected,a);
b.status='Playing'; assert.equal(s.update(b),b);
a.title='background change'; assert.equal(s.update(a),b);
b.status='Paused'; assert.equal(s.update(b),b);
a.status='Playing'; assert.equal(s.update(a),a);
a.canPlay=false; assert.equal(s.update(a),b);
assert.equal(s.remove(b),null);
assert.equal(isEligible({canPlay:true,status:'Stopped'}),true);
assert.equal(isEligible({canPlay:false,status:'Playing'}),false);
assert.equal(pickPlayer([{player:a,played:99,appeared:1},{player:b,played:1,appeared:2}]),b);
assert.deepEqual(shelfGaps(['dock','media','clock'],'media','clock',10,100),[110,10]);
assert.deepEqual(shelfGaps(['media','clock'],'media','clock',10,100),[10]);
// Whatever sits immediately before the status cluster hugs it; the slack goes
// to the gaps further back. A live extension island there used to be handed
// half the slack on each side and floated loose between the dock and the clock.
assert.deepEqual(shelfGaps(['dock','live','clock'],'media','clock',10,100),[110,10]);
// Media and a live island together pack against the clock as one group; the
// live island used to float in the middle, halfway between dock and player.
assert.deepEqual(shelfGaps(['dock','live','media','clock'],'media','clock',10,90,'live'),[100,10,10]);
assert.deepEqual(shelfGaps(['dock','live','clock'],'media','clock',10,100,'live'),[110,10]);
assert.deepEqual(shelfGaps(['live','media','clock'],'media','clock',10,90,'live'),[10,10]);
assert.deepEqual(anchoredToStatus(['live','media','clock'],'media','clock','live'),true);
assert.deepEqual(anchoredToStatus(['dock','media','clock'],'media','clock','live'),false);
// With nothing but the dock and the clock, the single gap is what carries the
// clock to the right edge, so it keeps the slack rather than being pinned tight.
assert.deepEqual(shelfGaps(['dock','clock'],'media','clock',10,100),[110]);
// The notifications island ends the row, one padding after the status
// cluster; the slack still goes towards the dock (0160).
assert.deepEqual(shelfGaps(['dock','clock','bell'],'media','clock',10,100,null,'bell'),[110,10]);
assert.deepEqual(shelfGaps(['dock','media','clock','bell'],'media','clock',10,100,null,'bell'),[110,10,10]);
assert.deepEqual(anchoredToStatus(['media','clock','bell'],'media','clock',null,'bell'),true);
assert.deepEqual(anchoredToStatus(['dock','clock','bell'],'media','clock',null,'bell'),false);
print('Packaged media selection and pinned gap checks: PASS');
