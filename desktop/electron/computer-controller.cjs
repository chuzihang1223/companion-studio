const {spawn}=require('node:child_process')
const readline=require('node:readline'),fs=require('node:fs'),path=require('node:path')
class ComputerController {
 constructor(registry,cache,audit=()=>{},guard=async()=>{}){this.registry=registry;this.guard=guard;this.cache=path.join(cache,'desktop-automation');fs.mkdirSync(this.cache,{recursive:true});this.audit=audit;this.pending=new Map();this.sequence=0;this.serial=Promise.resolve();this.worker=null;this.launchSeeds=new Map()}
 start(){
  if(this.worker)return
  const root=path.join(__dirname.replace('app.asar','app.asar.unpacked'),'automation')
  this.worker=spawn('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',path.join(root,'windows-worker.ps1')],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,TEMP:this.cache,TMP:this.cache}})
  readline.createInterface({input:this.worker.stdout}).on('line',line=>{let data;try{data=JSON.parse(line)}catch{return}const slot=this.pending.get(data.id);if(!slot)return;this.pending.delete(data.id);clearTimeout(slot.timer);data.error?slot.reject(Error(data.error)):slot.resolve(data.result)})
  this.worker.stderr.on('data',()=>this.audit('computer-control.log','worker_diagnostic','Windows automation worker reported a diagnostic'))
  const child=this.worker
  child.on('exit',()=>{if(this.worker===child)this.worker=null;for(const [id,slot] of this.pending){if(slot.child!==child)continue;clearTimeout(slot.timer);slot.reject(Error('Desktop worker stopped'));this.pending.delete(id)}})
 }
 call(app,action,args={}){
  const run=async()=>{await this.guard(action);return new Promise((resolve,reject)=>{
   const globalMedia=action==='media_all'
   const entry=globalMedia?null:this.registry.detect(app);if(entry&&!entry.installed)return reject(Error(`${entry.name}未安装`))
   this.start();const id=++this.sequence
   const timer=setTimeout(()=>{this.pending.delete(id);this.stop();reject(Error('桌面控件读取超时'))},22000)
   this.pending.set(id,{resolve,reject,timer,child:this.worker})
   const seed=this.launchSeeds.get(app)
   const request={...args,id,app,action,executable:entry?.path||entry?.executable,
    executables:[...(entry?.launch_candidates?.map(row=>row.executable_path||(['exe','launcher','custom'].includes(row.launch_type)?row.target:null)).filter(Boolean)||[]),...(entry?.verified_process_paths||[])],
    app_user_model_id:entry?.app_user_model_id,install_location:entry?.install_location,
    include_descendants:entry?.launch_candidates?.some(row=>row.launch_type==='launcher')||false,
    descendant_seed_ids:seed&&Date.now()-seed.saved<45000?[seed.pid]:[],
    descendant_seed_times:seed&&Date.now()-seed.saved<45000?[seed.created]:[],
    capture:path.join(this.cache,`${app}.png`)}
   this.audit('computer-control.log',action,app)
   this.worker.stdin.write(JSON.stringify(request)+'\n')
  })}
  const current=this.serial.then(run,run);this.serial=current.catch(()=>{});return current
 }
 async list_windows(app){return (await this.call(app,'list')).windows}
 observe_app(app){return this.call(app,'observe')}
 async launch_entry(app,candidateId){
  const entry=this.registry.detect(app),candidate=entry?.launch_candidates?.find(row=>row.id===candidateId)
  if(!candidate||!['exe','launcher','shortcut','custom','start_app'].includes(candidate.launch_type))throw Error('启动入口不在已授权应用中')
  const state=await this.call(app,'launch_entry',{launch_target:candidate.target,launch_arguments:candidate.launch_type==='shortcut'?'':candidate.arguments,launch_cwd:candidate.working_directory})
  if(state.pid&&state.created)this.launchSeeds.set(app,{pid:state.pid,created:state.created,saved:Date.now()})
  return state
 }
 async wait_ready(app,timeout=20000){const end=Date.now()+timeout;do{
  const rows=await this.list_windows(app)
  // Player tooltips belong to the same EXE; they are not an actionable main window.
  const ready=rows.filter(row=>row.responding&&row.bounds[2]>=240&&row.bounds[3]>=160).sort((a,b)=>b.bounds[2]*b.bounds[3]-a.bounds[2]*a.bounds[3])[0]
  if(ready)return this.verify_window_state(app,ready.handle)
  const minimized=rows.find(row=>row.responding&&row.minimized)
  if(minimized)await this.focus_window(app,minimized.handle)
  await new Promise(r=>setTimeout(r,250))
 }while(Date.now()<end);throw Error('应用主窗口未就绪')}
 focus_window(app,handle){return this.call(app,'focus',{handle})}
 inspect_ui_tree(app,handle){return this.call(app,'inspect',{handle})}
 screenshot_region(app,handle,scale=1){return this.call(app,'ocr',{handle,scale})}
 click_search_header(app,handle){return this.call(app,'click_search_header',{handle,scale:2})}
 click_song_tab(app,handle){return this.call(app,'click_song_tab',{handle,scale:2})}
 submit_exact_search(app,text,handle){return this.call(app,'submit_exact_search',{text,handle,scale:2})}
 set_text(app,selector,text,handle){return this.call(app,'element',{...selector,handle,operation:'set_text',text})}
 click(app,selector,handle,double=false){return this.call(app,'element',{...selector,handle,double})}
 click_text(app,text,handle,double=false,index=0,region,scale=1,exact=false){return this.call(app,'click_text',{text,handle,double,index,region,scale,exact})}
 type_text(app,text,handle){if(String(text).length>500)throw Error('桌面输入超过500字');return this.call(app,'type',{text,handle})}
 hotkey(app,key,handle){return this.call(app,'key',{key,handle})}
 verify_window_state(app,handle){return this.call(app,'state',{handle})}
 media(app,operation='get'){return this.call(app,'media',{operation})}
 media_sessions(operation='get',options={}){return this.call('system-media','media_all',{...options,operation})}
 stop(){const child=this.worker;this.worker=null;child?.kill()}
}
module.exports={ComputerController}
