const {connect,sleep}=require('./cdp-client.cjs')
async function connectMain(port,timeout=20000){
 let page
 for(let i=0;i<80;i++){try{page=(await(await fetch(`http://127.0.0.1:${port}/json`,{signal:AbortSignal.timeout(1500)})).json())[0];if(page)break}catch{}await sleep(250)}
 if(!page)throw Error('Main inspector unavailable')
 const socket=new WebSocket(page.webSocketDebuggerUrl),pending=new Map();let id=0
 await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve);socket.addEventListener('error',reject)})
 socket.addEventListener('message',({data})=>{const row=JSON.parse(data),slot=pending.get(row.id);if(slot){pending.delete(row.id);slot(row)}})
 return {close:()=>socket.close(),evaluate:async expression=>{
  const call=++id;let timer
  const row=await new Promise((resolve,reject)=>{timer=setTimeout(()=>{pending.delete(call);reject(Error('Main evaluation timeout'))},timeout);pending.set(call,resolve);socket.send(JSON.stringify({id:call,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true}}))}).finally(()=>clearTimeout(timer))
  if(row.error||row.result?.exceptionDetails)throw Error(row.error?.message||row.result.exceptionDetails.exception?.description||row.result.exceptionDetails.text)
  return row.result?.result?.value
 }}
}
module.exports={connect,connectMain,sleep}
