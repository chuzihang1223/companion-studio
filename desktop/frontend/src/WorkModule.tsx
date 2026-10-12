import {useState,type ReactNode} from 'react'
import type {WorkSession} from './api'
const bytes=(n:unknown)=>`${(Number(n)/1024**3).toFixed(1)} GB`
export function ModuleCard({id,title,children,className=''}:{id:string;title:string;children:ReactNode;className?:string}){
 const [fold,setFold]=useState(localStorage.getItem('work-fold-'+id)==='true'),[large,setLarge]=useState(false)
 return <section className={`work-module ${className} ${large?'module-maximized':''} ${fold?'module-folded':''}`}><header><h3>{title}</h3><div><button title={fold?'展开':'折叠'} onClick={()=>{setFold(!fold);localStorage.setItem('work-fold-'+id,String(!fold))}}>{fold?'＋':'−'}</button><button title={large?'还原':'放大'} onClick={()=>setLarge(!large)}>{large?'↙':'↗'}</button></div></header>{!fold&&children}</section>
}
export function WorkspaceModule({tab}:{tab:WorkSession['tabs'][number]}){
 const d=tab.data,w=d.window as Record<string,unknown>|undefined
 if(tab.kind==='preview')return <iframe className="work-preview" title="项目预览" sandbox="" srcDoc={String(d.html||'')} style={{width:'100%',minHeight:300,border:0,background:'white'}}/>
 if(tab.kind==='system')return <div className="work-system-grid"><span>处理器 · {String(d.cpu||'—')} · {String(d.cores||'—')} 核</span><span>CPU · {d.cpu_percent===null?'首次采样':String(d.cpu_percent)+'%'}</span><span>可用内存 · {bytes(d.memory_free)} / {bytes(d.memory_total)}</span><span>网络 · {d.network?'已连接':'未连接'}</span><span>磁盘可用 · {bytes((d.disk as Record<string,unknown>)?.free)}</span></div>
 if(tab.kind==='app')return <div className="work-app-observation"><span className="work-kicker">实际窗口观察</span><h2>{String(d.name||d.app||tab.title)}</h2><p>{String(w?.title||d.title||'')}</p><dl><dt>运行状态</dt><dd>{d.verified?'已验证':d.installed===false?'未安装':'已观察'}</dd><dt>进程</dt><dd>{String(w?.pid||d.pid||'—')}</dd><dt>窗口响应</dt><dd>{w?.responding===false?'无响应':'正常'}</dd></dl>{Array.isArray(d.results)&&<p>已读取 {d.results.length} 个歌曲候选</p>}</div>
 if(tab.kind==='media')return <div className="work-media"><span className="work-kicker">当前媒体</span><h2>{String(d.title||d.query||'音乐客户端')}</h2><p>{d.status==='playing'?'▶ 正在播放':d.status==='paused'?'Ⅱ 已暂停':'已读取搜索结果'}</p>{Array.isArray(d.results)&&<ol>{d.results.map((r,i)=><li key={i}>{String((r as Record<string,unknown>).title||'候选曲目')}</li>)}</ol>}<small>{d.verified?'歌名与播放状态已验证':'搜索结果来自客户端'}{d.source==='window_title_and_player_icon'?' · 窗口与播放器控件':''}</small></div>
 if(tab.kind==='code')return <><p className="work-file-path">{String(d.path||tab.title)}</p><pre className="work-diff">{String(d.diff||d.summary||'尚无变更')}</pre></>
 if(tab.kind==='terminal')return <><p className="work-file-path">{String(d.command||tab.title)}</p><pre className="work-terminal">{String(d.output||'等待终端输出…')}</pre><p>退出码 · {String(d.exit_code??'运行中')}</p></>
 return <><p className="work-file-path">{String(d.path||tab.title)}</p>{Array.isArray(d.items)?<ul>{d.items.map((item,i)=><li key={i}>{String(item)}</li>)}</ul>:Array.isArray(d.changes)?<ul>{d.changes.map((item,i)=><li key={i}>{String((item as Record<string,unknown>).path||'已修改文件')}</li>)}</ul>:<pre>{String(d.content||d.summary||'文件已写入项目')}</pre>}</>
}
