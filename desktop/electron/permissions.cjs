const {dialog}=require('electron')
const backend=require('./backend.cjs')
/** 通过控制接口查询/保存后端权限；网页内容或模型文本不能替代授权结果。 */
async function request(route,body) {
 const response=await fetch(`${backend.address.url}/api/permissions${route}`,{method:body?'POST':'GET',
  headers:{'Content-Type':'application/json','X-Companion-Control-Token':backend.controlToken},body:body?JSON.stringify(body):undefined})
 if(!response.ok)throw Error((await response.json()).detail||'权限设置失败')
 return response.json()
}
/**
 * 检查持久/任务范围的许可，必要时询问用户；高风险操作只提供单次批准。
 * @param {Electron.BrowserWindow} window
 * @param {string} action 工具和目标组成的权限标识。
 * @param {string|undefined} title 对话框显示的操作说明。
 * @param {string|undefined} taskId 任务范围许可归属，不能跨任务复用。
 * @returns {Promise<boolean>} 是否允许当前操作。
 */
async function authorize(window,action,title,taskId) {
 const state=await request(`/check?${new URLSearchParams({action,...(taskId?{task_id:taskId}:{})})}`)
 if(state.allowed)return true
 if(state.decision==='deny')return false
 const high=['high','critical'].includes(state.risk)
 const options=high?['拒绝','仅允许一次']:['拒绝','仅允许一次',...(taskId?['本次任务同类允许']:[])]
 const answer=await dialog.showMessageBox(window,{type:high?'warning':'question',title:'工作权限',message:title,
  detail:high?'此操作可能改变文件或系统状态，每次都需要确认。':'可仅允许一次，或允许当前任务的同类操作。总体策略可在「设置 → 工作模式 → 权限」修改。',buttons:options,defaultId:0,cancelId:0})
 if(answer.response===0)return false
 if(answer.response===2&&taskId)await request('',{action,scope:'task',task_id:taskId})
 return true
}
module.exports={request,authorize}
