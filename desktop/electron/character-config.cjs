'use strict'
const path = require('node:path')
const backend = require('./backend.cjs')
const idPath = id => {
  if (typeof id !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(id)) throw Error('无效角色 ID')
  return '/' + encodeURIComponent(id)
}
async function request(suffix = '', method = 'GET', body) {
  const serialized = body === undefined ? undefined : JSON.stringify(body)
  if (serialized && serialized.length > 8500000) throw Error('角色卡文件过大')
  const response = await fetch(backend.address.url + '/api/characters' + suffix, {
    method, headers: {'Content-Type':'application/json','X-Companion-Control-Token':backend.controlToken},
    body:serialized, signal:AbortSignal.timeout(15000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '角色配置无效，请检查字段和文件格式')
  return data
}
module.exports = {
  library: () => request(),
  detail: id => request(idPath(id)),
  create: body => request('/custom', 'POST', body),
  update: (id, body) => request(idPath(id), 'PATCH', body),
  activate: id => { idPath(id);return request('/active', 'PUT', {id}) },
  importCard: input => {
    if (!input || typeof input.filename !== 'string' || typeof input.data_base64 !== 'string' || !/\.(json|png)$/i.test(input.filename)) throw Error('请选择 JSON 或 PNG 角色卡')
    return request('/import', 'POST', {filename:path.basename(input.filename),data_base64:input.data_base64})
  },
}
