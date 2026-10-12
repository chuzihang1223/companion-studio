'use strict'
// Pure geometry regression. This does not claim renderer FPS or GUI acceptance.
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto')
const ts=require('../../desktop/node_modules/typescript')
require.extensions['.ts']=(m,f)=>m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,f)
const E=require('../../desktop/frontend/src/WorkspaceLayoutEngine.ts')
const S=require('../../desktop/frontend/src/WorkspaceSplitLayoutEngine.ts')
const {moduleIds}=require('../../desktop/frontend/src/WorkspaceModuleRegistry.ts')
const {cleanFree}=require('../../desktop/electron/free-layout-storage.cjs')
const rect=(l,id)=>{const p=l.modules[id];return [p.x,p.y,p.width,p.height]}
function setup(slots){const l=E.defaultLayout('blank');for(const p of Object.values(l.modules))p.closed=true;for(const [id,x,y,width,height]of slots)Object.assign(l.modules[id],{closed:false,x,y,width,height});return S.withWorkspaceSplits(l)}
function valid(l){const ids=E.visibleIds(l);for(const id of ids){const p=l.modules[id];assert.ok([p.x,p.y,p.width,p.height].every(Number.isFinite));assert.ok(p.x>=-.01&&p.y>=-.01&&p.width>0&&p.height>=39.99&&p.x+p.width<=l.canvasWidth+.01);for(const other of ids)if(id!==other)assert.equal(E.overlap(p,l.modules[other]),false,id+' overlaps '+other)}cleanFree(l)}

// A single pane can grow many screens vertically; workspace width is the only
// horizontal outer boundary, rather than an arbitrary preferred pane maximum.
const one=setup([['conversation',100,100,700,260]])
for(const [edge,dx,dy]of [['e',200,0],['w',-100,0],['n',0,-80],['s',0,4000]]){const l=E.resizeTracks(one,'conversation',edge,dx,dy);valid(l)}
assert.equal(E.resizeTracks(one,'conversation','s',0,4000).modules.conversation.height,4260)
const requested=E.resizePreset(E.defaultLayout('blank'),'conversation','custom',10000,5000);valid(requested);assert.equal(requested.modules.conversation.height,5000);assert.equal(requested.modules.conversation.width,1000)

// A connected T junction must use one shared boundary, preserving unrelated
// panes and satisfying the narrowest participating pane's safety minimum.
const tee=setup([['code',0,0,490,410],['workflow',500,0,500,200],['terminal',500,210,500,200],['conversation',0,600,1000,80]])
const moved=E.resizeTracks(tee,'terminal','w',-90,0);valid(moved)
assert.deepEqual(rect(moved,'code'),[0,0,400,410]);assert.deepEqual(rect(moved,'workflow'),[410,0,590,200]);assert.deepEqual(rect(moved,'terminal'),[410,210,590,200]);assert.deepEqual(rect(moved,'conversation'),rect(tee,'conversation'))
const up=E.resizeTracks(tee,'terminal','n',0,-120);valid(up);assert.deepEqual(rect(up,'workflow'),[500,0,500,80]);assert.deepEqual(rect(up,'terminal'),[500,90,500,320]);assert.deepEqual(rect(up,'code'),rect(tee,'code'))
const min=E.resizeTracks(tee,'terminal','w',-100000,0);valid(min);assert.equal(min.modules.code.width,64)
const pinned=structuredClone(tee);pinned.modules.workflow.pinned_position=true
const blocked=E.resizeTracks(pinned,'code','e',80,0);assert.deepEqual(rect(blocked,'workflow'),rect(pinned,'workflow'));assert.deepEqual(rect(blocked,'code'),rect(pinned,'code'));valid(blocked)

