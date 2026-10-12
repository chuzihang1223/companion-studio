const fs=require('node:fs'),path=require('node:path'),paths=require('./paths.cjs')
const ids=['conversation','workflow','collaboration','code','diff','terminal','browser','files','application','media','system','preview','artifacts','permissions']
const file=path.join(paths.userData,'workbench-layout.json')
function clean(value){
 if(value?.version===3)return require('./free-layout-storage.cjs').cleanFree(value)
 if(value?.version===2)return cleanGrid(value)
 if(value?.version!==1||!ids.includes(value.active)||!value.modules)throw Error('无效工作台布局')
 const modules={}
 for(const id of ids){const v=value.modules[id];if(!v)continue;modules[id]={position:v.position==='side'?'side':'main',size:v.size==='maximized'?'maximized':'normal',dockSide:['left','right'].includes(v.dockSide)?v.dockSide:null,collapsed:!!v.collapsed,minimized:!!v.minimized,closed:id==='conversation'?false:!!v.closed,order:Math.max(0,Math.min(100,Number(v.order)||0))}}
 return {version:1,active:value.active,locked:!!value.locked,modules}
}
const number=(v,fallback,min,max)=>Math.max(min,Math.min(max,Number.isFinite(Number(v))?Number(v):fallback))
function cleanGrid(value){
 if(!ids.includes(value.active)||!value.modules||!Array.isArray(value.columns)||!value.columns.length||value.columns.length>4||!Array.isArray(value.rows)||!value.rows.length||value.rows.length>40)throw Error('无效网格布局')
 if(JSON.stringify(value).length>64000)throw Error('布局数据过长')
 const columns=value.columns.map((v,i)=>({id:'column-'+i,width:number(v.width,320,160,3000),minWidth:160})),rows=value.rows.map((v,i)=>({id:'row-'+i,height:number(v.height,220,38,960),minHeight:140})),modules={}
 for(const [order,id] of ids.entries()){
  const v=value.modules[id]||{},column=Math.floor(number(v.column,0,0,columns.length-1))
  modules[id]={module_type:id,module_instance_id:id,column,row:Math.floor(number(v.row,0,0,32)),columnSpan:Math.floor(number(v.columnSpan,1,1,columns.length-column)),rowSpan:Math.floor(number(v.rowSpan,1,1,4)),position:v.position==='side'?'side':'main',size:v.size==='maximized'?'maximized':'normal',dockSide:['left','right'].includes(v.dockSide)?v.dockSide:null,collapsed:!!v.collapsed,minimized:!!v.minimized,closed:id==='conversation'?false:!!v.closed,order:Math.floor(number(v.order,order,0,100))}
 }
 return {version:2,active:value.active,locked:!!value.locked,columns,rows,modules,...(value.legacy?{legacy:value.legacy}:{})}
}
module.exports=function layout(key,value){
 if(typeof key!=='string'||!key.length||key.length>100)throw Error('无效会话')
 let state={};try{state=JSON.parse(fs.readFileSync(file,'utf8'))}catch{}
 if(!value)return state[key]||null
 if(value.splitLayout&&Object.values(state).some(v=>v?.version===3&&!v.splitLayout)&&!fs.existsSync(file+'.v86-backup'))fs.copyFileSync(file,file+'.v86-backup',fs.constants.COPYFILE_EXCL)
 if(value.version===3&&Object.values(state).some(v=>v?.version<3)&&!fs.existsSync(file+'.v85-backup'))fs.copyFileSync(file,file+'.v85-backup',fs.constants.COPYFILE_EXCL)
 // Archive V8.2 once; preserve existing keys rather than deleting layout history.
 if(value.version===2&&Object.values(state).some(v=>v?.version===1)&&!fs.existsSync(file+'.v82-backup'))fs.copyFileSync(file,file+'.v82-backup',fs.constants.COPYFILE_EXCL)
 state[key]=clean(value)
 // Bound abandoned histories; layouts contain no chat text or credentials.
 const tmp=file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(state));fs.renameSync(tmp,file)
 return state[key]
}
