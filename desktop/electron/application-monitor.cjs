const {ProcessLifecycleWatcher}=require('./process-lifecycle-watcher.cjs')
class ApplicationMonitor{
 constructor(controller,registry){this.controller=controller;this.registry=registry;this.watches=new Map();this.active=async()=>false;this.publish=async()=>{}}
 start(session,app){
  if(!session)return
  const key=session+':'+app;if(this.watches.has(key))return
  let busy=false
  const lifecycle=new ProcessLifecycleWatcher({graceMs:1500})
  const timer=setInterval(async()=>{if(busy)return;busy=true;try{
   if(!await this.active(session)){clearInterval(timer);this.watches.delete(key);return}
   const observed=await this.controller.observe_app(app),windows=observed.windows||[]
   try{lifecycle.observe(observed)}catch(error){
    await this.publish(session,'lifecycle','应用已关闭',{app,code:String(error.message),target_exited:true})
    clearInterval(timer);this.watches.delete(key);return
   }
   const window=windows.sort((a,b)=>b.bounds[2]*b.bounds[3]-a.bounds[2]*a.bounds[3])[0]
   await this.publish(session,'app',this.registry.detect(app).name,{app,window,windows,verified:!!window?.responding,observed_at:new Date().toISOString()})
  }catch{}finally{busy=false}},2000)
  this.watches.set(key,timer)
 }
 stop(){for(const timer of this.watches.values())clearInterval(timer);this.watches.clear()}
}
module.exports={ApplicationMonitor}
