import {useEffect,useState} from 'react'
export type Presence={state:'online'|'offline'|'degraded';label:string;detail:string}
export class PresenceService{
 async check():Promise<Presence>{
  try{
   const base=await window.desktopAPI?.getBackendUrl()||'http://127.0.0.1:8000'
   const response=await fetch(base+'/api/presence',{signal:AbortSignal.timeout(2500),cache:'no-store'})
   if(!response.ok)throw Error('Backend unavailable')
   const data=await response.json(),problems:string[]=[]
   if(window.desktopAPI)await window.desktopAPI.windowState()
   if(!data.llm?.available)problems.push(data.llm?.configured?'模型最近一次请求失败':'尚未配置聊天模型')
   if(!data.agent)problems.push('工作服务不可用')
   if(!data.voice?.available)problems.push('语音不可用')
   return {state:problems.length?'degraded':'online',label:'在线',detail:problems.length?'部分功能不可用 · '+problems.join(' · '):data.llm?.checked?'聊天、工作与本地连接正常':'本地连接正常 · 模型已配置，连接将在使用时验证'}
  }catch{return {state:'offline',label:'离线',detail:'本地服务连接中断，正在等待恢复'}}
 }
}
export function usePresence(){const [presence,setPresence]=useState<Presence>({state:'offline',label:'连接中',detail:'正在连接本地服务'});useEffect(()=>{let live=true,running=false;const service=new PresenceService();const tick=async()=>{if(running)return;running=true;const state=await service.check();if(live)setPresence(state);running=false};void tick();const timer=setInterval(()=>void tick(),4000);return()=>{live=false;clearInterval(timer)}},[]);return presence}
export function PresenceIndicator({presence}:{presence:Presence}){return <span className={`presence-indicator ${presence.state}`} role="status" title={presence.detail}><i/>{presence.label}</span>}
