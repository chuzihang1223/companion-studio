'use strict'
const {normalizeName}=require('./application-record.cjs')
function matchApplications(target,records){
 const query=normalizeName(target)
 if(!query)return []
 return records.filter(r=>r.installed).map(r=>{
  const names=[r.name,...r.aliases||[],...r.successful_aliases||[],...r.shortcut_names||[],...r.executable_names||[]].map(normalizeName)
  const score=names.includes(query)?1:names.some(n=>n.startsWith(query)&&query.length>=3)?.88:0
  return {app_id:r.app_id,name:r.name,score,sources:r.sources,launch_candidates:r.launch_candidates}
 }).filter(r=>r.score).sort((a,b)=>b.score-a.score||a.name.localeCompare(b.name)).slice(0,8)
}
class RuntimeApplicationResolver{
 constructor(discovery){this.discovery=discovery;this.pending=new Map();this.last=null}
 resolve(target,diagnostic=false){
  target=String(target||'').trim();if(!target||target.length>150||/[\r\n\0]/.test(target))throw Error('无效应用名称')
  const key=normalizeName(target)+':'+diagnostic
  if(!this.pending.has(key))this.pending.set(key,this.perform(target,diagnostic).finally(()=>this.pending.delete(key)))
  return this.pending.get(key)
 }
 async perform(target,diagnostic){
  const started=Date.now(),levels=[],cached=matchApplications(target,this.discovery.list())
  levels.push({source:'index',matches:cached.length,scanner_version:this.discovery.originalVersion})
  if(cached.length&&this.discovery.originalVersion>=5&&!diagnostic)return this.last={target,candidates:cached,levels,cache_hit:true,elapsed_ms:Date.now()-started}
  let candidates=[]
  for(const source of ['registered','running','start']){
   try{
    const found=await this.discovery.query(target,source)
    levels.push({source,count:found.applications.length,sources:found.source_status})
    if(found.applications.length)await this.discovery.upsert(found)
    candidates=matchApplications(target,this.discovery.list())
    if(candidates.length&&!diagnostic)break
   }catch(error){levels.push({source,error:String(error.message)})}
  }
  return this.last={target,candidates:candidates.length?candidates:cached,levels,cache_hit:false,elapsed_ms:Date.now()-started}
 }
}
module.exports={RuntimeApplicationResolver,matchApplications}
