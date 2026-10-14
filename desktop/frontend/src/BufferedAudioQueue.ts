/** 一个回复拥有连续输出；后续片段可在已有片段播放时继续入队。 */
export type AudioQueueState='pending'|'generating'|'ready'|'playing'|'done'|'failed'
type Observer=(event:string,detail?:string)=>void
const log=(event:string,segment_id:string,values:Record<string,unknown>={})=>void window.desktopAPI?.logDiagnostic('voice-continuity.log',{event,segment_id,monotonic_ms:performance.now(),...values}).catch(()=>{})
function wavPcm(raw:ArrayBuffer){
 const view=new DataView(raw),tag=(at:number)=>String.fromCharCode(...new Uint8Array(raw,at,4))
 if(raw.byteLength<44||tag(0)!=='RIFF'||tag(8)!=='WAVE')throw Error('无效 WAV 音频')
 let rate=0,channels=0,bits=0,pcm:Uint8Array|null=null
 for(let at=12;at+8<=raw.byteLength;){const size=view.getUint32(at+4,true),kind=tag(at),start=at+8;if(start+size>raw.byteLength)throw Error('WAV 数据被截断');if(kind==='fmt '){if(size<16||view.getUint16(start,true)!==1)throw Error('仅支持 PCM WAV');channels=view.getUint16(start+2,true);rate=view.getUint32(start+4,true);bits=view.getUint16(start+14,true)}if(kind==='data')pcm=new Uint8Array(raw,start,size);at=start+size+(size%2)}
 if(!pcm?.length||bits!==16||![1,2].includes(channels)||rate<8000||rate>96000)throw Error('不支持的 PCM 格式')
 return {pcm,rate,channels}
}
const base64=(bytes:Uint8Array)=>{let value='';for(let i=0;i<bytes.length;i+=16384)value+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(value)}
export class BufferedAudioQueue{
 readonly states=new Map<string,AudioQueueState>()
 readonly done:Promise<void>
 finished=false
 private finishing=false
 paused=false
 private resolveDone!:()=>void
 private stopped=false
  // 设备初始化、PCM 入队、分段结束与最终结束按同一链执行，禁止并行重排。
  private chain:Promise<unknown>=Promise.resolve()
 private native=false
 private initialized:Promise<void>|null=null
 private context:AudioContext|null=null
 private gain:GainNode|null=null
 private end=0
 private ticker:number|null=null
 private endTimer:number|null=null
 private started=false
 private rate=0
 private channels=0
 private session=crypto.randomUUID()
 private sequence=0
 private prebuffer:AudioBuffer[]=[]
 private prebufferSeconds=0
 private segmentStarts=new Map<string,number>()
 constructor(private id:string,private volume:number,private observer:Observer,private ended:()=>void,private previousStop:Promise<unknown>,private initialBufferMs=300){this.done=new Promise(resolve=>this.resolveDone=resolve)}
  /** 等前一回复的设备释放后初始化；原生 PCM 不可用时回退浏览器音频。 */
  private initialize(rate:number,channels:number){
  if(!this.initialized)this.initialized=(async()=>{await this.previousStop;if(this.stopped)return;this.rate=rate;this.channels=channels;this.native=!!(await window.desktopAPI?.nativePcmCapability())?.supported;if(this.stopped)return;
   if(this.native){await window.desktopAPI!.startNativePcm({message_id:this.id,session_id:this.session,sample_rate:rate,channels,volume:this.volume,initial_buffer_ms:Math.max(200,Math.min(1200,this.initialBufferMs))});if(this.stopped){await window.desktopAPI!.stopNativeAudio();return}let polling=false;this.ticker=window.setInterval(()=>{if(polling||this.stopped)return;polling=true;void window.desktopAPI!.nativeAudioStatus().then(status=>{if(this.stopped)return;if(!this.started&&status.position>.01){this.started=true;this.observer('native-pcm-started')}if(this.finished&&status.status==='stopped')this.complete()}).catch(e=>this.fail(e)).finally(()=>polling=false)},40)}
   else{this.context=new AudioContext({sampleRate:rate});this.gain=this.context.createGain();this.gain.gain.value=this.volume;this.gain.connect(this.context.destination);await this.context.resume()}
  })();return this.initialized
 }
 feed(pcmBase64:string,rate:number,channels:number,segment=this.id){
  this.states.set(segment,'ready');log('buffer_ready',segment)
  this.chain=this.chain.then(async()=>{if(this.stopped)return;await this.initialize(rate,channels);if(this.stopped)return;if(rate!==this.rate||channels!==this.channels)throw Error('音频流格式发生变化');const bytes=Uint8Array.from(atob(pcmBase64),c=>c.charCodeAt(0));if(!bytes.length)return;
   if(this.native){await window.desktopAPI!.feedNativePcm({pcm_base64:pcmBase64,segment_id:segment,session_id:this.session,sequence:this.sequence++});this.states.set(segment,'playing')}
   else{const ctx=this.context!;if(ctx.state!=='running')throw Error('音频设备不可用');const frames=bytes.length/(channels*2);if(!Number.isInteger(frames))throw Error('PCM 对齐错误');const values=new DataView(bytes.buffer),buffer=ctx.createBuffer(channels,frames,rate);for(let c=0;c<channels;c++){const output=buffer.getChannelData(c);for(let i=0;i<frames;i++)output[i]=values.getInt16((i*channels+c)*2,true)/32768}if(!this.end){this.prebuffer.push(buffer);this.prebufferSeconds+=buffer.duration;if(this.prebufferSeconds>=(this.initialBufferMs>300?this.initialBufferMs/1000:.24)){for(const prepared of this.prebuffer)this.schedule(prepared,segment);this.prebuffer=[]}}else this.schedule(buffer,segment)}
  }).catch(e=>{this.fail(e);throw e});return this.chain
 }
 private schedule(buffer:AudioBuffer,segment:string){
  const ctx=this.context!,source=ctx.createBufferSource();source.buffer=buffer;source.connect(this.gain!)
  const at=Math.max(ctx.currentTime+.025,this.end)
  if(this.started&&this.end&&at-this.end>.05)log('audio_buffer_underrun_end',segment,{gap_ms:(at-this.end)*1000})
  source.start(at);this.end=at+buffer.duration
  if(!this.segmentStarts.has(segment)){
   this.segmentStarts.set(segment,at);window.setTimeout(()=>{if(!this.stopped){this.states.set(segment,'playing');log('play_start',segment,{audio_clock_seconds:at});if(!this.started){this.started=true;this.observer('pcm-started')}}},Math.max(0,(at-ctx.currentTime)*1000))
  }
 }
 async enqueueWav(url:string,segment:string){this.states.set(segment,'generating');log('decode_start',segment);const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw Error(`音频接口 HTTP ${response.status}`);const {pcm,rate,channels}=wavPcm(await response.arrayBuffer());for(let i=0;i<pcm.length;i+=131072)await this.feed(base64(pcm.subarray(i,i+131072)),rate,channels,segment);await this.endSegment(segment)}
 endSegment(segment:string){this.chain=this.chain.then(async()=>{if(this.stopped)return;if(this.native)await window.desktopAPI!.feedNativePcm({pcm_base64:'',segment_id:segment,end_segment:true,session_id:this.session,sequence:this.sequence++});else{const at=this.end,start=this.segmentStarts.get(segment)||at;log('segment_duration',segment,{segment_duration:at-start});window.setTimeout(()=>{if(!this.stopped){this.states.set(segment,'done');log('play_end',segment,{audio_clock_seconds:at})}},Math.max(0,(at-(this.context?.currentTime||0))*1000))}});return this.chain}
 finish(){if(this.finishing)return;this.finishing=true;this.chain=this.chain.then(async()=>{if(this.stopped)return;if(this.native){await window.desktopAPI!.finishNativePcm();this.finished=true}else if(this.context){this.finished=true;if(this.prebuffer.length){for(const prepared of this.prebuffer)this.schedule(prepared,this.id);this.prebuffer=[]}this.endTimer=window.setTimeout(()=>this.complete(),Math.max(0,(this.end-this.context.currentTime)*1000)+30)}else{this.finished=true;this.complete()}}).catch(e=>this.fail(e))}
 pause(){this.paused=true;if(this.endTimer!==null)clearTimeout(this.endTimer);if(this.native)void window.desktopAPI?.pauseNativeAudio();else void this.context?.suspend()}
 async resume(){this.paused=false;if(this.native)await window.desktopAPI?.resumeNativeAudio();else{await this.context?.resume();if(this.finished&&this.context)this.endTimer=window.setTimeout(()=>this.complete(),Math.max(0,(this.end-this.context.currentTime)*1000)+30)}}
 setVolume(value:number){this.volume=value;if(this.gain)this.gain.gain.value=value}
 private complete(){if(this.stopped)return;for(const id of this.states.keys())this.states.set(id,'done');const event=this.native?'native-pcm-ended':'pcm-ended';log(event,this.id,{segments:this.states.size});this.observer(event);void this.stop().then(()=>this.ended())}
 private fail(error:unknown){if(this.stopped)return;this.observer('pcm-failed',String(error));for(const id of this.states.keys())if(this.states.get(id)!=='done')this.states.set(id,'failed');void this.stop()}
 async stop(){if(this.stopped)return;this.stopped=true;if(this.ticker!==null)clearInterval(this.ticker);if(this.endTimer!==null)clearTimeout(this.endTimer);await this.initialized?.catch(()=>{});await this.context?.close().catch(()=>{});if(this.native)await window.desktopAPI?.stopNativeAudio().catch(()=>{});this.resolveDone()}
}
