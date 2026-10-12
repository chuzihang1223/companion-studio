import {useState} from 'react'
import {ClipboardService} from './ClipboardService'
export default function CopyCode({text}:{text:string}){
 const [label,setLabel]=useState('复制代码')
 return <button type="button" className="code-copy" onClick={()=>void ClipboardService.writeText(text).then(()=>{setLabel('已复制');setTimeout(()=>setLabel('复制代码'),1400)}).catch(()=>setLabel('复制失败'))}>{label}</button>
}
