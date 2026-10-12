const {dialog}=require('electron')
const backend=require('./backend.cjs')
async function request(route,body) {
 const response=await fetch(`${backend.address.url}/api/permissions${route}`,{method:body?'POST':'GET',
  headers:{'Content-Type':'application/json','X-Companion-Control-Token':backend.controlToken},body:body?JSON.stringify(body):undefined})
 if(!response.ok)throw Error((await response.json()).detail||'权限设置失败')
 return response.json()
}
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
