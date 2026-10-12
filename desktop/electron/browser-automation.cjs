'use strict'
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))
const SITES={
 bilibili:{home:'https://www.bilibili.com/',search:q=>'https://search.bilibili.com/all?keyword='+encodeURIComponent(q)},
 baidu:{home:'https://www.baidu.com/',search:q=>'https://www.baidu.com/s?wd='+encodeURIComponent(q)},
 github:{home:'https://github.com/',search:q=>'https://github.com/search?q='+encodeURIComponent(q)+'&type=repositories'}
}
async function evaluateDOM(wc,expression){
 // CDP can inspect DOMContentLoaded without waiting for ads/subresources to finish.
 if(!wc.debugger.isAttached())wc.debugger.attach('1.3')
 let timer
 try{const reply=await Promise.race([wc.debugger.sendCommand('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:false}),new Promise((_r,reject)=>{timer=setTimeout(()=>reject(Error('网页 DOM 操作超时')),3000)})]);if(reply.exceptionDetails)throw Error(reply.exceptionDetails.text);return reply.result?.value}
 finally{clearTimeout(timer)}
}
async function domReady(wc,timeout=20000,expected){
 const end=Date.now()+timeout
 do{try{const state=await evaluateDOM(wc,'({ready:document.readyState,url:location.href,body:!!document.body})'),actual=new URL(state.url),wanted=expected&&new URL(expected);if(state.body&&state.ready!=='loading'&&actual.protocol==='https:'&&(!wanted||actual.hostname.replace(/^www\./,'')===wanted.hostname.replace(/^www\./,'')))return state}catch{}await sleep(120)}while(Date.now()<end)
 throw Error('网页 DOM 未就绪，请检查网络或代理。')
}
async function fillSearch(wc,query){
 return evaluateDOM(wc,`(()=>{const visible=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;
 const candidates=[...document.querySelectorAll('input:not([type=password]):not([type=hidden]),[role=searchbox]')].filter(visible);
 const score=e=>(e.type==='search'||e.getAttribute('role')==='searchbox'?10:0)+(/搜索|search|keyword|query/i.test([e.placeholder,e.name,e.id,e.getAttribute('aria-label')].join(' '))?8:0)+(e.closest('form[role=search]')?4:0);
 const input=candidates.sort((a,b)=>score(b)-score(a)).find(e=>score(e)>0);if(!input)return null;
 input.focus();if(input instanceof HTMLInputElement)Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(query)});else input.textContent=${JSON.stringify(query)};
 input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));
 const selector=input.id?'#'+input.id:input.type==='search'?'input[type=search]':'searchbox';
 const form=input.closest('form');if(form)setTimeout(()=>form.requestSubmit(),0);return {selector,submitted:!!form}})()`)
}
async function readPage(wc,site=''){
 return evaluateDOM(wc,`(()=>{const visible=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;
 const site=${JSON.stringify(site)},selectors={baidu:'#content_left .result h3 a, #content_left .c-container h3 a',github:'[data-testid="results-list"] h3 a, [data-testid="result-list"] h3 a',bilibili:'a[href*="bilibili.com/video/"]'};
 const resultSelector=selectors[site]||'main h2 a, main h3 a, [role=main] h2 a, [role=main] h3 a, .search-results h2 a, .search-results h3 a';
 const elements=[...document.querySelectorAll(resultSelector)].filter(e=>visible(e)&&!e.closest('nav,header,footer,aside'));
 const links=[...new Map(elements.filter(e=>e.innerText.trim().length>2).map(e=>({title:e.innerText.trim().slice(0,160),url:e.href})).filter(e=>/^https?:/.test(e.url)).map(e=>[e.url,e])).values()].slice(0,50);
 return {title:document.title,url:location.href,text:(document.body?.innerText||'').slice(0,14000),links,resultSelector,ready:document.readyState}})()`)
}
async function searchResults(wc,site,query,original,timeout=18000){
 const end=Date.now()+timeout;let content
 do{
  try{
   content=await readPage(wc,site)
   const url=new URL(content.url),params=[...url.searchParams.values()],words=query.toLowerCase().split(/\s+/)
   const reflected=params.includes(query)||words.every(w=>content.text.toLowerCase().includes(w))
   const moved=content.url!==original||params.includes(query)
   const links=content.links.filter(l=>l.title!==query&&l.url!==content.url)
   if(reflected&&moved&&links.length>=2&&content.ready!=='loading')return {...content,verified:true,proof:{query_reflected:true,navigation:true,result_links:links.length,result_selector:content.resultSelector}}
  }catch{}
  await sleep(250)
 }while(Date.now()<end)
 throw Error('页面已打开，但尚未验证搜索结果；网站可能需要登录、验证码或网络恢复。')
}
module.exports={SITES,domReady,fillSearch,readPage,searchResults,evaluateDOM}
