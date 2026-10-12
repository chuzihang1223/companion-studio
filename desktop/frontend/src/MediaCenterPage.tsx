import {useEffect,useState} from 'react'
import {useLocalMusic} from './useLocalMusic'

const time=(value:number|null)=>value===null||!Number.isFinite(value)?'—:—':`${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`
const stateLabel={playing:'正在播放',paused:'已暂停',stopped:'已停止',unknown:'状态暂不可用'}
type Props={expanded:boolean;onOpen:()=>void;onClose:()=>void}

export default function MediaCenterPage({expanded,onOpen}:Props){
 const [snapshot,setSnapshot]=useState<MediaSnapshot|null>(null),[selected,setSelected]=useState(''),[error,setError]=useState('')
 const [clients,setClients]=useState<KnownApp[]>([]),[busy,setBusy]=useState(false),[query,setQuery]=useState('')
 const local=useLocalMusic(()=>{setSelected('local');onOpen()})
 useEffect(()=>{
  if(!expanded)return
  let active=true,pending=false
  const poll=async()=>{if(pending)return;pending=true;try{const value=await window.desktopAPI?.mediaSnapshot(selected==='local'?undefined:selected||undefined);if(active&&value){setSnapshot(value);setError('')}}catch(e){if(active)setError(String(e))}finally{pending=false}}
  void poll();const timer=window.setInterval(()=>void poll(),1000)
  void Promise.all(['netease','qqmusic'].map(id=>window.desktopAPI!.knownAppStatus(id))).then(rows=>{if(active)setClients(rows)}).catch(()=>{})
  return()=>{active=false;window.clearInterval(timer)}
 },[expanded,selected])
 const isLocal=selected==='local',current=isLocal?null:snapshot?.current
 const title=isLocal?local.current?.title||'尚未选择本地歌曲':current?.title||'还没有正在播放的音乐'
 const position=isLocal?local.position:current?.position_seconds??null,duration=isLocal?local.duration:current?.duration_seconds??null
 const playing=isLocal?local.playing:current?.status==='playing'
 async function control(action:'play'|'pause'|'next'|'previous'|'seek'|'volume',value?:number){
  if(!current)return;setBusy(true);setError('')
  try{setSnapshot(await window.desktopAPI!.mediaControl(action,current.id,value))}catch(e){setError(String(e))}finally{setBusy(false)}
 }
 async function openClient(id:string){setBusy(true);setError('');try{const app=await window.desktopAPI!.launchKnownApp(id);if(!app.installed)throw Error('未检测到此音乐客户端，请在工作设置中扫描或手动添加。');setSelected('')}catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="music-page" aria-label="音乐中心" hidden={!expanded}>
  <div className="music-source-row"><label>播放来源 <select aria-label="播放来源" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">当前活跃媒体</option>{snapshot?.sessions.map(row=><option key={row.id} value={row.id}>{row.source_app} · {row.title||'未命名媒体'}</option>)}<option value="local">本地音乐</option></select></label><small>{isLocal?'本地曲库':current?.source_app||'等待媒体会话'}</small></div>
  <div className="music-playing">{current?.thumbnail?<img className="music-cover" src={current.thumbnail} alt="歌曲封面"/>:<div className="music-cover" aria-label="默认音乐封面">♫</div>}<div><small>{isLocal?'本地音乐':current?.source_app||'当前播放'}</small><h2 data-testid="now-playing-title">{title}</h2><p>{isLocal?local.current?.format.toUpperCase()||'选择音乐文件夹后播放':current?.artist||'—'}</p><span role="status">{isLocal?playing?'正在播放':'已暂停':stateLabel[current?.status||'unknown']}</span></div></div>
  <div className="music-progress"><span>{time(position)}</span><input aria-label="播放进度" type="range" min={0} max={Math.max(duration||1,1)} value={Math.min(position||0,Math.max(duration||1,1))} step={1} disabled={isLocal?!local.canSeek:!current?.controls.seek} onChange={e=>isLocal?local.seek(Number(e.target.value)):void control('seek',Number(e.target.value))}/><span>{time(duration)}</span></div>
  <div className="music-controls"><button aria-label="上一首" disabled={busy||(isLocal?!local.tracks.length:!current?.controls.previous)} onClick={()=>isLocal?void local.next(undefined,-1):void control('previous')}>⏮</button><button aria-label={playing?'暂停音乐':'继续播放音乐'} disabled={busy||(isLocal?!local.tracks.length:!(playing?current?.controls.pause:current?.controls.play))} onClick={()=>isLocal?void local.toggle().catch(e=>setError(String(e))):void control(playing?'pause':'play')}>{playing?'Ⅱ':'▶'}</button><button aria-label="下一首" disabled={busy||(isLocal?!local.tracks.length:!current?.controls.next)} onClick={()=>isLocal?void local.next():void control('next')}>⏭</button><label>音量 <input aria-label="音乐音量" type="range" min={0} max={100} value={isLocal?local.volume:70} disabled={!isLocal&&!current?.controls.volume} onChange={e=>isLocal?local.changeVolume(Number(e.target.value)):void control('volume',Number(e.target.value)/100)}/></label></div>
  {!isLocal&&current&&(!current.controls.volume||duration===null)&&<p className="setting-note">{duration===null?'此客户端尚未提供播放时间。 ':''}{!current.controls.volume?'音量可在客户端或 Windows 音量混合器中调整。':''}</p>}
  <div className="music-clients">{(['netease','qqmusic'] as const).map(id=><button key={id} disabled={busy} onClick={()=>void openClient(id)}>{id==='netease'?'网易云音乐':'QQ 音乐'} · {clients.find(a=>a.id===id)?.installed?'打开客户端':'检测客户端'}</button>)}</div>
  <details open={isLocal}><summary>本地音乐 · {local.tracks.length} 首</summary><div className="music-actions"><button onClick={()=>void local.chooseFolder()}>选择音乐文件夹</button></div><p className="setting-note">{local.folder||'选择你电脑上的音乐文件夹'}</p><input className="music-search" aria-label="搜索本地歌曲" value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索本地歌曲"/><div className="music-track-list">{local.tracks.filter(t=>t.title.toLowerCase().includes(query.toLowerCase())).map(track=><button key={track.id} className={local.current?.id===track.id?'selected':''} onClick={()=>void local.play(track)}><span>{track.title}</span><small>{track.format.toUpperCase()}</small></button>)}</div></details>
  {(error||local.error)&&<p className="error" role="alert">{error||local.error}</p>}
 </section>
}
