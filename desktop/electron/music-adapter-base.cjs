const {parseQuery}=require('./music-query.cjs')
const names={netease:'网易云音乐',qqmusic:'QQ音乐'}
class MusicAdapterBase{
 constructor(controller,app){if(!names[app])throw Error('Unknown music provider');this.controller=controller;this.app=app;this.applicationName=names[app];this.window=null;this.query='';this.target=parseQuery('');this.diagnostics=[];this.selected=null;this.resultScale=1;this.context=null}
 bindTask(input={},session){if(input.providerId&&input.providerId!==this.app)throw Error('Music task provider mismatch');this.context={taskId:input.taskId||session,providerId:this.app,applicationId:input.applicationId||this.app,applicationName:this.applicationName,targetSong:input.targetSong||this.target.title,targetArtist:input.targetArtist||this.target.artist,currentStep:input.currentStep||'ready'}}
 result(value){return {...value,app:this.app,provider:this.app,application_id:this.context?.applicationId||this.app,music_context:this.context?{...this.context}:null}}
 async wait_ready(){this.window=await this.controller.wait_ready(this.app);return this.window}
 async focus(){this.window??=await this.wait_ready();return this.controller.focus_window(this.app,this.window.handle)}
 record(strategy,status,detail){const row={strategy,status,detail,music_context:this.context?{...this.context}:null,at:new Date().toISOString()};this.diagnostics.push(row);this.controller.audit?.('music-search.log',strategy,JSON.stringify(row))}
 error(code,detail){const e=Error(detail);e.code=code;e.publicMessage=this.publicError(code);return e}
 publicError(code){const name=this.applicationName;return code==='resource_unavailable'?`${name}显示这首歌目前无法播放，可能需要账号权限或该资源暂不可用。`:code==='song_not_found'?`我还没在${name}里找到这首歌的可播放版本。`:code==='ambiguous_song'?`我在${name}里找到了几个同名版本，告诉我歌手或版本就能继续。`:code==='search_failed'?`我还没在${name}里确认到这首歌的单曲结果。`:`我还没确认这首歌在${name}里开始播放，这次先停下了。`}
}
module.exports={MusicAdapterBase}
