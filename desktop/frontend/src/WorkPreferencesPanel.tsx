import {useEffect,useMemo,useState} from 'react'
import PermissionsPanel from './PermissionsPanel'

const sourceNames:Record<string,string>={registry_uninstall:'安装信息',app_paths:'应用路径',start_menu:'开始菜单',apps_folder:'Windows 应用',url_protocol:'协议注册',running:'运行中的应用',custom:'手动添加'}
export default function WorkPreferencesPanel(){
 const [prefs,setPrefs]=useState<WorkPreferences|null>(null),[note,setNote]=useState(''),[saving,setSaving]=useState(false)
 const [discovery,setDiscovery]=useState<ApplicationDiscovery|null>(null),[scanBusy,setScanBusy]=useState(false),[scanNote,setScanNote]=useState(''),[search,setSearch]=useState(''),[includeMissing,setIncludeMissing]=useState(false)
 useEffect(()=>{
  void window.desktopAPI?.workPreferences().then(setPrefs).catch(e=>setNote(String(e)))
  void window.desktopAPI?.applicationDiscovery().then(setDiscovery).catch(e=>setScanNote(String(e)))
 },[])
 async function change(patch:Partial<WorkPreferences>){setSaving(true);try{setPrefs(await window.desktopAPI!.workPreferences(patch));setNote('工作设置已保存')}catch(e){setNote(String(e))}finally{setSaving(false)}}
 async function scan(){setScanBusy(true);setScanNote('正在读取 Windows 应用信息，可继续使用其他功能…');try{const result=await window.desktopAPI!.scanApplications();setDiscovery(result);setScanNote(`扫描完成，发现 ${result.applications.filter(row=>row.installed).length} 个可用应用`)}catch(e){setScanNote(String(e))}finally{setScanBusy(false)}}
 async function add(){try{const app=await window.desktopAPI!.registerKnownApp();if(app){setDiscovery(await window.desktopAPI!.applicationDiscovery());setScanNote(`已添加 ${app.name}`)}}catch(e){setScanNote(String(e))}}
 const matches=useMemo(()=>{const query=search.trim().toLocaleLowerCase();return (discovery?.applications||[]).filter(row=>(includeMissing||row.installed)&&(!query||[row.name,row.executable,row.app_user_model_id].some(text=>text?.toLocaleLowerCase().includes(query)))).sort((a,b)=>a.name.localeCompare(b.name,'zh-CN'))},[discovery,search,includeMissing])
 if(!prefs)return <p className="setting-note">{note||'正在读取工作设置…'}</p>
 return <section className="work-preferences"><h3>工作模式</h3>
  <PermissionsPanel mode={prefs.permission_mode||'smart'} disabled={saving} onChange={mode=>void change({permission_mode:mode})}/>
  <label><input type="checkbox" disabled={saving} checked={prefs.automation_enabled} onChange={e=>void change({automation_enabled:e.target.checked})}/>启用桌面自动操作</label>
  <label><input type="checkbox" disabled={saving} checked={prefs.auto_work_mode} onChange={e=>void change({auto_work_mode:e.target.checked})}/>遇到执行任务时自动进入工作模式</label>
  <label>默认音乐客户端<select disabled={saving} value={prefs.music_player} onChange={e=>void change({music_player:e.target.value as WorkPreferences['music_player']})}><option value="netease">网易云音乐</option><option value="qqmusic">QQ 音乐</option></select></label>
  <label><span>在音乐中心显示浏览器媒体</span><input type="checkbox" disabled={saving} checked={prefs.browser_media_enabled} onChange={e=>void change({browser_media_enabled:e.target.checked})}/></label>
  <p className="setting-note">开启后，音乐中心可以显示并控制浏览器正在播放的音频或视频。</p>
  <label>编码执行器<select disabled={saving} value={prefs.coding_provider} onChange={e=>void change({coding_provider:e.target.value as WorkPreferences['coding_provider']})}><option value="internal">内置编码智能体</option><option value="codex">本机 Codex CLI</option></select></label>
  <p className="setting-note">Codex 使用本机登录账号的额度；内置执行器使用当前模型。</p>{note&&<p role="status">{note}</p>}
  <section aria-label="应用发现"><h4>已发现应用</h4>
   <p className="setting-note">从 Windows 安装信息和开始菜单发现应用。未注册的便携程序可手动添加。</p>
   <div className="grant-choices"><button type="button" disabled={scanBusy} onClick={()=>void scan()}>{scanBusy?'正在扫描…':'重新扫描应用'}</button><button type="button" onClick={()=>void add()}>添加已安装应用</button></div>
   <label>搜索应用<input aria-label="搜索已发现应用" type="search" value={search} onChange={event=>setSearch(event.target.value)} placeholder="名称或安装路径"/></label>
   <label><span>显示已卸载或不可用的应用</span><input type="checkbox" checked={includeMissing} onChange={event=>setIncludeMissing(event.target.checked)}/></label>
   <p className="setting-note">{discovery?.scanned_at?`最近扫描：${new Date(discovery.scanned_at).toLocaleString('zh-CN')}`:'尚未扫描，可点击重新扫描应用'} · {matches.length} 个匹配</p>
   <div style={{maxHeight:280,overflowY:'auto'}} aria-label="已发现应用列表">
    {matches.map(row=><div className="permission-grant" key={row.app_id}><span style={{minWidth:0,overflowWrap:'anywhere'}}>{row.name}<small>{row.installed?'可用':'已卸载或不可用'} · {row.sources.map(source=>sourceNames[source]||source).join('、')}</small><small>{row.executable||row.app_user_model_id||'Windows 注册应用'}</small></span></div>)}
    {!matches.length&&<p className="setting-note">没有匹配的应用。</p>}
   </div>{scanNote&&<p role="status">{scanNote}</p>}
  </section>
 </section>
}
