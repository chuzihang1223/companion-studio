const assert=require('node:assert/strict'),{NetEaseMusicAdapter,parseQuery}=require('../../desktop/electron/music-adapters.cjs')
const adapter=new NetEaseMusicAdapter({})
adapter.target=parseQuery('周杰伦的歌');adapter.query=adapter.target.query
const rows=adapter.results({width:1600,height:1100,lines:[
 {text:'布 拉 格 广 场',x:500,y:440,width:150,height:24},
 {text:'笋 女 添',x:500,y:469,width:65,height:15},
 {text:'蔡依林 / 周杰伦',x:570,y:471,width:160,height:19},
 {text:'单元剧《班女郎》插曲',x:500,y:500,width:220,height:22},
]})
assert.equal(rows.length,1);assert.equal(rows[0].title,'布 拉 格 广 场');assert.ok(rows[0].artist_match)
// Actual 2x OCR from the installed client: the short red 屋顶 title was not
// recognized, but lettering on the cover image was. That row must be skipped.
const observed=adapter.results({width:1592,height:1128,lines:[
 {text:'笋 女 添',x:469.5,y:498,width:44,height:15},
 {text:'〔 灬 〕 原 周 杰 伦 / 温 岚 / 吴 宗 宪',x:532.5,y:534.5,width:301.5,height:20.5},
 {text:'最 近 听 过 悲 伤 时 都 在 听',x:538,y:575.5,width:147.5,height:13.5},
 {text:'布 拉 格 广 场',x:532.5,y:634.5,width:104.5,height:20.5},
 {text:'〔 〕 @源唱 v 一 蔡 依 林 / 周 杰 伦',x:532.5,y:665,width:310.5,height:21},
 {text:'单 元 剧 《 一 上 班 女 郎 》 主 题 曲',x:532,y:700.5,width:234,height:20.5},
]})
assert.deepEqual(observed.map(r=>r.title),['布 拉 格 广 场']);assert.ok(observed[0].artist_match)
console.log(JSON.stringify({pass:true,quality_badge_excluded:true,cover_text_excluded:true,artist_only_title:true}))
