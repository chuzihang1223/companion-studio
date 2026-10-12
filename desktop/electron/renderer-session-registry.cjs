'use strict'
const {randomUUID}=require('node:crypto')
/** Only trusted app renderers may register; embedded web pages never enter this map. */
class RendererSessionRegistry{
 constructor(){this.rows=new Map()}
 register(window){const id=window.webContents.id,row={window_id:randomUUID(),renderer_id:randomUUID(),active_conversation_id:null};this.rows.set(id,row);window.once('closed',()=>this.rows.delete(id));return row}
 context(contents,id){const row=this.rows.get(contents.id);if(!row)throw Error('Unauthorized renderer');if(id!==undefined){if(id!==null&&(typeof id!=='string'||id.length>100))throw Error('会话标识无效');row.active_conversation_id=id}return {...row}}
}
module.exports={RendererSessionRegistry}
