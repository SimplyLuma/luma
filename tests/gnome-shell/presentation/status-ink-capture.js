// SPDX-License-Identifier: Apache-2.0
// Every status-ink decision comes from one bounded native stage capture.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../../../src/luma-shell-presentation/presentation.js',import.meta.url),'utf8');
const start=source.indexOf('    _sampleInk() {'), end=source.indexOf('\n    }\n',start)+7;
assert.ok(start>0&&end>start,'presentation.js keeps _sampleInk()');
let captures=0, points;
const pixels=[[1,10,20,255],[2,10,20,255],[3,10,20,255],[4,10,20,255],[5,10,20,255],[6,10,20,255]];
const GLib={Variant:class {constructor(type,value){assert.equal(type,'a(ii)');this.value=value;}}};
const Shell={Screenshot:class {pick_colors(value){captures++;points=value.value;return {deep_unpack:()=>pixels};}}};
const Main={layoutManager:{primaryMonitor:{x:10,y:20,width:1000,height:2000}}};
const inkFor=colors=>colors.map(color=>color.red).join(',');
const context=vm.createContext({console,Error,GLib,Shell,Main,inkFor});
vm.runInContext(`this.Presentation=class{${source.slice(start,end)}}`,context);
const home=new context.Presentation();
Object.assign(home,{_home:{visible:false},_statusWindow:{},mode:'mobile',_scale:2,_destroyed:false,updates:0,
    _updateStatusStyle(){this.updates++;}});
home._sampleInk();
assert.equal(captures,1,'one status sample makes one native capture');
assert.equal(JSON.stringify(points),JSON.stringify([[70,22],[310,22],[710,22],[950,22],[70,2017],[950,2017]]),
    'the batch contains the four status and two gesture-edge points');
assert.deepEqual({...home._sampledInk},{all:'1,2,3,4',left:'1,2',right:'3,4',bottom:'5,6'});
assert.equal(home.updates,1);
assert.equal(home._inkSampling,false);
console.log('status ink single capture: ok');
