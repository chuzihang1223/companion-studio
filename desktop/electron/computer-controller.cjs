const {spawn}=require('node:child_process')
const readline=require('node:readline'),fs=require('node:fs'),path=require('node:path')
/**
 * @typedef {Object} WindowSnapshot
 * @property {number} handle Win32 窗口句柄，只能用于刚观察到的授权窗口。
 * @property {number} pid
 * @property {string} title
 * @property {number[]} bounds [left, top, width, height]，与桌面截图坐标系一致。
 * @property {boolean} responding
 * @property {boolean} visible
 * @property {boolean} minimized
 *
 * @typedef {Object} AppObservation
 * @property {Array<{pid: number, path: string}>} processes
 * @property {WindowSnapshot[]} windows
 */
/**
 * 本实例拥有的 JSON 行协议 worker；输入/窗口观察共享串行通道。
 * 每次调用先执行任务 guard，再由 worker 验证目标进程和窗口归属。
 * request ID 只用于结果关联，不提供跨任务授权。
 */
class ComputerController {
 constructor(registry,cache,audit=()=>{},guard=async()=>{}){this.registry=registry;this.guard=guard;this.cache=path.join(cache,'desktop-automation');fs.mkdirSync(this.cache,{recursive:true});this.audit=audit;this.pending=new Map();this.sequence=0;this.serial=Promise.resolve();this.worker=null;this.launchSeeds=new Map()}
 /** 懒启动 Windows worker；只读取本安装版解包的自动化脚本。 */
 start(){
  if(this.worker)return
  const root=path.join(__dirname.replace('app.asar','app.asar.unpacked'),'automation')
  this.worker=spawn('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',path.join(root,'windows-worker.ps1')],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,TEMP:this.cache,TMP:this.cache}})
  readline.createInterface({input:this.worker.stdout}).on('line',line=>{let data;try{data=JSON.parse(line)}catch{return}const slot=this.pending.get(data.id);if(!slot)return;this.pending.delete(data.id);clearTimeout(slot.timer);data.error?slot.reject(Error(data.error)):slot.resolve(data.result)})
  this.worker.stderr.on('data',()=>this.audit('computer-control.log','worker_diagnostic','Windows automation worker reported a diagnostic'))
  const child=this.worker
  child.on('exit',()=>{if(this.worker===child)this.worker=null;for(const [id,slot] of this.pending){if(slot.child!==child)continue;clearTimeout(slot.timer);slot.reject(Error('Desktop worker stopped'));this.pending.delete(id)}})
 }
 /**
 * @param {string} app 授权记录中的应用 ID，不能由任意窗口标题替代。
 * @param {string} action worker 支持的操作名。
 * @param {Record<string, unknown>} args 操作参数，目标句柄仍由 worker 验证。
 * @returns {Promise<Record<string, unknown>>} 观察/操作结果；请求失败向上层报告。
 */
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
 /** 仅使用当前应用登记的候选入口；记录父进程创建时间以防 PID 复用误匹配。 */
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
 /** 结束本控制器拥有的 worker，不结束被操作的用户应用。 */
 stop(){const child=this.worker;this.worker=null;child?.kill()}
}
module.exports={ComputerController}
