import {useEffect,useRef,useState,type PointerEvent as ReactPointerEvent} from 'react'
import type {ModuleId} from './WorkspaceModuleRegistry'
export const MODULE_DRAG_THRESHOLD=180
export type DropTarget={module?:ModuleId;edge:string}

export function useModuleDrag(onDrop:(id:ModuleId,target:DropTarget)=>void){
 const [drag,setDrag]=useState<ModuleId|null>(null),[target,setTarget]=useState<DropTarget|null>(null)
 const ghost=useRef<HTMLDivElement>(null),pending=useRef<{id:ModuleId;x:number;y:number;at:number;active:boolean;target:DropTarget|null}|null>(null),frame=useRef(0),suppressed=useRef<ModuleId|null>(null)
 const handlers=useRef<{move:(e:PointerEvent)=>void;up:(e:PointerEvent)=>void}|null>(null)
 function cancel(){if(pending.current?.active)suppressed.current=pending.current.id;pending.current=null;cancelAnimationFrame(frame.current);setDrag(null);setTarget(null)}
 useEffect(()=>{const escape=(e:KeyboardEvent)=>{if(e.key==='Escape')cancel()},move=(e:PointerEvent)=>handlers.current?.move(e),up=(e:PointerEvent)=>handlers.current?.up(e);window.addEventListener('keydown',escape);window.addEventListener('blur',cancel);window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',cancel);return()=>{window.removeEventListener('keydown',escape);window.removeEventListener('blur',cancel);window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',cancel);cancelAnimationFrame(frame.current)}},[])
 function down(e:ReactPointerEvent,id:ModuleId){if(e.button!==0)return;suppressed.current=null;pending.current={id,x:e.clientX,y:e.clientY,at:performance.now(),active:false,target:null};e.currentTarget.setPointerCapture(e.pointerId)}
 function move(e:ReactPointerEvent|PointerEvent){const state=pending.current;if(!state)return
  if(!state.active){if(performance.now()-state.at<MODULE_DRAG_THRESHOLD||Math.hypot(e.clientX-state.x,e.clientY-state.y)<6)return;state.active=true;setDrag(state.id)}
  e.preventDefault();const x=e.clientX,y=e.clientY;cancelAnimationFrame(frame.current);frame.current=requestAnimationFrame(()=>{
   if(!pending.current)return;if(ghost.current)ghost.current.style.transform=`translate(${x+14}px,${y+14}px)`
   const zone=document.elementFromPoint(x,y)?.closest<HTMLElement>('[data-drop-edge]'),next=zone?{module:zone.dataset.dropModule as ModuleId|undefined,edge:zone.dataset.dropEdge||'row'}:null
   if(JSON.stringify(next)!==JSON.stringify(pending.current.target)){pending.current.target=next;setTarget(next)}
   const scroller=document.querySelector('.workbench'),rect=scroller?.getBoundingClientRect();if(scroller&&rect&&y>rect.bottom-45)scroller.scrollTop+=12;else if(scroller&&rect&&y<rect.top+45)scroller.scrollTop-=12
  })
 }
 function up(e:ReactPointerEvent|PointerEvent){const state=pending.current;if(!state)return;if(state.active){e.preventDefault();suppressed.current=state.id;if(state.target)onDrop(state.id,state.target)}pending.current=null;cancelAnimationFrame(frame.current);setDrag(null);setTarget(null)}
 function click(id:ModuleId,action:()=>void){if(suppressed.current===id){suppressed.current=null;return}action()}
 handlers.current={move,up};return {drag,target,ghost,down,move,up,cancel,click}
}
