export const RESIZE_SCROLL_MARGIN = 80
const MIN_SPEED = 360, MAX_SPEED = 1200

export function resizeScrollVelocity(clientY:number,top:number,bottom:number,edge:string){
 if(!edge.includes('n')&&!edge.includes('s'))return 0
 const band=Math.min(RESIZE_SCROLL_MARGIN,(bottom-top)/3)
 if(clientY<top+band)return -(MIN_SPEED+(MAX_SPEED-MIN_SPEED)*Math.min(1,Math.max(0,(top+band-clientY)/band)))
 if(clientY>bottom-band)return MIN_SPEED+(MAX_SPEED-MIN_SPEED)*Math.min(1,Math.max(0,(clientY-bottom+band)/band))
 return 0
}

/** Read before writes; refresh bounds only when the viewport changes. */
export class WorkspaceAutoScrollEngine {
 top=0;bottom=0;left=0;viewportHeight=0;gridOffset=0;tail=0
 private dirty=true
 constructor(private container:HTMLElement,private grid:HTMLElement,readonly edge:string){this.measure()}
 invalidate(){this.dirty=true}
 measure(){
  if(!this.dirty)return false
  const rect=this.container.getBoundingClientRect(),grid=this.grid.getBoundingClientRect()
  this.top=rect.top;this.bottom=rect.bottom;this.left=rect.left
  this.viewportHeight=this.container.clientHeight
  this.gridOffset=grid.top-rect.top+this.container.scrollTop
  this.tail=parseFloat(getComputedStyle(this.container).paddingBottom)||0
  this.dirty=false;return true
 }
 velocity(clientY:number){return resizeScrollVelocity(clientY,this.top,this.bottom,this.edge)}
 pointerY(clientY:number){return Math.max(this.top+4,Math.min(this.bottom-8,clientY))}
 clampScroll(target:number,canvasHeight:number){return Math.max(0,Math.min(target,Math.max(0,this.gridOffset+canvasHeight+this.tail-this.viewportHeight)))}
}
