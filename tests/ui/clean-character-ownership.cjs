const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict')
const ts=require('../../desktop/node_modules/typescript'),root=path.resolve(__dirname,'../../desktop/frontend/src'),cache=new Map()
global.localStorage={getItem:()=>null,setItem:()=>{}};global.sessionStorage={getItem:()=>null,setItem:()=>{}};global.window={desktopAPI:undefined};global.requestAnimationFrame=f=>f()
function load(name){if(cache.has(name))return cache.get(name);const module={exports:{}};cache.set(name,module.exports)
 const code=ts.transpileModule(fs.readFileSync(path.join(root,name+'.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 vm.runInThisContext('(function(require,module,exports){'+code+'\n})',{filename:name})(id=>id==='./api'?{}:id.startsWith('./')?load(id.slice(2)):require(id),module,module.exports);cache.set(name,module.exports);return module.exports}
const {ConversationSubmissionService}=load('ConversationSubmissionService'),{ConversationRuntimeStore}=load('ConversationRuntimeStore')
async function run(){
 const store=new ConversationRuntimeStore(),created=[],owners=[];let release
 const blocked=new Promise(resolve=>release=resolve)
 const sdk={routeAgent:async()=> 'chat',createConversation:async(character,mode)=>{created.push({character,mode});await blocked;return{id:'conversation-'+character,character_id:character}},loadConversation:async()=>[],
  streamMessage:async function*(character,text,_expression,_voice,id){owners.push({character,id});yield{event:'text_delta',data:{text:'reply '+text,conversation_id:id}};yield{event:'completed',data:{message_id:'reply-'+text,content:'reply '+text,conversation_id:id}}}}
 const hooks={visible:()=>false,created:(origin,id,mode,character)=>owners.push({created:true,origin,id,mode,character}),messages(){},work(){},changed(){}}
 const service=new ConversationSubmissionService(store,{stop(){}},hooks,sdk)
 const request=(character,text)=>({conversationId:null,character:{id:character},text,mode:'chat',folder:null,attachments:[],voice:false,expression:'natural',speed:1,volume:1,prefetch:false,autoWork:false})
 const pending=[service.submit(request('assistant','one')),service.submit(request('custom_test','two')),service.submit(request('assistant','three'))]
 await new Promise(resolve=>setTimeout(resolve,30))
 assert.deepEqual(created.map(row=>row.character).sort(),['assistant','custom_test'])
 release();await Promise.all(pending);await new Promise(resolve=>setTimeout(resolve,200))
 assert.equal(store.get('conversation-assistant').messages.length,4)
 assert.equal(store.get('conversation-custom_test').messages.length,2)
 for(const id of ['assistant','custom_test'])assert.ok(store.get('conversation-'+id).messages.every(row=>row.character_id===id))
 assert.ok(owners.every(row=>row.id==='conversation-'+row.character))
 console.log(JSON.stringify({pending_creations_separated_by_character:true,same_character_coalesces:true,messages_owned_by_character:true,external_requests:0}))
}
run().catch(error=>{console.error(error);process.exitCode=1})
