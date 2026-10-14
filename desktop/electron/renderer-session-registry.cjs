'use strict'
const {randomUUID}=require('node:crypto')
/**
 * 将主窗口身份与活动会话关联；只能由主进程注册，内嵌网页不会进入此表。
 * 关闭窗口删除登记。会话 ID 是归属标识，不是执行工具或访问文件的授权。
 */
class RendererSessionRegistry{
 constructor(){this.rows=new Map()}
 register(window){const id=window.webContents.id,row={window_id:randomUUID(),renderer_id:randomUUID(),active_conversation_id:null};this.rows.set(id,row);window.once('closed',()=>this.rows.delete(id));return row}
 /** @param {Electron.WebContents} contents @param {string|null|undefined} id */
 context(contents,id){const row=this.rows.get(contents.id);if(!row)throw Error('Unauthorized renderer');if(id!==undefined){if(id!==null&&(typeof id!=='string'||id.length>100))throw Error('会话标识无效');row.active_conversation_id=id}return {...row}}
}
module.exports={RendererSessionRegistry}
