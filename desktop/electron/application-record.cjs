'use strict'
const path=require('node:path'),crypto=require('node:crypto'),fs=require('node:fs')
const normalizeName=value=>String(value||'').normalize('NFKC').toLowerCase().replace(/\b(?:v?\d+(?:\.\d+){1,4}|x64|x86|64-bit|32-bit|user)\b/g,'').replace(/[\s\p{P}\p{S}_]+/gu,'')
function windowsPath(value){
 const candidate=String(value||'').trim().replace(/^"(.*)"$/,'$1').replace(/%([^%]+)%/g,(match,name)=>Object.entries(process.env).find(([key])=>key.toLowerCase()===name.toLowerCase())?.[1]||match)
 if(!candidate||/[\r\n\0]/.test(candidate)||!path.win32.isAbsolute(candidate))return ''
 return path.win32.normalize(candidate)
}
const nonApp=value=>/(?:^|[\s_\-.\\/])(?:unins\w*|uninstall\w*|setup\w*|dpinst\w*|vcredist\w*|vc_redist\w*|crash\w*|maintenancetool|update\.exe)(?:$|[\s_\-.])/i.test(value)||/卸载|驱动程序包|redistributable|prerequisites|webview2 runtime/i.test(value)
function candidate(type,target,row={}){
 if(!target)return null
 const args=String(row.arguments||''),cwd=windowsPath(row.working_directory||row.install_location),key=type+'\0'+target.toLowerCase()+'\0'+args
 return {id:crypto.createHash('sha256').update(key).digest('hex').slice(0,20),launch_type:type,target,
  arguments:args,working_directory:cwd,name:row.name||'',source:row.source||'custom',confidence:row.source==='custom'?1:.9,
  executable_path:windowsPath(row.executable_path||row.executable),launch_success_count:0,launch_failure_count:0}
}
function normalizeRecord(row){
 const executable=windowsPath(row.executable_path||row.executable||row.path),shortcut=windowsPath(row.shortcut_path||row.launch_target)
 const aumid=String(row.app_user_model_id||'').trim(),protocol=String(row.protocol||'').trim(),name=String(row.display_name||row.name||'').trim()
 if(!name||nonApp(name)||executable&&nonApp(path.win32.basename(executable)))return null
 const candidates=(row.launch_candidates||[]).map(item=>({...item,target:['exe','shortcut','launcher','custom','start_app'].includes(item.launch_type)?windowsPath(item.target):String(item.target||'')})).filter(item=>item.target)
 if(!candidates.length){
  if(shortcut&&path.win32.extname(shortcut).toLowerCase()==='.lnk')candidates.push(candidate('shortcut',shortcut,{...row,executable}))
  if(executable)candidates.push(candidate(row.source==='start_apps'?'start_app':/launcher/i.test(path.win32.basename(executable))?'launcher':row.source==='custom'?'custom':'exe',executable,{...row,executable}))
  if(aumid&&!/^[A-Za-z]:[\\/]|^https?:/i.test(aumid))candidates.push(candidate(row.package_family||row.application_kind==='packaged'?'app_user_model_id':'shell_app',aumid,row))
  if(protocol&&/^[a-z][a-z0-9+.-]*:/i.test(protocol))candidates.push(candidate('uri_protocol',protocol,row))
 }
 const byId=new Map();for(const item of candidates.filter(Boolean))if(!byId.has(item.id))byId.set(item.id,item)
 const unique=[...byId.values()]
 if(!unique.length)return null
 const aliases=new Set([name,...row.aliases||[],...row.successful_aliases||[]])
 if(executable){const base=path.win32.basename(executable,'.exe');if(!/^(?:launcher|app|main|setup|uninstall|explorer)$/i.test(base))aliases.add(base)}
 if(shortcut)aliases.add(path.win32.basename(shortcut,'.lnk'))
 const key=(executable||aumid||protocol||shortcut).toLowerCase(),primary=rankCandidates({...row,name,launch_candidates:unique})[0]
 const resolvedShortcut=shortcut||unique.find(item=>item.launch_type==='shortcut')?.target||''
 return {...row,app_id:row.app_id||'installed_'+crypto.createHash('sha256').update(key).digest('hex').slice(0,20),
  name,display_name:name,normalized_name:normalizeName(name),aliases:[...aliases],shortcut_names:shortcut?[path.win32.basename(shortcut,'.lnk')]:row.shortcut_names||[],executable_names:executable?[path.win32.basename(executable)]:row.executable_names||[],protocols:protocol?[protocol]:row.protocols||[],window_titles:[],executable,executable_path:executable||null,
  shortcut_path:resolvedShortcut||null,launch_target:resolvedShortcut,app_user_model_id:aumid||null,protocol:protocol||null,
  install_location:windowsPath(row.install_location),arguments:primary.arguments,launch_type:primary.launch_type,launch_candidates:unique,
  launch_success_count:row.launch_success_count||0,launch_failure_count:row.launch_failure_count||0,
  sources:row.sources||[row.source||'custom'],installed:row.installed!==false}
}
function rankCandidates(record){
 const name=normalizeName(record.display_name||record.name)
 const score=item=>(item.id===record.preferred_launch_method?10000:0)+(item.launch_success_count||0)*120-(item.launch_failure_count||0)*25+
  (item.launch_type==='shortcut'?90:item.launch_type==='app_user_model_id'?85:item.launch_type==='uri_protocol'?75:item.launch_type==='launcher'?60:50)+
  (!item.arguments?60:0)+(normalizeName(item.name)===name?40:0)
 return [...record.launch_candidates||[]].sort((a,b)=>score(b)-score(a))
}
function available(record){return record.installed!==false&&record.launch_candidates.some(item=>['exe','shortcut','launcher','custom','start_app'].includes(item.launch_type)?fs.existsSync(item.target):Boolean(item.target))}
module.exports={windowsPath,normalizeName,normalizeRecord,rankCandidates,available,nonApp,candidate}
