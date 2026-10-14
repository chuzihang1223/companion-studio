import type {ChatStreamEvent} from './api'
import type {AudioPlaybackService} from './AudioPlaybackService'
/** Chat/Work 共用消费器；回复、会话及序号共同限定可播放的 PCM 片段。 */
export class VoicePresentationService {
 private id=''
 private session=''
 private sequence=0
 private stopped=false
 constructor(private playback:AudioPlaybackService,private volume:number,private error:(detail:string)=>void,private eligible:()=>boolean=()=>true){}
 begin(id:string,session:string){this.id=id;this.session=session;this.sequence=0}
 async accept(item:ChatStreamEvent):Promise<boolean>{
  const d=item.data
  if(item.event==='assistant_started'){this.begin(String(d.message_id),String(d.session_id||''));return false}
  if(!['audio_pcm','audio_segment_end','audio_end','voice_error','audio_aborted'].includes(item.event))return false
  if(this.stopped)return true
   // 后台仍消费事件供后端保存语音，但不抢占当前回复的输出设备。
  if(!this.eligible())return true
  if(d.session_id&&d.session_id!==this.session||d.message_id&&d.message_id!==this.id)return true
  if(item.event==='audio_pcm'){
   if(typeof d.sequence==='number'){
    if(d.sequence<this.sequence){void window.desktopAPI?.logDiagnostic('audio-chunks.log',{event:'duplicate_ignored',reply_id:this.id,sequence:d.sequence});return true}
    if(d.sequence!==this.sequence)throw Error('语音片段顺序缺失，已停止播放')
    this.sequence++
   }
   await this.playback.feedPcm(this.id,String(d.pcm_base64),Number(d.sample_rate),Number(d.channels),this.volume,String(d.segment_id||this.id),Number(d.initial_buffer_ms)||300)
  }else if(item.event==='audio_segment_end')await this.playback.endPcmSegment(String(d.segment_id))
  else if(item.event==='audio_end')this.playback.finishPcm()
  else if(item.event==='audio_aborted'){this.playback.stop();this.error(String(d.detail))}
  else this.error(String(d.detail))
  return true
 }
 stop(){this.stopped=true;if(this.session)void window.desktopAPI?.cancelVoiceStream(this.session).catch(()=>{})}
 detach(){this.stopped=true}
}
