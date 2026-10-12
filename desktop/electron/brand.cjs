'use strict'
const fs = require('node:fs')
const path = require('node:path')
let buildInfo = {}
try { buildInfo = JSON.parse(fs.readFileSync(path.join(__dirname, 'build-info.json'), 'utf8')) } catch {}
module.exports = {
  buildInfo,
  appName: buildInfo.product_name || 'Companion Studio',
  appId: buildInfo.app_id || 'com.companionstudio.desktop',
  clean: true,
  icon: 'companion-studio.ico',
}
