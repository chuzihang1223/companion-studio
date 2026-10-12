'use strict'
/** Observe the authorized app's whole process family, including launcher handoff. */
class ProcessLifecycleWatcher{
 constructor({graceMs=1500,onEvent=()=>{}}={}){this.graceMs=graceMs;this.onEvent=onEvent;this.seenProcess=false;this.seenWindow=false;this.missingSince=null}
 observe(state){
  const processes=state.processes||[],windows=(state.windows||[]).filter(row=>row.visible!==false||row.minimized)
  if(processes.length&&!this.seenProcess){this.seenProcess=true;this.onEvent('started')}
  if(windows.length&&!this.seenWindow){this.seenWindow=true;this.onEvent('ready')}
  if(this.seenProcess&&!processes.length||this.seenWindow&&!windows.length){
   this.missingSince??=Date.now()
   if(Date.now()-this.missingSince>=this.graceMs){this.onEvent('exited');throw Error(this.seenWindow?'TARGET_WINDOW_CLOSED':'APP_EXITED_BEFORE_READY')}
  }else this.missingSince=null
 }
}
module.exports={ProcessLifecycleWatcher}
