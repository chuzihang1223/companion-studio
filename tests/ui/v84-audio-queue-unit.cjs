// A stale 'stopped' poll must not truncate pending PCM or skip the finish call.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict')
const ts=require('../../desktop/node_modules/typescript')
const source=fs.readFileSync(require('node:path').resolve(__dirname,'../../desktop/frontend/src/BufferedAudioQueue.ts'),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
let ticker,releaseEnd,finishCalls=0,stopCalls=0,ended=0
const endPromise=new Promise(resolve=>releaseEnd=resolve)
const api={logDiagnostic:async()=>{},nativePcmCapability:async()=>({supported:true}),startNativePcm:async()=>({status:'buffering'}),
 feedNativePcm:async body=>{if(body.end_segment)await endPromise;return{}},nativeAudioStatus:async()=>({status:'stopped',position:1}),
 finishNativePcm:async()=>{finishCalls++;return{}},stopNativeAudio:async()=>{stopCalls++;return{}}}
const context={exports:{},crypto:require('node:crypto').webcrypto,performance,atob,btoa,Uint8Array,DataView,ArrayBuffer,clearInterval:()=>{},clearTimeout,
 window:{desktopAPI:api,setInterval:fn=>{ticker=fn;return 1},clearInterval:()=>{},setTimeout,clearTimeout}}
vm.runInNewContext(compiled,context)
const settle=()=>new Promise(resolve=>setImmediate(resolve))
;(async()=>{
 const queue=new context.exports.BufferedAudioQueue('test',1,()=>{},()=>ended++,Promise.resolve())
 await queue.feed(Buffer.from([0,0,1,0]).toString('base64'),44100,1)
 void queue.endSegment('test');queue.finish();await settle();ticker();await settle()
 assert.equal(ended,0);assert.equal(stopCalls,0);assert.equal(finishCalls,0)
 releaseEnd();await settle();await settle();assert.equal(finishCalls,1)
 ticker();await settle();await settle();assert.equal(ended,1);assert.equal(stopCalls,1)
 console.log('Pending PCM drains before completion; stale stop cannot truncate it')
})().catch(error=>{console.error(error);process.exitCode=1})
