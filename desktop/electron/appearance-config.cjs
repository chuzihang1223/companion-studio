const fs=require('node:fs'),path=require('node:path')
const paths=require('./paths.cjs')
const file=path.join(paths.userData,'themes','theme.json')
const background=()=>({mode:'color',color:'#f5f7fa',gradient_start:'#f5f7fa',gradient_end:'#e8eef5',brightness:100,blur:0,mask:0,panel:100})
function assets(){try{return JSON.parse(fs.readFileSync(file,'utf8'))}catch{return {}}}
function current(){const data=assets(),config=data.app_theme||{theme:'light',chat:background(),work:{...background(),follow_chat:true}};return {...config,migrated:!!data.app_theme,chat:{...background(),...config.chat,image:data.background?`/api/appearance/background?v=${path.parse(data.background).name}`:null},work:{...background(),follow_chat:true,...config.work,image:data.work_background?`/api/appearance/work_background?v=${path.parse(data.work_background).name}`:null}}}
function apply(value){
 if(!value||!['light','dark'].includes(value.theme))throw Error('无效主题')
 const result={theme:value.theme}
 for(const key of ['chat','work']){
  const input=value[key]||{},row=background()
  if(!['default','color','gradient','image'].includes(input.mode))throw Error('无效背景类型')
  row.mode=input.mode
  for(const name of ['color','gradient_start','gradient_end']){if(!/^#[0-9a-f]{6}$/i.test(input[name]))throw Error('无效背景颜色');row[name]=input[name]}
  for(const [name,min,max] of [['brightness',40,150],['blur',0,24],['mask',0,100],['panel',55,100]]){if(!Number.isFinite(input[name])||input[name]<min||input[name]>max)throw Error('无效背景参数');row[name]=input[name]}
  if(key==='work')row.follow_chat=input.follow_chat!==false
  result[key]=row
 }
 const data=assets();if(!data.legacy_theme)data.legacy_theme={background:data.background||null};data.app_theme=result
 fs.mkdirSync(path.dirname(file),{recursive:true});const temp=file+'.tmp';fs.writeFileSync(temp,JSON.stringify(data),'utf8');fs.renameSync(temp,file)
 return current()
}
module.exports={assets,current,apply}
