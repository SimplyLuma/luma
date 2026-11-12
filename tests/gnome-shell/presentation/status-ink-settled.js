// SPDX-License-Identifier: Apache-2.0
// Status ink is sampled once the stage settles, not periodically while it
// keeps painting: each sample repaints the stage and reads it back on the
// compositor thread, which dropped frames during scrolls and animations.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../../src/luma-shell-presentation/presentation.js',import.meta.url),'utf8');
const start=source.indexOf('    _queueInkSample() {'), end=source.indexOf('\n    }\n',start)+7;
assert.ok(start>0&&end>start,'presentation.js keeps _queueInkSample()');
let now=0, next=1;
const timers=new Map();
const GLib={PRIORITY_LOW:300,SOURCE_REMOVE:false,
 timeout_add(_priority,ms,callback){const id=next++;timers.set(id,{at:now+ms,callback});return id;},
 source_remove(id){assert.ok(timers.delete(id),'only live sources are removed');}};
const advance=ms=>{now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.callback();}};
const Main={sessionMode:{isLocked:false,isGreeter:false}};
const context=vm.createContext({GLib,Main,INK_SAMPLE_MS:400});
vm.runInContext(`this.Presentation=class{${source.slice(start,end)}}`,context);
const home=new context.Presentation();
Object.assign(home,{_inkSampleSource:0,_inkSampling:false,_home:{visible:false},_statusWindow:{},mode:'mobile',samples:0});
home._sampleInk=function(){this.samples++;};
for(let i=0;i<120;i++){home._queueInkSample();advance(8);}
assert.equal(home.samples,0,'no sample while the stage paints every frame');
advance(400);
assert.equal(home.samples,1,'one sample once painting stops');
assert.equal(home._inkSampleSource,0);
home._queueInkSample();home._inkSampling=true;home._queueInkSample();advance(400);
assert.equal(home.samples,2,'a paint during a sample does not queue another');
home._inkSampling=false;
for(const [key,value] of [['mode','desktop'],['_statusWindow',null]]){
 const old=home[key];home[key]=value;home._queueInkSample();advance(400);home[key]=old;}
home._home.visible=true;home._queueInkSample();advance(400);home._home.visible=false;
for(const prop of ['isLocked','isGreeter']){Main.sessionMode[prop]=true;home._queueInkSample();advance(400);Main.sessionMode[prop]=false;}
assert.equal(home.samples,2,'desktop, Home, no status bar, lock and greeter never sample');
console.log('status ink settled: ok');
