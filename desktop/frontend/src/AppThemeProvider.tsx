import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react'

export type BackgroundConfig={mode:'default'|'color'|'gradient'|'image';color:string;gradient_start:string;gradient_end:string;brightness:number;blur:number;mask:number;panel:number;image?:string|null;follow_chat?:boolean}
export type ThemeConfig={theme:'light'|'dark';chat:BackgroundConfig;work:BackgroundConfig;migrated?:boolean}
const bg:BackgroundConfig={mode:'default',color:'#f6f8fa',gradient_start:'#edf5f6',gradient_end:'#e7eff8',brightness:100,blur:0,mask:30,panel:92}
const defaults:ThemeConfig={theme:'light',chat:bg,work:{...bg,follow_chat:true}}
const ThemeContext=createContext<{config:ThemeConfig;update:(patch:Partial<ThemeConfig>)=>Promise<void>;mode:(mode:string)=>void;choose:(target:'chat'|'work')=>Promise<void>;error:string}>({config:defaults,update:async()=>{},mode:()=>{},choose:async()=>{},error:''})
export const useAppTheme=()=>useContext(ThemeContext)
export default function AppThemeProvider({children}:{children:ReactNode}){
 const [config,setConfig]=useState(defaults),[view,setView]=useState('chat'),[error,setError]=useState('')
 const current=useRef(config);current.current=config
 const base=useRef(''),revision=useRef(0)
 async function update(patch:Partial<ThemeConfig>){const version=++revision.current,next={...current.current,...patch};current.current=next;setConfig(next);try{if(window.desktopAPI){const saved=await window.desktopAPI.themeSettings(next);if(version===revision.current){current.current=saved;setConfig(saved)}}else localStorage.setItem('app-theme-v81',JSON.stringify(next));setError('')}catch(e){setError(String(e));throw e}}
 useEffect(()=>{let active=true;void(async()=>{
  base.current=await window.desktopAPI?.getBackendUrl()||''
  let restored=await window.desktopAPI?.themeSettings()
  if(!restored){try{restored=JSON.parse(localStorage.getItem('app-theme-v81')||'null')}catch{}}
  if(restored&&!restored.migrated){
   const oldMode=localStorage.getItem('background-mode') as BackgroundConfig['mode']
   restored={...restored,theme:localStorage.getItem('theme')==='dark'?'dark':'light',chat:{...restored.chat,
    mode:['default','color','gradient','image'].includes(oldMode)?oldMode:'default',color:localStorage.getItem('background-color')||bg.color,
    brightness:Number(localStorage.getItem('background-brightness')||100),blur:Number(localStorage.getItem('background-blur')||0)}}
   restored=await window.desktopAPI?.themeSettings(restored)||restored
  }
  if(active&&restored){current.current=restored;setConfig(restored)}
 })().catch(e=>setError(String(e)));return()=>{active=false}},[])
 useEffect(()=>{
  document.documentElement.dataset.theme=config.theme
  const background=view==='agent'&&!config.work.follow_chat?config.work:config.chat
  const image=background.image?base.current+background.image:''
  const value=background.mode==='color'?`linear-gradient(${background.color},${background.color})`:background.mode==='gradient'?`linear-gradient(135deg,${background.gradient_start},${background.gradient_end})`:background.mode==='image'&&image?`url("${image}")`:'none'
  const style=document.documentElement.style
  style.setProperty('--app-background',value);style.setProperty('--app-background-blur',background.blur+'px')
  style.setProperty('--app-background-brightness',background.brightness+'%');style.setProperty('--app-background-mask',String(background.mask/100))
  style.setProperty('--panel-opacity',background.panel+'%')
 },[config,view])
 async function choose(target:'chat'|'work'){const url=await window.desktopAPI?.selectAppearance(target==='chat'?'background':'work_background');if(!url)return;const saved=await window.desktopAPI!.themeSettings();await update({...saved,[target]:{...saved[target],mode:'image',...(target==='work'?{follow_chat:false}:{})}})}
 return <ThemeContext.Provider value={{config,update,mode:setView,choose,error}}>{children}</ThemeContext.Provider>
}
