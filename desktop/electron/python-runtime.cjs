const fs=require('node:fs'),path=require('node:path')

// Coding uses an installed interpreter; the packaged API server is not Python CLI.
module.exports=function agentPython({env,home,backend,packaged}){
 if(env.AGENT_PYTHON)return env.AGENT_PYTHON
 if(!packaged)return backend
 const candidates=[path.join(home,'Source','.venv','Scripts','python.exe'),
  ...String(env.PATH||env.Path||'').split(path.delimiter).filter(Boolean)
   .filter(folder=>!folder.toLowerCase().includes('windowsapps'))
   .map(folder=>path.join(folder.replace(/^"|"$/g,''),'python.exe')),
  path.join(home,'Cache','voice_candidates','index-tts','.venv','Scripts','python.exe')]
 const available=candidates.find(file=>{try{return fs.statSync(file).isFile()}catch{return false}})
 return available||path.join(home,'Runtime','python','python.exe')
}
