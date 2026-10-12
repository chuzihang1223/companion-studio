import {useState,type ReactNode} from 'react'
import Icon,{type IconName} from './Icon'
export type SettingsKey='general'|'appearance'|'chat'|'work'|'permissions'|'api'|'voice'|'voiceInput'|'attachments'|'music'|'data'|'developer'|'about'
const categories:Array<[SettingsKey,string,IconName]>=[['general','通用','settings'],['appearance','外观','eye'],['chat','聊天','conversation'],['work','工作模式','network'],['permissions','权限','check'],['api','API','code'],['voice','语音','wave'],['voiceInput','语音输入','volume'],['attachments','附件','folder'],['music','音乐','music'],['data','数据与缓存','package'],['developer','开发者','activity'],['about','关于','user']]
export default function SettingsPage({sections,onBack}:{sections:Record<SettingsKey,ReactNode>;onBack:()=>void}){
 const [active,setActive]=useState<SettingsKey>((localStorage.getItem('settings-section') as SettingsKey)||'general')
 const current=categories.some(([key])=>key===active)?active:'general'
 return <section className="app-page settings-page" aria-label="设置"><div className="page-heading"><button type="button" className="page-back" onClick={onBack}><Icon name="back"/>返回</button><div><h1>设置</h1><p>让这里更适合你</p></div></div><div className="settings-layout"><nav className="settings-navigation" aria-label="设置分类">{categories.map(([key,title,icon])=><button type="button" key={key} className={current===key?'active':''} aria-current={current===key?'page':undefined} onClick={()=>{setActive(key);localStorage.setItem('settings-section',key)}}><Icon name={icon}/>{title}</button>)}</nav><div className="settings-content" key={current}><h2>{categories.find(([key])=>key===current)?.[1]}</h2>{sections[current]}</div></div></section>
}
