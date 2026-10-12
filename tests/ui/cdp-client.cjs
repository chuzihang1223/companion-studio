const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function connect(port, urlPart = '/app/') {
  let page
  for (let i = 0; i < 100; i++) {
    try { page = (await (await fetch(`http://127.0.0.1:${port}/json`,{signal:AbortSignal.timeout(1500)})).json()).find(p => p.type === 'page' && p.url.includes(urlPart)); if (page) break } catch {}
    await sleep(400)
  }
  if (!page) throw Error('No application renderer')
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve); socket.addEventListener('error', reject) })
  let id = 0; const pending = new Map()
  socket.addEventListener('message', ({data}) => { const r = JSON.parse(data); if (pending.has(r.id)) { pending.get(r.id)(r); pending.delete(r.id) } })
  async function call(method, params = {}) {
    const n = ++id
    let timer
    const r = await new Promise((resolve,reject) => { timer=setTimeout(()=>reject(Error(`Timeout: ${method}`)),45000); pending.set(n,resolve); socket.send(JSON.stringify({id:n,method,params})) }).finally(()=>clearTimeout(timer))
    if (r.error) throw Error(r.error.message); return r.result
  }
  async function evaluate(expression) { const r = await call('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true}); if(r.exceptionDetails) throw Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); return r.result?.value }
  async function until(expression, timeout = 15000) { const end=Date.now()+timeout; while(Date.now()<end) { if(await evaluate(expression)) return; await sleep(100) } throw Error(`Not ready: ${expression}`) }
  return {call,evaluate,until,close:()=>socket.close()}
}
module.exports = {connect,sleep}
