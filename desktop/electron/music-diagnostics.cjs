const fs=require('node:fs'),path=require('node:path')
const {searchEdit,searchHint}=require('./music-adapters.cjs')
function publicTree(tree){
 return tree.elements.map(row=>({control_type:row.type,name:/搜索|search|播放|暂停|单曲|歌曲|下一首|上一首/i.test(row.name)?row.name:'[text omitted]',automation_id:row.automation_id,class_name:row.class_name,bounding_rect:row.bounds,enabled:row.enabled,visible:row.visible??!row.offscreen}))
}
async function runMusicDiagnostics({controller,registry,cache,adapter,query='远航星的告别',launch=false,search=false}){
 const folder=path.join(cache,'music-diagnostics');fs.mkdirSync(folder,{recursive:true})
 const report={created_at:new Date().toISOString(),client:registry.detect('netease'),steps:[],search_query:query}
 const step=async(name,run)=>{try{const result=await run();report.steps.push({name,status:'ok',result});return result}catch(error){report.steps.push({name,status:'error',error:String(error.message||error)});return null}}
 if(launch)await step('launch',async()=>{const result=await registry.launch('netease');await adapter.wait_ready();return result})
 const windows=await step('windows',()=>controller.list_windows('netease'))
 const target=windows?.filter(w=>w.responding&&w.bounds[2]>=240&&w.bounds[3]>=160).sort((a,b)=>b.bounds[2]*b.bounds[3]-a.bounds[2]*a.bounds[3])[0]
 if(target){
  adapter.window=target
  const tree=await step('uia_tree',async()=>{
   const value=await controller.inspect_ui_tree('netease',target.handle),rows=publicTree(value)
   fs.writeFileSync(path.join(folder,'netease-ui-tree.txt'),rows.map(r=>JSON.stringify(r)).join('\n'),'utf8')
   return {count:rows.length,search_edit_present:!!searchEdit(value),elements:rows}
  })
  if(search){await step('search',()=>adapter.search(query));report.strategies=adapter.diagnostics}
  else if(tree&&!tree.search_edit_present)report.shortcut={tested:false,reason:'No shortcut is assumed; visual search requires an explicit diagnostic search'}
  await step('player',()=>adapter.get_now_playing({passive:true}))
 }
 await step('media_sessions',()=>controller.media_sessions('get',{allow_browser:false}))
 report.path=path.join(folder,'diagnostics.json');fs.writeFileSync(report.path,JSON.stringify(report,null,2),'utf8')
 return report
}
module.exports={runMusicDiagnostics,publicTree}
