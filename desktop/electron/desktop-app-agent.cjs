const fs=require('node:fs'),os=require('node:os'),path=require('node:path')
const {ComputerController}=require('./computer-controller.cjs')
const {NetEaseMusicAdapter,QQMusicAdapter}=require('./music-adapters.cjs')
const {AsyncLocalStorage}=require('node:async_hooks')
const {ApplicationMonitor}=require('./application-monitor.cjs')
const launchLabels={launching:'正在启动应用…',launch_submitted:'启动请求已交给 Windows',process_detected:'已检测到应用进程',window_detected:'已找到窗口，正在恢复',window_ready:'窗口已就绪',existing_window_observed:'已找到正在运行的应用',windows_shell_launch:'正在使用 Windows 启动入口',candidate_failed:'正在尝试备用启动入口',launch_failed:'应用启动未通过验证',focus_unavailable:'窗口已找到，未能切换到前台'}
class DesktopAppAgent {
 constructor(registry,paths,audit,browser){this.registry=registry;this.paths=paths;this.audit=audit;this.browser=browser;this.scope=new AsyncLocalStorage();this.guard=async()=>{};this.controller=new ComputerController(registry,paths.cache,audit,action=>this.guard(this.scope.getStore(),action));registry.setController(this.controller,audit);this.monitor=new ApplicationMonitor(this.controller,registry);this.adapters=new Map();this.cpuSample=null}
 adapter(app,session){const key=app+':'+session;if(!this.adapters.has(key))this.adapters.set(key,app==='qqmusic'?new QQMusicAdapter(this.controller):new NetEaseMusicAdapter(this.controller,app));return this.adapters.get(key)}
 async execute(operation,args,session){return this.scope.run(session,async()=>{
  await this.guard(session)
  const app=args.app||args.provider,contextual=['qqmusic','netease'].includes(app)&&(args.music_context||/^(music|media)\./.test(operation)),adapter=contextual?this.adapter(app,session):null
  if(adapter)adapter.bindTask({...args.music_context,applicationId:this.registry.detect(app).id||args.music_context?.applicationId,currentStep:operation},session)
  try{const result=await this.perform(operation,args,session);return adapter?adapter.result(result):result}
  catch(error){if(!adapter||/user_interruption|desktop_locked|automation_paused|task_cancelled/.test(String(error))){this.audit('app-automation.log','guard_pause',JSON.stringify({operation,app,session,reason:String(error.message||error)}));throw error}adapter.record('operation','failed',String(error.message||error));return adapter.result({verified:false,error:String(error.message||error),error_code:error.code||'music_operation_failed',public_error:error.publicMessage||adapter.publicError(error.code)})}
 })}
 async perform(operation,args,session){
  const app=args.app||args.provider
  this.audit('app-automation.log',operation,app||'workspace')
  if(operation==='system.status')return this.systemStatus()
  if(operation==='applications.scan'){const result=await this.registry.discovery.scan();return {verified:true,count:result.applications.length}}
  if(operation==='applications.resolve')return this.registry.resolver.resolve(args.target)
  if(operation.startsWith('browser.'))return this.browser(operation,args,session)
  if(['app.detect','app.launch'].includes(operation)){
   const current=this.registry.detect(app)
   if(!current.installed){await this.registry.resolver.resolve(current.name);if(!this.registry.detect(app).installed)throw Error('没有检测到该应用，请选择快捷方式或 EXE。')}
  }
  if(!this.registry.ids.includes(app))throw Error('未知应用 ID')
  if(operation==='app.detect')return this.registry.detect(app)
  if(operation==='app.launch')this.monitor.start(session,app)
  if(operation==='app.launch'){const state=await this.registry.launch(app,{taskId:session,targetText:args.target_text,checkpoint:()=>this.guard(session),onProgress:async(stage,observed)=>{if(session&&this.monitor.publish)await this.monitor.publish(session,'app_launch_stage',launchLabels[stage]||'正在检查启动结果',{app,stage,process_count:observed.processes?.length,window_count:observed.windows?.length})}});if(!state.installed)throw Error(`${state.name}未安装`);if(!state.verified)throw Error(state.error||'应用启动未验证');this.monitor.start(session,app);return {...state,action:'desktop_completed'}}
  if(operation==='app.status'){return {...await this.registry.status(app),verified:true,action:'desktop_completed'}}
  if(operation==='app.focus'){const window=await this.controller.wait_ready(app);return {...await this.controller.focus_window(app,window.handle),verified:true,action:'desktop_completed'}}
  if(operation==='app.close'){return {...await this.registry.close(app),action:'desktop_completed'}}
  const adapter=this.adapter(app,session)
  if(operation==='music.ready'){await adapter.wait_ready();return {window:await adapter.focus(),verified:true}}
  if(operation==='music.search')return adapter.search(args.query)
  if(operation==='music.select_play')return adapter.play_song(args.query,args.index)
  if(operation==='music.verify')return adapter.verify_playing(args.query)
  if(operation==='media.current'){await adapter.wait_ready();return adapter.get_now_playing()}
  if(operation==='media.sessions')return this.controller.media(app)
  if(operation==='media.play')return adapter.resume()
  if(operation==='media.pause')return adapter.pause()
  if(operation==='media.next')return adapter.next()
  if(operation==='media.previous')return adapter.previous()
  throw Error('不支持此桌面操作')
 }
 async systemStatus(){
  const sampleCpu=()=>os.cpus().reduce((r,c)=>({idle:r.idle+c.times.idle,total:r.total+Object.values(c.times).reduce((a,b)=>a+b,0)}),{idle:0,total:0})
  const cpus=os.cpus(),old=this.cpuSample||sampleCpu()
  if(!this.cpuSample)await new Promise(resolve=>setTimeout(resolve,150))
  const sample=sampleCpu();this.cpuSample=sample
  let disk=null;try{const d=fs.statfsSync(this.paths.home);disk={total:d.bsize*d.blocks,free:d.bsize*d.bavail}}catch{}
  return {cpu:cpus[0]?.model||'未知',cores:cpus.length,cpu_percent:sample.total>old.total?Math.max(0,Math.min(100,Math.round((1-(sample.idle-old.idle)/(sample.total-old.total))*100))):null,memory_total:os.totalmem(),memory_free:os.freemem(),disk,network:Object.values(os.networkInterfaces()).some(entries=>entries?.some(e=>!e.internal)),verified:true}
 }
 stop(){this.monitor.stop();this.controller.stop()}
}
class DesktopCommandPump {
 constructor(url,token,agent){this.url=url;this.headers={'Content-Type':'application/json','X-Companion-Control-Token':token};this.agent=agent;this.stopped=false;agent.guard=async(session,action)=>{if(!session)return;const s=await this.state(session);if(['cancelled','stopped','failed'].includes(s.status))throw Error('task_cancelled');if((['paused','waiting_user'].includes(s.status)&&!['list','state','observe'].includes(action))||!s.automation_enabled)throw Error('automation_paused')};agent.monitor.active=async session=>!['done','completed','cancelled','stopped','failed'].includes((await this.state(session)).status);agent.monitor.publish=async(session_id,kind,title,data)=>{await fetch(this.url+'/api/desktop/observation',{method:'POST',headers:this.headers,body:JSON.stringify({session_id,kind,title,data}),signal:AbortSignal.timeout(4000)})}}
 async state(session){const r=await fetch(this.url+'/api/desktop/state/'+encodeURIComponent(session),{headers:this.headers,signal:AbortSignal.timeout(4000)});if(!r.ok)throw Error('任务状态无法读取');return r.json()}
 async handle(command){
  let result={},error=null
  const heartbeat=setInterval(()=>{void this.agent.monitor.publish(command.session_id,'heartbeat','桌面操作仍在执行',{operation:command.operation}).catch(()=>{})},3000)
  try{
   const execute=()=>{if(this.stopped)throw Error('task_cancelled');return this.agent.execute(command.operation,command.arguments,command.session_id)}
   if(/^(system\.|applications\.|app\.(detect|status)$)/.test(command.operation))result=await execute()
   else{const pending=this.inputQueue.then(execute,execute);this.inputQueue=pending.catch(()=>{});result=await pending}
  }catch(e){error=String(e.message||e)}finally{clearInterval(heartbeat)}
  await fetch(this.url+'/api/desktop/result',{method:'POST',headers:this.headers,body:JSON.stringify({id:command.id,result,error}),signal:AbortSignal.timeout(4000)})
 }
 async start(){this.inflight=new Set();this.inputQueue=Promise.resolve();while(!this.stopped){try{
  if(this.inflight.size>=4){await Promise.race(this.inflight);continue}
  const r=await fetch(this.url+'/api/desktop/commands',{headers:this.headers,signal:AbortSignal.timeout(4000)});if(!r.ok)throw Error('Desktop bridge unavailable')
  for(const command of await r.json()){
   if(this.stopped)break
   if(this.inflight.size>=4)await Promise.race(this.inflight)
   const task=this.handle(command).catch(()=>{}).finally(()=>this.inflight.delete(task));this.inflight.add(task)
  }
 }catch{if(!this.stopped)await new Promise(r=>setTimeout(r,750))}}}
 stop(){this.stopped=true;this.agent.stop()}
}
module.exports={DesktopAppAgent,DesktopCommandPump}
