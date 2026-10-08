// Execute the real title-facts method with fixture metadata; runtime GI is separate.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2] + '/src/ui/mainWindow.js', 'utf8');
const body = source.split('    _updateFileFacts() {')[1].split('    _updateTitlebar() {')[0].replace(/},?\s*$/, '');
const update = vm.runInNewContext('(function(){' + body + '})', {
 Gio: {content_type_get_description: x => x}, GLib: {format_size: n => n + ' bytes'},
});
function facts(player, wrapped=false) {
 let result;
 const w = {_fileInfo: {get_content_type: () => 'video/mp4', get_size: () => 99},
  _renderer: wrapped ? {_player: player} : player,
  _fileFacts: {set_text: x => result=x}};
 update.call(w); return result;
}
const player = (duration,width=0,height=0) => ({get_media_duration:()=>duration,
 get_video_width:()=>width,get_video_height:()=>height});
assert.equal(facts(player(44.9,1920,1080)), 'video/mp4 · 1920 × 1080 · 0:44 · 99 bytes');
assert.equal(facts(player(3603),true), 'video/mp4 · 1:00:03 · 99 bytes');
for (const d of [-1,0,NaN,Infinity]) assert.equal(facts(player(d)), 'video/mp4 · 99 bytes');
assert.equal(facts(player(3,1920,0)), 'video/mp4 · 0:03 · 99 bytes');
assert.equal(facts({}), 'video/mp4 · 99 bytes');
console.log('Media facts formatting and unknown-data tests passed');
