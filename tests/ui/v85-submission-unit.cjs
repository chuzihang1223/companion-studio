// Independent controller test before enabling its application UI entry point.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict')
const ts=require('../../desktop/node_modules/typescript'),root=path.resolve(__dirname,'../../desktop/frontend/src'),cache=new Map()
global.localStorage={getItem:()=>null,setItem:()=>{}};global.sessionStorage={getItem:()=>null,setItem:()=>{}};global.window={desktopAPI:undefined};global.requestAnimationFrame=f=>f()
function load(name){if(cache.has(name))return cache.get(name);const module={exports:{}};cache.set(name,module.exports)
 const code=ts.transpileModule(fs.readFileSync(path.join(root,name+'.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 vm.runInThisContext('(function(require,module,exports){'+code+'\n})',{filename:name})(id=>id==='./api'?{}:id.startsWith('./')?load(id.slice(2)):require(id),module,module.exports);cache.set(name,module.exports);return module.exports}
const {ConversationSubmissionService}=load('ConversationSubmissionService'),{ConversationRuntimeStore}=load('ConversationRuntimeStore')
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
async function run(){
 const store=new ConversationRuntimeStore(),history={},audios=[],shown=[];let active='A',release
 const blocked=new Promise(r=>release=r)
 const sdk={routeAgent:async(text,mode)=>mode==='agent'?'agent':'chat',loadConversation:async id=>history[id]||[],
  planAgent:async(_character,id,text)=>{history[id]=[{id:'u-'+id,role:'user',content:text}];return {plan_id:id,conversation_id:id,work_session_id:'ws-'+id,calls:[]}},
  loadWorkSession:async id=>({id,conversation_id:id.slice(3),status:'completed'}),
  streamMessage:async function*(_character,text,_expression,_voice,id){yield {event:'user_saved',data:{conversation_id:id,message_id:'u-'+text}};yield {event:'text_delta',data:{conversation_id:id,text:'reply:'+text}};yield {event:'completed',data:{conversation_id:id,message_id:'r-'+text,content:'reply:'+text}}},
  streamAgent:async function*(plan){if(plan==='A')await blocked;yield {event:'assistant_started',data:{conversation_id:plan,message_id:'r-'+plan,session_id:'voice-'+plan}};yield {event:'text_delta',data:{conversation_id:plan,text:'work:'+plan}};yield {event:'completed',data:{conversation_id:plan,message_id:'r-'+plan,content:'work:'+plan,work_session_id:'ws-'+plan}};yield {event:'audio_pcm',data:{message_id:'r-'+plan,session_id:'voice-'+plan,sequence:0,pcm_base64:'AAA=',sample_rate:24000,channels:1}};yield {event:'audio_end',data:{session_id:'voice-'+plan}};yield {event:'voice_saved',data:{message_id:'r-'+plan}}},
  stopWorkSession:async()=>{}}
 const service=new ConversationSubmissionService(store,{stop(){},feedPcm:async id=>audios.push(id),finishPcm(){}},{visible:id=>active===id,created(){},messages:(id,rows)=>{if(active===id)shown.push({id,count:rows.length})},work(){},changed(){}},sdk)
 const request=(id,mode,text,voice=false)=>({conversationId:id,character:{id:'assistant'},text,mode,folder:null,attachments:[],voice,expression:'natural',speed:1,volume:.8,prefetch:false,autoWork:false})
 await service.submit(request('A','agent','long',true));await sleep(30);assert.equal(store.get('A').generating,true)
 active='B';service.silence()
 await Promise.all(['one','two','three'].map(text=>service.submit(request('B','chat',text))))
 await sleep(200);assert.equal(store.get('B').messages.filter(r=>r.role==='assistant').length,3);assert.equal(store.get('A').generating,true)
 active='C';await service.submit(request('C','agent','open notepad'));await sleep(70)
 assert.equal(store.get('C').messages.at(-1).content,'work:C');assert.equal(store.get('A').generating,true)
 active='B';service.silence();release();await sleep(2300)
 assert.equal(store.get('A').messages.at(-1).content,'work:A');assert.equal(store.get('B').messages.length,6);assert.equal(store.get('C').messages.length,2)
 assert.deepEqual(audios,[]);assert.equal(store.get('A').voice_state,'ready');assert.equal(active,'B');assert.equal(store.get('A').generating,false)
 console.log(JSON.stringify({chat_b_replies:3,work_c_before_a_finished:true,owners_separate:true,background_audio_feeds:audios.length,background_voice_cached:true}))
}
run().catch(error=>{console.error(error);process.exitCode=1})