// 30 distinct gestures on 8 simultaneously visible panes. Serialization and
// session restoration must retain exact staggered geometry and ordering.
let many=setup(['conversation','code','diff','terminal','files','workflow','browser','collaboration'].map((id,i)=>[id,(i%4)*252.5,Math.floor(i/4)*210,242.5,200]))
const order=structuredClone(many)
for(let i=0;i<30;i++){const id=E.visibleIds(many)[i%8],edge=['e','w','n','s','se','nw'][i%6];many=E.resizeTracks(many,id,edge,i%2?14:-12,i%3?11:-9);valid(many)}
for(const a of E.visibleIds(order))for(const b of E.visibleIds(order)){if(a===b)continue;const p=order.modules[a],q=order.modules[b],r=many.modules[a],s=many.modules[b];if(p.x+p.width<=q.x&&Math.min(p.y+p.height,q.y+q.height)>Math.max(p.y,q.y))assert.ok(r.x+r.width<=s.x+.02);if(p.y+p.height<=q.y&&Math.min(p.x+p.width,q.x+q.width)>Math.max(p.x,q.x))assert.ok(r.y+r.height<=s.y+.02)}
const restored=E.migrateLayout(cleanFree(many));for(const id of E.visibleIds(many))assert.deepEqual(rect(restored,id),rect(many,id))
for(const width of [720,1024,1440]){const visible=E.effectiveLayout(tee,width).layout,next=E.canonicalLayout(E.resizeTracks(visible,'terminal','n',0,-50),tee);valid(next);assert.deepEqual(rect(next,'code'),rect(tee,'code'));assert.equal(next.modules.terminal.y+next.modules.terminal.height,410)}

// Repeated frames use total gesture displacement, not incremental geometry.
// Legacy/history objects must not be copied or changed on the gesture path.
const state=structuredClone(tee);state.history=Array.from({length:10},()=>structuredClone(tee));state.legacy={version:2,description:'retained migration metadata'}
const original=JSON.stringify(state),prepared=S.createWorkspaceResize(state,'terminal','w')
const first=prepared.update(-90,0),reverse=prepared.update(30,0),reset=prepared.update(0,0)
assert.equal(first.modules.code.width,400);assert.equal(reverse.modules.code.width,520);assert.deepEqual(rect(reset,'code'),rect(state,'code'));assert.equal(first.history,state.history);assert.equal(first.legacy,state.legacy);assert.equal(JSON.stringify(state),original);valid(first);valid(reverse)
const continuous=S.createWorkspaceResize(many,'terminal','s')
for(let i=0;i<500;i++)valid(continuous.update(0,i*11))

// Reproducible CPU-only characterization of the production geometry path.
// Clone instrumentation adds a little overhead; it is not a renderer measure.
const benchmark=(base,iterations=300)=>{const native=global.structuredClone;let calls=0,cloneMs=0;global.structuredClone=v=>{const start=performance.now();const result=native(v);cloneMs+=performance.now()-start;calls++;return result};let elapsed;try{const start=performance.now();for(let i=0;i<iterations;i++)E.resizeTracks(base,'code','s',0,200+i%7);elapsed=performance.now()-start}finally{global.structuredClone=native}return {iterations,input_bytes:Buffer.byteLength(JSON.stringify(base)),mean_ms:elapsed/iterations,clone_calls_per_update:calls/iterations,mean_clone_ms:cloneMs/iterations}}
const history=structuredClone(tee);history.history=Array.from({length:10},()=>structuredClone(tee))
const native=global.structuredClone,samples=[];let frameClones=0;global.structuredClone=v=>{frameClones++;return native(v)}
try{const resize=S.createWorkspaceResize(history,'code','s');for(let i=0;i<1000;i++){const start=performance.now();resize.update(0,200+i%7);samples.push(performance.now()-start)}}finally{global.structuredClone=native}
assert.equal(frameClones,0,'The prepared resize path must not clone undo history or legacy metadata')
samples.sort((a,b)=>a-b)
const report={pass:true,geometry_checks:['single_four_edges','no_vertical_max','shared_t_junction','upper_neighbor_shrinks','minimum_width','pinned_neighbor','eight_modules_30_resizes','exact_restore','responsive_canonical','gesture_relative_to_start','history_legacy_unchanged','500_continuous_frames'],visible_modules:8,resizes:30,split_engine_sha256:crypto.createHash('sha256').update(fs.readFileSync(require.resolve('../../desktop/frontend/src/WorkspaceSplitLayoutEngine.ts'))).digest('hex'),pure_cpu:{wrapper_no_history:benchmark(tee),wrapper_ten_history:benchmark(history),prepared_ten_history:{iterations:1000,clone_calls:frameClones,mean_ms:samples.reduce((sum,n)=>sum+n,0)/samples.length,p50_ms:samples[500],p95_ms:samples[950],max_ms:samples[999]}}}
console.log(JSON.stringify(report))
