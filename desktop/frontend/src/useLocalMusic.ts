import {useEffect,useRef,useState} from 'react'
import {loadMusicLibrary} from './api'

export function useLocalMusic(onSelect:()=>void){
 const [tracks,setTracks]=useState<MusicTrack[]>([]),[folder,setFolder]=useState<string|null>(null)
 const [current,setCurrent]=useState<MusicTrack|null>(null),[playing,setPlaying]=useState(false)
 const [position,setPosition]=useState(0),[duration,setDuration]=useState(0),[volume,setVolume]=useState(70),[error,setError]=useState('')
 const audio=useRef<HTMLAudioElement|null>(null),native=useRef(false),latest=useRef({tracks,volume})
 latest.current={tracks,volume}
 useEffect(()=>{void loadMusicLibrary().then(r=>{setFolder(r.folder);setTracks(r.tracks)}).catch(e=>setError(String(e)));return()=>{audio.current?.pause()}},[])
 useEffect(()=>{const timer=window.setInterval(()=>{if(native.current)void window.desktopAPI?.nativeAudioStatus().then(s=>{setPosition(s.position);setDuration(s.duration);setPlaying(s.status==='playing')}).catch(()=>{})},1000);return()=>clearInterval(timer)},[])
 async function play(track:MusicTrack){
  onSelect();setError('');audio.current?.pause()
  if(native.current)await window.desktopAPI?.stopNativeAudio().catch(()=>{})
  native.current=false
  const url=(await window.desktopAPI?.getBackendUrl()||'http://127.0.0.1:8000')+track.audio_url
  const player=new Audio(url);audio.current=player;player.volume=latest.current.volume/100
  player.ontimeupdate=()=>setPosition(player.currentTime)
  player.ondurationchange=()=>setDuration(Number.isFinite(player.duration)?player.duration:0)
  player.onended=()=>{setPlaying(false);void next(track.id,1)}
  player.onerror=()=>{if(audio.current!==player)return;if(track.format==='wav'&&window.desktopAPI){void window.desktopAPI.playNativeAudio(url,latest.current.volume/100).then(s=>{native.current=true;setDuration(s.duration);setPlaying(true);setError('')}).catch(e=>{setError(String(e));setPlaying(false)})}else{setError('无法解码此音频，请检查文件与输出设备。');setPlaying(false)}}
  setCurrent(track);setPosition(0);setDuration(0)
  try{await player.play();setPlaying(true)}catch(e){if(!player.error){setError(String(e));setPlaying(false)}}
 }
 async function pause(){if(native.current)await window.desktopAPI?.pauseNativeAudio();else audio.current?.pause();setPlaying(false)}
 async function toggle(){if(playing)return pause();if(!current){if(tracks[0])return play(tracks[0]);return}if(native.current)await window.desktopAPI?.resumeNativeAudio();else await audio.current?.play();setPlaying(true)}
 async function next(from=current?.id,delta=1){const rows=latest.current.tracks;if(rows.length)await play(rows[(rows.findIndex(t=>t.id===from)+delta+rows.length)%rows.length])}
 async function chooseFolder(){try{const result=await window.desktopAPI?.selectMusicFolder();if(result){setFolder(result.folder);setTracks(result.tracks);setError('')}}catch(e){setError(String(e))}}
 useEffect(()=>{const handle=(e:Event)=>{const action=(e as CustomEvent).detail;if(action.action==='local_music_pause'){void pause();return}if(action.action==='local_music_search'){const match=latest.current.tracks.find(t=>t.title.toLowerCase().includes((action.query||'').toLowerCase()));if(match)void play(match);else{setError(`本地曲库未找到「${action.query}」`);onSelect()}}};window.addEventListener('companion-music-action',handle);return()=>window.removeEventListener('companion-music-action',handle)})
 return {tracks,folder,current,playing,position,duration,volume,error,play,toggle,next,chooseFolder,
  canSeek:!!current&&!native.current,seek:(value:number)=>{if(audio.current&&!native.current){audio.current.currentTime=value;setPosition(value)}},
  changeVolume:(value:number)=>{setVolume(value);if(audio.current)audio.current.volume=value/100}}
}
