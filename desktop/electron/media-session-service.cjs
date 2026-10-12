const {NetEaseMusicAdapter,QQMusicAdapter}=require('./music-adapters.cjs')
const names={netease:'网易云音乐',qqmusic:'QQ音乐'}
const browserSource=source=>/chrome|msedge|firefox|opera|brave|vivaldi|browser/i.test(source)
class MediaSessionService {
 constructor({controller,registry,cache,audit=()=>{},adapterFor}){
  this.controller=controller;this.registry=registry;this.cache=cache;this.audit=audit;this.allowBrowserMedia=false;this.adapters=new Map();this.selectedId=null;this.cached=null;this.pending=null;this.stopped=false
  this.adapterFor=adapterFor||((app)=>{if(!this.adapters.has(app))this.adapters.set(app,app==='qqmusic'?new QQMusicAdapter(controller):new NetEaseMusicAdapter(controller,app));return this.adapters.get(app)})
 }
 configure({allowBrowserMedia}={}){if(typeof allowBrowserMedia==='boolean'&&allowBrowserMedia!==this.allowBrowserMedia){this.allowBrowserMedia=allowBrowserMedia;this.cached=null}return {allow_browser_media:this.allowBrowserMedia}}
 async snapshot({selectedId,force=false}={}){
  if(selectedId!==undefined)this.selectedId=String(selectedId||'')||null
  if(this.stopped)return {available:false,sessions:[],current:null,diagnostics:['媒体服务已关闭']}
  if(!force&&this.cached&&Date.now()-this.cached.at<850)return this.select(this.cached.value)
  if(this.pending)return this.select(await this.pending)
  this.pending=this.read().finally(()=>{this.pending=null})
  return this.select(await this.pending)
 }
 select(value){const current=value.sessions.find(s=>s.id===this.selectedId)||value.sessions.find(s=>s.active)||value.sessions.find(s=>s.status==='playing')||value.sessions[0]||null;return {...value,selected_id:current?.id||null,current}}
 async read(){
  const observed_at=new Date().toISOString(),diagnostics=[];let raw
  try{raw=await this.controller.media_sessions('get',{allow_browser:this.allowBrowserMedia})}catch(error){raw={available:false,sessions:[]};diagnostics.push(String(error.message||error))}
  const sessions=(raw.sessions||[]).filter(row=>this.allowBrowserMedia||!browserSource(row.source_app||row.source||'')).map(row=>({...row,app_id:/netease|cloudmusic/i.test(row.source_app||row.source)?'netease':/qqmusic/i.test(row.source_app||row.source)?'qqmusic':null,source_app:row.source_app||row.source,source:'windows_media_session',status:String(row.status||'unknown').toLowerCase(),verified:true,observed_at}))
  for(const app of ['netease','qqmusic']){
   if(sessions.some(s=>s.app_id===app)||!this.registry.detect(app).installed)continue
   try{
    const windows=await this.controller.list_windows(app)
    const target=windows.filter(w=>w.responding&&!w.minimized&&w.bounds[2]>=240&&w.bounds[3]>=160).sort((a,b)=>b.bounds[2]*b.bounds[3]-a.bounds[2]*a.bounds[3])[0]
    if(!target)continue
    const adapter=this.adapterFor(app);adapter.window=target
    const state=await adapter.get_now_playing({passive:true})
    if(!state.title||['网易云音乐','QQ音乐'].includes(state.title))continue
    sessions.push({...state,id:'adapter:'+app,source_app:names[app],app_id:app,thumbnail:state.thumbnail||null,artist:state.artist||'',position_seconds:state.position_seconds??null,duration_seconds:state.duration_seconds??null,active:false,verified:!!state.visual_verified,controls:{play:true,pause:true,next:false,previous:false,seek:false,volume:false},observed_at})
   }catch(error){diagnostics.push(names[app]+': '+String(error.message||error))}
  }
  const value={available:!!raw.available||sessions.length>0,sessions,active_id:sessions.find(s=>s.active)?.id||null,allow_browser_media:this.allowBrowserMedia,observed_at,diagnostics}
  this.cached={at:Date.now(),value};return value
 }
 async control(action,sessionId,value){
  if(!['play','pause','next','previous','seek'].includes(action))throw Error('当前来源不支持该媒体操作')
  const state=await this.snapshot({selectedId:sessionId,force:true}),session=state.current
  if(!session||session.id!==sessionId)throw Error('媒体来源已关闭，请刷新后重试')
  if(!session.controls?.[action])throw Error('此媒体来源未提供'+action+'控制')
  if(session.source==='windows_media_session'){
   if(action==='seek'&&(!Number.isFinite(value)||value<0||value>session.duration_seconds))throw Error('进度超出歌曲范围')
   await this.controller.media_sessions(action,{session_id:session.id,allow_browser:this.allowBrowserMedia,position:value})
  }else{
   const result=await this.adapterFor(session.app_id).control(action)
   if(!result.verified)throw Error('客户端未确认播放状态变化')
  }
  this.audit('music-center.log','media_control',action)
  this.cached=null
  let result=await this.snapshot({selectedId:session.id,force:true})
  if(session.source==='windows_media_session'&&['play','pause'].includes(action)){
   const expected=action==='play'?'playing':'paused',until=Date.now()+2500
   while(result.current?.status!==expected&&Date.now()<until){await new Promise(resolve=>setTimeout(resolve,400));result=await this.snapshot({selectedId:session.id,force:true})}
   if(result.current?.status!==expected)throw Error('系统媒体来源尚未确认播放状态变化，请稍后重试')
  }
  return result
 }
 async diagnose(options={}){const {runMusicDiagnostics}=require('./music-diagnostics.cjs');return runMusicDiagnostics({controller:this.controller,registry:this.registry,cache:this.cache,adapter:this.adapterFor('netease'),...options})}
 stop(){this.stopped=true;this.cached=null}
}
module.exports={MediaSessionService,browserSource}
