const {Menu,clipboard}=require('electron')
const clipboardService=require('./clipboard-service.cjs')
async function show(window,type,state,audit) {
 window.focus()
 window.webContents.focus()
 const text=String(state?.selectedText||state?.text||'')
 const editable=type==='input'
 const hasClipboardText=editable?Boolean(await clipboard.readText()):false
 const items=editable?[
  {label:'撤销',role:'undo',enabled:!!state.canUndo},{label:'重做',role:'redo',enabled:!!state.canRedo},
  {type:'separator'},{label:'剪切',role:'cut',enabled:!!state.hasSelection&&!state.readOnly},
  {label:'复制',enabled:!!state.hasSelection,click:()=>void clipboardService.writeText(text).catch(()=>audit('clipboard.log','context_copy_failed'))},{label:'粘贴',role:'paste',enabled:!state.readOnly&&hasClipboardText},
  {type:'separator'},{label:'全选',role:'selectAll',enabled:true},
 ]:[{label:'复制',enabled:!!text,click:()=>void clipboardService.writeText(text).catch(()=>audit('clipboard.log','context_copy_failed'))},{label:'全选',role:'selectAll'}]
 return new Promise(resolve=>{
  let choice=null
  if(type==='assistant'||type==='user') {
   items.push({type:'separator'})
   const actions=type==='assistant'?[['回复','reply'],['重新生成','regenerate'],['播放语音','play'],['新建分支','branch']]:[['编辑','edit'],['回复','reply'],['新建分支','branch']]
   for(const [label,action]of actions)items.push({label,enabled:!state.disabled,click:()=>{choice=action}})
  }
  audit('context-menu.log','open',`${type} selected=${!!state.hasSelection}`)
  const position=Number.isFinite(state?.x)&&Number.isFinite(state?.y)?{x:Math.max(0,Math.round(state.x)),y:Math.max(0,Math.round(state.y))}:{}
  Menu.buildFromTemplate(items).popup({window,...position,callback:()=>{audit('context-menu.log','closed',`${type} action=${choice||'native'}`);resolve(choice)}})
 })
}
module.exports={show}
