'use strict'
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process')
const {normalizeRecord,windowsPath,available}=require('./application-record.cjs')
const priority={registry_uninstall:5,start_menu:4,desktop_shortcut:4,apps_folder:3,app_paths:2,running:1,url_protocol:0}
function mergeApplications(scanned,previous=[],{partial=false}={}){
 if(!Array.isArray(scanned.applications)||!scanned.scanned_at)throw Error('Invalid scan result')
 const groups=new Map(),counts={},oldRecords=previous.map(normalizeRecord).filter(Boolean)
 const catalog=Array.isArray(scanned.catalog_metadata)?scanned.catalog_metadata:[]
 for(const input of scanned.applications){
  const metadata=catalog.filter(item=>windowsPath(item.install_location)&&input.executable&&windowsPath(input.executable).toLowerCase().startsWith(windowsPath(item.install_location).toLowerCase()+'\\'))
  const row=normalizeRecord({...input,aliases:[...(input.aliases||[]),...metadata.map(item=>item.name)].filter(Boolean),publisher:input.publisher||metadata[0]?.publisher});if(!row)continue
  counts[row.source]=(counts[row.source]||0)+1
  const key=(row.executable||row.app_user_model_id||row.protocol||row.shortcut_path).toLowerCase(),existing=groups.get(key)
  if(existing){
   const combined={...existing,aliases:[...new Set([...existing.aliases,...row.aliases])],sources:[...new Set([...existing.sources,...row.sources])],
    launch_candidates:[...existing.launch_candidates,...row.launch_candidates]}
   if((priority[row.source]??-1)>(priority[existing.source]??-1)){combined.name=row.name;combined.display_name=row.name;combined.source=row.source}
   for(const field of ['icon','install_location','publisher','app_user_model_id','package_family','application_kind','process_names'])if(!combined[field]&&row[field])combined[field]=row[field]
   groups.set(key,normalizeRecord(combined));continue
  }
  const old=oldRecords.filter(item=>(item.executable||item.app_user_model_id||item.protocol||item.shortcut_path).toLowerCase()===key)
  if(!old.length){const moved=oldRecords.filter(item=>item.app_user_model_id&&item.app_user_model_id===row.app_user_model_id||item.shortcut_path&&item.shortcut_path===row.shortcut_path);if(moved.length===1)old.push(moved[0])}
  let combined=row
  if(partial&&old.length){
   const prior=old[0],keepName=(priority[prior.source]??-1)>(priority[row.source]??-1)
   combined={...prior,...row,name:keepName?prior.name:row.name,source:keepName?prior.source:row.source,
    aliases:[...new Set([...old.flatMap(item=>item.aliases||[]),...row.aliases])],
    sources:[...new Set([...old.flatMap(item=>item.sources||[]),...row.sources])],
    launch_candidates:[...old.flatMap(item=>item.launch_candidates||[]),...row.launch_candidates]}
   for(const field of ['icon','install_location','publisher','app_user_model_id','package_family','application_kind','process_names'])if(!combined[field]&&prior[field])combined[field]=prior[field]
   combined=normalizeRecord(combined)
  }
  groups.set(key,{...combined,app_id:old[0]?.app_id||row.app_id,legacy_ids:[...new Set(old.flatMap(item=>[item.app_id,...item.legacy_ids||[]]))],
   successful_aliases:old.flatMap(item=>item.successful_aliases||[]),preferred_launch_method:old.find(item=>item.preferred_launch_method)?.preferred_launch_method,
   launch_success_count:Math.max(0,...old.map(item=>item.launch_success_count||0)),launch_failure_count:Math.max(0,...old.map(item=>item.launch_failure_count||0)),
   last_successful_method:old.find(item=>item.last_successful_method)?.last_successful_method,
   verified_process_paths:[...new Set(old.flatMap(item=>item.verified_process_paths||[]))],last_seen:scanned.scanned_at,installed:true})
 }
 const rows=[...groups.values()],ids=new Set(rows.flatMap(row=>[row.app_id,...row.legacy_ids||[]]))
 for(const row of rows){
  const prior=oldRecords.find(old=>old.app_id===row.app_id)
  for(const entry of row.launch_candidates){const old=prior?.launch_candidates?.find(item=>item.id===entry.id);if(old){entry.launch_success_count=old.launch_success_count||0;entry.launch_failure_count=old.launch_failure_count||0}}
 }
 for(const old of oldRecords){
  if(ids.has(old.app_id))continue
  if(old.source==='custom'){rows.push({...old,installed:available({...old,installed:true})});counts.custom=(counts.custom||0)+1}
  else rows.push({...old,installed:scanned.source_status&&Object.values(scanned.source_status).some(s=>!s.complete)?old.installed:false})
 }
 return {version:5,scanner_version:5,registry_version:5,applications:rows,counts,scanned_at:scanned.scanned_at,last_scan_time:scanned.scanned_at,catalog_metadata:catalog,source_status:scanned.source_status||{}}
}
class ApplicationDiscoveryService{
 constructor(paths,audit=()=>{}){
  this.paths=paths;this.audit=audit;this.file=path.join(paths.userData,'application-registry.json');this.pending=null
  this.originalVersion=0;this.cache={version:5,scanner_version:5,applications:[],counts:{},scanned_at:null}
  try{const saved=JSON.parse(fs.readFileSync(this.file,'utf8'));if(Array.isArray(saved.applications)){this.originalVersion=saved.version;this.cache=mergeApplications({...saved,applications:saved.applications.filter(row=>row.installed!==false),scanned_at:saved.scanned_at||new Date().toISOString()},saved.applications)}}catch{}
 }
 list(){return this.cache.applications.map(row=>row.launch_candidates?row:normalizeRecord(row)).filter(Boolean).map(row=>({...row,installed:available(row)}))}
 find(id){const source=this.cache.applications.find(row=>row.app_id===id||row.legacy_ids?.includes(id));const row=source&&(source.launch_candidates?source:normalizeRecord(source));return row?{...row,installed:available(row)}:undefined}
 discoverDelegate(id){
  const row=this.cache.applications.find(item=>item.app_id===id||item.legacy_ids?.includes(id));if(!row)return
  const delegate=require('./launcher-delegates.cjs').findLauncherDelegate(row,this.list())
  if(delegate){
   row.launcher_delegate=delegate
   row.verified_process_paths=[...new Set([...(row.verified_process_paths||[]),delegate.path])]
   this.save(this.cache)
   this.audit('app-launch.log',JSON.stringify({event:'registered_launcher_delegate',app_id:row.app_id,delegate_app_id:delegate.app_id,executable:delegate.path}))
  }
 }
 save(next){
  fs.mkdirSync(this.paths.userData,{recursive:true})
  if(this.originalVersion&&this.originalVersion<5&&fs.existsSync(this.file)&&!fs.existsSync(this.file+'.v85-backup'))fs.copyFileSync(this.file,this.file+'.v85-backup',fs.constants.COPYFILE_EXCL)
  if(this.originalVersion&&this.originalVersion<3&&fs.existsSync(this.file)&&!fs.existsSync(this.file+'.v83-backup'))fs.copyFileSync(this.file,this.file+'.v83-backup',fs.constants.COPYFILE_EXCL)
  const temporary=this.file+'.tmp';fs.writeFileSync(temporary,JSON.stringify(next),'utf8');fs.renameSync(temporary,this.file);this.cache=next;this.originalVersion=5
  return {...next,applications:this.list()}
 }
 addCustom(id,name,executable,metadata={}){
  const row=normalizeRecord({app_id:id,name,executable:windowsPath(executable),icon:executable,install_location:path.win32.dirname(executable),source:'custom',last_seen:new Date().toISOString(),installed:true,...metadata})
  if(!row)throw Error('启动入口无效')
  const applications=[...this.cache.applications.filter(item=>item.app_id!==id),row]
  this.save({...this.cache,applications,counts:{...this.cache.counts,custom:applications.filter(item=>item.source==='custom').length}});return row
 }
 recordLaunch(id,candidateId,success,targetText,processes=[]){
  const row=this.cache.applications.find(row=>row.app_id===id||row.legacy_ids?.includes(id));if(!row)return
  const entry=row.launch_candidates.find(item=>item.id===candidateId),field=success?'launch_success_count':'launch_failure_count'
  row[field]=(row[field]||0)+1;if(entry){entry[field]=(entry[field]||0)+1;if(success)entry.last_success_at=new Date().toISOString()}
  if(success){row.preferred_launch_method=candidateId;row.last_successful_method=entry?.launch_type;row.last_used=new Date().toISOString();if(targetText&&String(targetText).length<150)row.successful_aliases=[...new Set([...row.successful_aliases||[],String(targetText)])].slice(-40)}
  if(success&&row.launch_candidates.some(item=>item.launch_type==='launcher')){
   row.verified_process_paths=[...new Set([...(row.verified_process_paths||[]),...processes.map(item=>windowsPath(item.path)).filter(value=>value&&!/\\(?:Windows|System32|SysWOW64)\\|\\(?:cmd|powershell|pwsh|explorer|svchost|rundll32)\.exe$/i.test(value))])].slice(-20)
  }
  this.save(this.cache)
 }
 scan(){if(!this.pending)this.pending=this.run().finally(()=>this.pending=null);return this.pending}
 async upsert(result){
  // A targeted result cannot declare unrelated cached apps uninstalled.
  return this.save(mergeApplications({...result,catalog_metadata:[...(this.cache.catalog_metadata||[]),...(result.catalog_metadata||[])],source_status:{...(result.source_status||{}),targeted:{complete:false}}},this.cache.applications,{partial:true}))
 }
 query(target,source='registered'){return this.run(target,source)}
 run(target,source='all'){return new Promise((resolve,reject)=>{
  const file=path.join(__dirname.replace('app.asar','app.asar.unpacked'),'automation','discover-applications.ps1'),started=Date.now()
  const args=['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',file];if(target)args.push('-Query',target,'-SourceGroup',source)
  const child=spawn('powershell.exe',args,{windowsHide:true,env:{...process.env,TEMP:this.paths.cache,TMP:this.paths.cache}})
  let output='',settled=false;const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);this.audit('app-discovery.log',JSON.stringify({event:error?'scan_failed':'scan_completed',elapsed_ms:Date.now()-started,count:value?.applications.length,error:error?.message}));error?reject(error):resolve(value)}
  const timer=setTimeout(()=>{child.kill();finish(Error('应用扫描超时，已保留原缓存'))},target?18000:60000)
  child.stdout.setEncoding('utf8');child.stdout.on('data',part=>{output+=part;if(output.length>12e6){child.kill();finish(Error('应用发现结果过长，已保留原缓存'))}});child.stderr.resume()
  child.on('error',error=>finish(error));child.on('close',code=>{if(settled)return;if(code!==0)return finish(Error('Windows 应用扫描失败，已保留原缓存'));try{const result=JSON.parse(output);finish(null,target?result:this.save(mergeApplications(result,this.cache.applications)))}catch{finish(Error('无法读取应用扫描结果，已保留原缓存'))}})
 })}
}
module.exports={ApplicationDiscoveryService,mergeApplications}
