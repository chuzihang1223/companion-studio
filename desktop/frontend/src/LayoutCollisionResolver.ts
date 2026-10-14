import {moduleIds,type ModuleId} from './WorkspaceModuleRegistry'
import type {ModulePlacement,WorkbenchLayout} from './WorkspaceLayoutEngine'
const GAP=10
const visible=(l:WorkbenchLayout)=>moduleIds.filter(id=>!l.modules[id].closed&&!l.modules[id].minimized)
const intersects=(a:ModulePlacement,b:ModulePlacement,gap=GAP)=>a.x<b.x+b.width+gap-.1&&b.x<a.x+a.width+gap-.1&&a.y<b.y+b.height+gap-.1&&b.y<a.y+a.height+gap-.1
/** 优先在可见纵向区域寻找最近空位，再落到已放置窗格之后。 */
export function findNearestFreeSlot(layout:WorkbenchLayout,id:ModuleId,anchor?:{x:number;y:number;viewportTop?:number;viewportBottom?:number},placed=visible(layout).filter(other=>other!==id)){
 const gap=layout.gap??GAP,p=layout.modules[id],others=placed.map(other=>layout.modules[other]),xs=[0,...others.map(r=>r.x+r.width+gap)],ys=[0,...others.flatMap(r=>[r.y,r.y+r.height+gap])]
 if(anchor){xs.push(anchor.x);ys.push(anchor.y)}
 const slots=ys.flatMap(y=>xs.map(x=>({x,y}))).filter(s=>s.x>=0&&s.x+p.width<=layout.canvasWidth+.1&&!others.some(r=>intersects({...p,...s},r,gap)))
 // 调用方提供视口时先填可见区域；距离及排序权重保持原有约定。
 const top=anchor?.viewportTop||0,bottom=anchor?.viewportBottom||Infinity
 const band=(s:{x:number;y:number})=>s.y>=top&&s.y<bottom?0:1
 const distance=(s:{x:number;y:number})=>anchor?Math.abs(s.y-anchor.y)+Math.abs(s.x-anchor.x)/2:s.y*layout.canvasWidth+s.x
 slots.sort((a,b)=>band(a)-band(b)||distance(a)-distance(b)||a.y-b.y||a.x-b.x)
 return slots[0]||{x:0,y:Math.max(0,...others.map(r=>r.y+r.height+gap))}
}
/** 固定窗格继续作为障碍物；扩大的窗格将冲突邻居放到空闲位置。 */
export function resolveCollisions(input:WorkbenchLayout,priority?:ModuleId){
 const layout=structuredClone(input),ids=visible(layout),placed=ids.filter(id=>layout.modules[id].pinned_position&&id!==priority)
 const rest=ids.filter(id=>!placed.includes(id)).sort((a,b)=>a===priority?-1:b===priority?1:layout.modules[a].y-layout.modules[b].y||layout.modules[a].x-layout.modules[b].x||layout.modules[a].order-layout.modules[b].order)
 for(const id of rest){const p=layout.modules[id];p.width=Math.min(p.width,layout.canvasWidth);p.x=Math.max(0,Math.min(p.x,layout.canvasWidth-p.width));p.height=p.collapsed?40:p.height;if(placed.some(other=>intersects(p,layout.modules[other],layout.gap??GAP)))Object.assign(p,findNearestFreeSlot(layout,id,p,placed));placed.push(id)}
 return layout
}
export function compactLayout(input:WorkbenchLayout,priority?:ModuleId){
 const layout=resolveCollisions(input,priority),ids=visible(layout),placed=ids.filter(id=>layout.modules[id].pinned_position||id===priority)
 for(const id of ids.filter(id=>!placed.includes(id)).sort((a,b)=>layout.modules[a].y-layout.modules[b].y||layout.modules[a].x-layout.modules[b].x)){Object.assign(layout.modules[id],findNearestFreeSlot(layout,id,undefined,placed));placed.push(id)}
 return layout
}
