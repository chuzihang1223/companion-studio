import {useEffect,useRef,useState} from 'react'
import {addWorkTab,changeWorkTab,type WorkSession} from './api'
type Tab={id:string;recordId?:string;url:string;title:string;state?:BrowserTabState}
export default function WorkspaceBrowser({visible,session,initialUrl,onError}:{visible:boolean;session:WorkSession|null;initialUrl?:string;onError:(message:string)=>void}){
 const [tabs,setTabs]=useState<Tab[]>(()=>session?.tabs.filter(t=>t.kind==='web').map(t=>({id:String(t.data.tabId||t.id),recordId:t.id,url:String(t.data.url),title:t.title}))||[])
 const [active,setActive]=useState<string|null>(null),[address,setAddress]=useState(initialUrl||'https://www.bilibili.com/')
 const canvas=useRef<HTMLDivElement>(null),activeRef=useRef(active),visibleRef=useRef(visible),resizingRef=useRef(false);activeRef.current=active;visibleRef.current=visible
 const bounds=()=>{const r=canvas.current?.getBoundingClientRect(),clip=canvas.current?.closest('.workbench')?.getBoundingClientRect();if(!r)return {x:0,y:0,width:0,height:0};const x=Math.max(0,r.x,clip?.x||0),y=Math.max(0,r.y,clip?.y||0),right=Math.min(innerWidth,r.right,clip?.right||innerWidth),bottom=Math.min(innerHeight,r.bottom,clip?.bottom||innerHeight);return {x,y,width:Math.max(0,right-x),height:Math.max(0,bottom-y)}}
 async function open(url:string,id?:string){
  try{
   const parsed=new URL(url);if(parsed.protocol!=='https:')throw Error('请输入 HTTPS 地址')
   const tabId=id||(session?await addWorkTab(session.id,'web',parsed.hostname,{url:parsed.toString()}):crypto.randomUUID())
   setActive(tabId);activeRef.current=tabId;setAddress(parsed.toString())
   setTabs(rows=>rows.some(t=>t.id===tabId)?rows.map(t=>t.id===tabId?{...t,url:parsed.toString(),state:{id:tabId,url:parsed.toString(),title:t.title,status:'loading'}}:t):[...rows,{id:tabId,url:parsed.toString(),title:parsed.hostname}])
   await new Promise<void>(r=>requestAnimationFrame(()=>r()))
   const rect=bounds(),state=await window.desktopAPI?.openWorkspaceBrowser(parsed.toString(),rect,{tabId,taskId:session?.id,background:!visibleRef.current||rect.width<2||rect.height<2})
   if(!visibleRef.current)await window.desktopAPI?.workspaceBrowserAction('hide')
   if(state)setTabs(rows=>rows.map(t=>t.id===tabId?{...t,state,title:state.title||t.title}:t))
  }catch(e){onError(`打开网页失败：${String(e)}`)}
 }
 async function select(tab:Tab){setActive(tab.id);activeRef.current=tab.id;setAddress(tab.url);const state=await window.desktopAPI?.workspaceBrowserAction('show',tab.id);if(!state?.id)await open(tab.url,tab.id)}
 async function close(id:string){await window.desktopAPI?.workspaceBrowserAction('close',id);if(session)await changeWorkTab(session.id,tabs.find(t=>t.id===id)?.recordId||id);setTabs(rows=>rows.filter(t=>t.id!==id));if(activeRef.current===id){setActive(null);activeRef.current=null}}
 useEffect(()=>{if(!session)return;const incoming=session.tabs.filter(t=>t.kind==='web').map(t=>({id:String(t.data.tabId||t.id),recordId:t.id,url:String(t.data.url||''),title:t.title}));setTabs(rows=>[...rows,...incoming.filter(t=>!rows.some(r=>r.id===t.id))]);if(visible&&!activeRef.current&&incoming.length){const tab=incoming[incoming.length-1];setActive(tab.id);activeRef.current=tab.id;setAddress(tab.url);void window.desktopAPI?.resizeWorkspaceBrowser(bounds());void window.desktopAPI?.workspaceBrowserAction('show',tab.id)}},[session?.tabs.length,visible])
 useEffect(()=>{
  const unsubscribe=window.desktopAPI?.onWorkspaceBrowserState(state=>{
   if(!state.id)return
   if(state.popup_url&&state.id===activeRef.current){void open(state.popup_url);return}
   setTabs(rows=>rows.map(t=>t.id===state.id?{...t,url:state.url||t.url,title:state.title||t.title,state}:t))
   if(state.id===activeRef.current&&state.url)setAddress(state.url)
   if(session&&state.status==='loaded'){const record=session.tabs.find(t=>t.id===state.id||t.data.tabId===state.id);if(record)void changeWorkTab(session.id,record.id,{...record.data,url:state.url,title:state.title,active:state.id===activeRef.current}).catch(()=>{})}
  })
  const resize=()=>{if(resizingRef.current||!visibleRef.current||!activeRef.current)return;const rect=bounds();if(rect.width>1&&rect.height>1){void window.desktopAPI?.resizeWorkspaceBrowser(rect);void window.desktopAPI?.workspaceBrowserAction('show',activeRef.current)}else void window.desktopAPI?.workspaceBrowserAction('hide')}
  const gesture=(event:Event)=>{const detail=(event as CustomEvent<{active:boolean;container:HTMLElement}>).detail;if(detail.container!==canvas.current?.closest('.workbench'))return;resizingRef.current=detail.active;if(detail.active){if(visibleRef.current&&activeRef.current)void window.desktopAPI?.workspaceBrowserAction('hide')}else resize()}
  const observer=new ResizeObserver(resize);if(canvas.current)observer.observe(canvas.current)
  window.addEventListener('resize',resize)
  window.addEventListener('companion-workspace-resize',gesture)
  const scroller=canvas.current?.closest('.workbench');scroller?.addEventListener('scroll',resize,{passive:true})
  return()=>{unsubscribe?.();observer.disconnect();window.removeEventListener('resize',resize);window.removeEventListener('companion-workspace-resize',gesture);scroller?.removeEventListener('scroll',resize);void window.desktopAPI?.workspaceBrowserAction('hide')}
 },[session?.id])
 useEffect(()=>{const frame=requestAnimationFrame(()=>{const rect=bounds();if(!visible||rect.width<2||rect.height<2)void window.desktopAPI?.workspaceBrowserAction('hide');else if(activeRef.current){void window.desktopAPI?.resizeWorkspaceBrowser(rect);void window.desktopAPI?.workspaceBrowserAction('show',activeRef.current)}});return()=>cancelAnimationFrame(frame)},[visible])
 useEffect(()=>{if(initialUrl)void open(initialUrl)},[initialUrl])
 const current=tabs.find(t=>t.id===active),failed=current?.state?.status==='failed'||current?.state?.status==='denied'
 return <div className="workspace-browser"><div className="browser-tabstrip">{tabs.map(t=><div key={t.id} className={active===t.id?'active':''}><button onClick={()=>void select(t)}>{t.title}</button><button aria-label={`关闭${t.title}`} onClick={()=>void close(t.id)}>×</button></div>)}<button onClick={()=>{setActive(null);void window.desktopAPI?.workspaceBrowserAction('hide')}}>＋</button></div>
  <div className="work-browser-toolbar"><button title="后退" onClick={()=>void window.desktopAPI?.workspaceBrowserAction('back',active||undefined)}>←</button><button title="前进" onClick={()=>void window.desktopAPI?.workspaceBrowserAction('forward',active||undefined)}>→</button><button onClick={()=>current&&void open(current.url,current.id)}>刷新</button><input aria-label="网页地址" value={address} onChange={e=>setAddress(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')void open(address,active||undefined)}}/><button onClick={()=>void open(address,active||undefined)}>打开</button></div>
  <div className="work-browser-canvas" ref={canvas}>{failed?<div className="browser-error"><h3>网页未能加载</h3><p>{current.state?.error}</p><button onClick={()=>void open(current.url,current.id)}>重试</button><button onClick={()=>void window.desktopAPI?.workspaceBrowserAction('external',current.id)}>在系统浏览器打开</button><details><summary>错误详情</summary><pre>{JSON.stringify(current.state,null,2)}</pre></details></div>:!active?<p>选择历史标签或输入网址。历史网页不会自动加载。</p>:current?.state?.status==='loading'?<p>正在加载网页…</p>:null}</div>
 </div>
}
