import {useEffect,useRef,useState,type SetStateAction} from 'react'
import type {ModuleId} from './WorkspaceModuleRegistry'
import {defaultLayout,migrateLayout,openModule,pack,type WorkbenchLayout,type ModulePlacement,type LayoutTemplate} from './WorkspaceLayoutEngine'
export type {WorkbenchLayout,ModulePlacement} from './WorkspaceLayoutEngine'
export {defaultLayout} from './WorkspaceLayoutEngine'

/** 按任务 key 恢复布局；完成的用户操作才产生撤销快照和保存请求。 */
export function useWorkbenchLayout(key:string,fallbackKey?:string){
 const [layout,render]=useState(defaultLayout),[loaded,setLoaded]=useState(false),[restored,setRestored]=useState(false),[saveError,setSaveError]=useState(''),[undoCount,setUndoCount]=useState(0)
 const current=useRef(layout),history=useRef<WorkbenchLayout[]>([]),ready=useRef(false),storeKey=useRef(key)
 useEffect(()=>{let live=true;ready.current=false;storeKey.current=key;setLoaded(false);history.current=[];setUndoCount(0)
  // IPC/网页缓存可能包含 V1/V2；统一以 unknown 进入迁移，不提前视为 V3。
  const read=async(k:string):Promise<unknown>=>window.desktopAPI?window.desktopAPI.workbenchLayout(k):JSON.parse(localStorage.getItem('work-layout-'+k)||'null')
  void (key.startsWith('pending:')?Promise.resolve(null):read(key)).then(async saved=>({exact:!!saved,value:saved||(!key.startsWith('pending:')&&fallbackKey?await read(fallbackKey):null)||await read('@default-template')})).then(saved=>{if(live){const value=migrateLayout(saved.value);current.current=value;history.current=value.history||[];setUndoCount(history.current.length);render(value);setRestored(saved.exact);ready.current=true;setLoaded(true)}}).catch(e=>{if(live){current.current=defaultLayout();render(current.current);setRestored(false);ready.current=true;setLoaded(true);setSaveError(String(e))}})
  return()=>{live=false;ready.current=false}
 },[key,fallbackKey])
 function persist(value:WorkbenchLayout){if(!ready.current)return;const k=storeKey.current;if(window.desktopAPI)void window.desktopAPI.workbenchLayout(k,value).catch(e=>setSaveError('布局保存失败：'+String(e)));else localStorage.setItem('work-layout-'+k,JSON.stringify(value))}
 // 快照不能递归保留自己的撤销历史或完整旧文件，否则保存体积会不断增长。
 function snapshot(value:WorkbenchLayout){const v=structuredClone(value);delete v.history;delete v.legacy;return v}
 function setLayout(value:SetStateAction<WorkbenchLayout>,record=true){const before=current.current,next=typeof value==='function'?value(before):value;if(record){history.current=[...history.current.slice(-9),snapshot(before)];setUndoCount(history.current.length)}next.history=history.current;current.current=next;render(next);persist(next)}
 function change(id:ModuleId,patch:Partial<ModulePlacement>){setLayout(old=>{const value={...old,modules:{...old.modules,[id]:{...old.modules[id],...patch}}};return patch.size||patch.pinned_position!==undefined?value:pack(value)})}
 function restore(id:ModuleId){setLayout(old=>openModule(old,id))}
 function undo(){const previous=history.current.pop();if(previous){previous.history=[...history.current];current.current=previous;render(previous);persist(previous);setUndoCount(history.current.length)}}
 function saveDefault(){const value=snapshot(current.current);if(window.desktopAPI)void window.desktopAPI.workbenchLayout('@default-template',value).catch(e=>setSaveError(String(e)));else localStorage.setItem('work-layout-@default-template',JSON.stringify(value))}
 return {layout,loaded,restored,saveError,setLayout,change,restore,undo,undoCount,saveDefault,reset:()=>setLayout(defaultLayout()),template:(name:LayoutTemplate)=>setLayout(defaultLayout(name))}
}
