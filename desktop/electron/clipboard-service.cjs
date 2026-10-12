'use strict'
const {clipboard}=require('electron')
async function writeText(text){
 if(typeof text!=='string'||text.length>2_000_000)throw Error('复制文本无效或超过 200 万字')
 for(let attempt=0;attempt<3;attempt++){
  await clipboard.writeText(text)
  if(await clipboard.readText()===text)return true
  await new Promise(resolve=>setTimeout(resolve,50))
 }
 throw Error('系统剪贴板未能写入，请确认 Windows 已解锁后重试')
}
module.exports={writeText,readText:()=>clipboard.readText()}
