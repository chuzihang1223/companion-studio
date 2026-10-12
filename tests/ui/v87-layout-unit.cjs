const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path'),ts=require('../../desktop/node_modules/typescript')
require.extensions['.ts']=(m,f)=>m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,f)
const E=require('../../desktop/frontend/src/WorkspaceLayoutEngine.ts'),S=require('../../desktop/frontend/src/WorkspaceSplitLayoutEngine.ts'),{cleanFree}=require('../../desktop/electron/free-layout-storage.cjs')
const rect=(l,id)=>{const p=l.modules[id];return [p.x,p.y,p.width,p.height]}
function setup(slots){const l=E.defaultLayout('blank');for(const p of Object.values(l.modules))p.closed=true
 for(const [id,x,y,width,height] of slots)Object.assign(l.modules[id],{closed:false,x,y,width,height});return S.withWorkspaceSplits(l)}
function valid(l){for(const id of E.visibleIds(l)){const p=l.modules[id];assert.ok(p.x>=-.01&&p.y>=-.01&&p.width>0&&p.height>=40&&p.x+p.width<=l.canvasWidth+.01);for(const other of E.visibleIds(l))if(id!==other)assert.equal(E.overlap(p,l.modules[other]),false,id+' / '+other)}cleanFree(l)}
const vertical=setup([['workflow',0,0,1000,200],['terminal',0,210,1000,200],['conversation',0,500,1000,80]])
const v=E.resizeTracks(vertical,'terminal','n',0,-70);valid(v)
assert.deepEqual(rect(v,'workflow'),[0,0,1000,130]);assert.deepEqual(rect(v,'terminal'),[0,140,1000,270]);assert.deepEqual(rect(v,'conversation'),rect(vertical,'conversation'))
const t=setup([['code',0,0,490,410],['workflow',500,0,500,200],['terminal',500,210,500,200],['conversation',0,500,1000,80]])
const up=E.resizeTracks(t,'terminal','n',0,-60);valid(up);assert.deepEqual(rect(up,'code'),rect(t,'code'));assert.equal(up.modules.workflow.y,0);assert.equal(up.modules.terminal.y,150)
const left=E.resizeTracks(t,'workflow','w',-80,0);valid(left)
assert.deepEqual(rect(left,'code'),[0,0,410,410]);assert.deepEqual(rect(left,'workflow'),[420,0,580,200]);assert.deepEqual(rect(left,'terminal'),[420,210,580,200]);assert.deepEqual(rect(left,'conversation'),rect(t,'conversation'))
const limited=E.resizeTracks(t,'workflow','w',-10000,0);valid(limited);assert.equal(limited.modules.code.width,64);assert.equal(limited.modules.workflow.y,0);assert.equal(limited.modules.code.y,0)
const back=E.resizeTracks(left,'workflow','w',80,0);valid(back);for(const id of E.visibleIds(t))assert.deepEqual(rect(back,id),rect(t,id))
for(const width of [720,1024]){const view=E.effectiveLayout(t,width).layout;assert.equal(view.modules.terminal.y,210);assert.equal(view.modules.workflow.height,200);assert.ok(S.workspaceDividers(view).some(d=>d.axis==='y'&&d.before.includes('workflow')&&d.after.includes('terminal')));const resized=E.canonicalLayout(E.resizeTracks(view,'terminal','n',0,-60),t);assert.equal(resized.modules.terminal.y+resized.modules.terminal.height,410);assert.deepEqual(rect(resized,'code'),rect(t,'code'))}
const standalone=E.resizePreset(E.defaultLayout('blank'),'conversation','custom',10000,1800);valid(standalone);assert.equal(standalone.modules.conversation.width,1000);assert.equal(standalone.modules.conversation.height,1800)
const obstructed=E.resizeTracks(t,'code','s',0,400);valid(obstructed);assert.ok(obstructed.modules.code.height<=500.01);assert.deepEqual(rect(obstructed,'conversation'),rect(t,'conversation'))
const staggered=setup([['code',0,0,490,200],['workflow',0,210,490,200],['diff',500,0,500,190],['browser',500,200,500,210],['conversation',0,500,1000,80]])
const staggeredRestore=E.migrateLayout(cleanFree(staggered));for(const id of E.visibleIds(staggered))assert.deepEqual(rect(staggeredRestore,id),rect(staggered,id),'Restoring a valid staggered layout must not repack it')
let complex=setup(['conversation','code','diff','terminal','files','workflow','browser','collaboration'].map((id,i)=>[id,(i%4)*252.5,Math.floor(i/4)*210,242.5,200]))
const initial=structuredClone(complex),start=performance.now()
for(let i=0;i<250;i++){const id=E.visibleIds(complex)[i%8],edge=['e','w','n','s','se','nw'][i%6];complex=E.resizeTracks(complex,id,edge,i%2?14:-12,i%3?11:-9);valid(complex)
 for(const a of E.visibleIds(initial))for(const b of E.visibleIds(initial)){if(a===b)continue;const p=initial.modules[a],q=initial.modules[b],r=complex.modules[a],s=complex.modules[b]
  if(p.x+p.width<=q.x&&Math.min(p.y+p.height,q.y+q.height)>Math.max(p.y,q.y))assert.ok(r.x+r.width<=s.x+.02,'Horizontal order changed')
  if(p.y+p.height<=q.y&&Math.min(p.x+p.width,q.x+q.width)>Math.max(p.x,q.x))assert.ok(r.y+r.height<=s.y+.02,'Vertical order changed')
 }
}
const elapsed=performance.now()-start,saved=cleanFree(complex),restored=E.migrateLayout(saved)
for(const id of E.visibleIds(complex))assert.deepEqual(rect(restored,id),rect(complex,id));assert.deepEqual(restored.splitLayout,saved.splitLayout)
const untouched=JSON.stringify(complex);valid(E.effectiveLayout(complex,420).layout);assert.equal(JSON.stringify(complex),untouched)
const bad=structuredClone(saved);bad.splitLayout.dividers[0].ratio=0;assert.throws(()=>cleanFree(bad),/共享分割线/)
const baseline = null;
console.log(JSON.stringify({pass:true,modules:8,resizes:250,mean_ms:elapsed/250,shared_vertical:true,shared_horizontal:true,t_junction_unchanged:true,exact_restore:true,baseline}))
