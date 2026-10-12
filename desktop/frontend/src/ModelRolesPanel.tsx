import { useEffect, useState } from 'react'

const roles = [['character_model','角色回复'],['planning_model','任务规划'],['tool_execution_model','工具执行'],['coding_model','项目编码']]
export default function ModelRolesPanel({presets}:{presets:ApiPreset[]}) {
 const [selection,setSelection]=useState<Record<string,string>>({})
 const [status,setStatus]=useState<ModelRoleStatus>({})
 const [note,setNote]=useState(''),[busy,setBusy]=useState(false)
 useEffect(()=>{void window.desktopAPI?.listApiPresets().then(data=>setSelection((data as unknown as {roles?:Record<string,string>}).roles||{}));void window.desktopAPI?.modelRoleStatus().then(setStatus).catch(()=>{})},[])
 async function save(){if(!window.desktopAPI||busy)return;setBusy(true);try{const result=await window.desktopAPI.setModelRoles(selection);setStatus(result.role_status);setNote('模型职责已保存，下一项任务立即生效。')}catch(error){setNote(String(error))}finally{setBusy(false)}}
 return <section className="model-roles"><h4>模型职责</h4><p className="setting-note">从已保存的 API 预设选择。留空沿用当前角色 API；简单打开应用直接由本机执行。工具任务只选择未被标记为“不支持工具调用”的模型。</p>
 {roles.map(([id,label])=><label key={id}>{label}<select aria-label={label+'模型'} value={selection[id]||''} onChange={event=>setSelection(old=>({...old,[id]:event.target.value}))}><option value="">沿用当前 API</option>{presets.map(preset=><option key={preset.id} value={preset.id}>{preset.name} · {preset.model}</option>)}</select><span className="setting-note">工具调用：{status[id]?.capabilities.tool_calling?.value===true?'支持':status[id]?.capabilities.tool_calling?.value===false?'不支持':'使用时检测'}{status[id]?.capabilities.tool_calling?.source==='observed'?'（已实测）':''}</span></label>)}
 <button disabled={busy} onClick={()=>void save()}>保存模型职责</button><button disabled={busy} onClick={()=>void window.desktopAPI?.modelRoleStatus().then(setStatus)}>刷新能力状态</button>{note&&<p role="status">{note}</p>}</section>
}
