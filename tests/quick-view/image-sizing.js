const fs = require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2]+'/src/ui/mainWindow.js','utf8');
const body=source.split('    _resizeWindow() {')[1].split('    _createRenderer() {')[0].replace(/},?\s*$/,'');
const policy={MAX_SIZE:0,NAT_SIZE:1,SCALED:2,STRETCHED:3};
const resize=vm.runInNewContext('(function(){'+body+'})', {Renderer:{ResizePolicy:policy}, Utils:{getScaledSize:(s,max,up)=>{const k=Math.min(max[0]/s[0],max[1]/s[1],up?Infinity:1);return s.map(n=>Math.floor(n*k));}}});
function pane(w,h,max=[1080,800]) {
 let result,outer;
 resize.call({_renderer:{get_preferred_width:()=>[1,w],get_preferred_height:()=>[1,h],resizePolicy:2},
 _getMaxSize:()=>max,_lastWindowSize:[0,0],_fileInfo:{get_content_type:()=> 'image/png'},
 _embed:{set_size_request:(...x)=>result=x},resize:(...x)=>outer=x,get_mapped:()=>false,
 _applyContentSize(size){this._embed.set_size_request(...size);this.resize(size[0]+12,size[1]+50);}});return [result,outer];
}
assert.deepEqual(pane(1000,500),[[1000,500],[1012,550]]);
assert.deepEqual(pane(2000,1000),[[1080,540],[1092,590]]);
assert.deepEqual(pane(100,100),[[400,100],[412,150]]); // letterbox; renderer doesn't upscale
assert.deepEqual(pane(100,100,[300,400]),[[300,100],[312,150]]);
assert(!source.split('var MainWindow =')[1].split('    _onRealize()')[0].includes('        this.show_all();')); // first mapping must follow external parenting
console.log('Image pane aspect, bounds, and first-map regression checks passed');
// A preview is built before it is shown on Filer's monitor; a picture must be
// rebuilt when the window's scale changes (1x monitor beside a 1.25x panel) and
// placed by the scale its surface was built for.
const image = fs.readFileSync(process.argv[2] + '/src/viewers/image.js', 'utf8');
assert(/connect\('notify::scale-factor'[\s\S]{0,120}this\._scaledSurface = null;/.test(image), 'picture rebuilds on scale change');
assert(/let scaleFactor = this\._surfaceScale;/.test(image.split('vfunc_draw(context)')[1].split('_createImageTexture')[0]), 'picture drawn at its own scale');
assert(/this\._surfaceScale === scaleFactor/.test(image), 'a scale change alone rebuilds the picture');
console.log('Picture scale-change checks passed');
