const path=require('node:path'),fs=require('node:fs')
const {dialog}=require('electron'),paths=require('./paths.cjs'),permissions=require('./permissions.cjs')

/**
 * 注册虚拟代码草稿的固定 IPC。编辑草稿不写项目；保存/应用经路径选择与权限检查。
 * 文件系统最终校验由后端执行器负责，不能把渲染层的相关文件字段当作授权。
 */
module.exports=function register({ipcMain,requireMainWindow,request,getWindow}){
 const route=(session,id)=>{if(typeof session!=='string'||typeof id!=='string'||session.length>100||id.length>100)throw Error('无效草稿');return `/api/work/${encodeURIComponent(session)}/documents/${encodeURIComponent(id)}`}
 ipcMain.handle('virtual-document-get',(event,session,id)=>{requireMainWindow(event);return request(route(session,id))})
 ipcMain.handle('virtual-document-edit',(event,session,id,content)=>{requireMainWindow(event);if(typeof content!=='string'||Buffer.byteLength(content)>100000)throw Error('草稿内容超过 100 KB');return request(route(session,id),{content},'PATCH')})
 ipcMain.handle('virtual-document-discard',(event,session,id)=>{requireMainWindow(event);return request(route(session,id)+'/discard',{})})
 ipcMain.handle('virtual-document-save',async(event,session,id,mode)=>{
  requireMainWindow(event);if(!['save','saveAs','apply'].includes(mode))throw Error('无效保存方式')
  const prefix=route(session,id),document=await request(prefix),work=await request('/api/work/'+encodeURIComponent(session))
  let destination,overwrite=false
  if(mode==='apply'&&document.related_file&&work.workspace_root)destination=path.resolve(work.workspace_root,document.related_file)
  else{
   const choice=await dialog.showSaveDialog(getWindow(),{title:mode==='saveAs'?'草稿另存为':'保存代码到项目',defaultPath:path.join(work.workspace_root||paths.home,path.basename(document.title)),properties:['createDirectory','showOverwriteConfirmation']})
   if(choice.canceled||!choice.filePath)return {cancelled:true}
   destination=path.resolve(choice.filePath);overwrite=fs.existsSync(destination)
  }
  const selected=work.workspace_root&&path.resolve(work.workspace_root),relative=selected&&path.relative(selected,destination)
  const inside=relative&&!relative.startsWith('..'+path.sep)&&relative!=='..'&&!path.isAbsolute(relative)
  const root=inside?selected:path.dirname(destination),file=inside?relative:path.basename(destination)
  if(!await permissions.authorize(getWindow(),'workspace.write:'+root,'保存代码到 '+destination+'？',session))return {cancelled:true}
  return request(prefix+'/apply',{path:file,destination_root:root,overwrite,approved:true})
 })
}
