const fs = require('node:fs')
const path = require('node:path')
const { app } = require('electron')

const projectRoot = path.resolve(__dirname, '../..')
const home = process.env.COMPANION_HOME || (app.isPackaged
  ? path.resolve(path.dirname(process.execPath), '../..')
  : path.basename(projectRoot).toLowerCase() === 'source'
    ? path.dirname(projectRoot)
    : path.join(projectRoot, '..'))
const userData = path.join(home, 'UserData')
const cache = path.join(home, 'Cache')
const logs = path.join(userData, 'logs')
for (const dir of [userData, cache, logs, path.join(userData, 'session'), path.join(cache, 'chromium')]) fs.mkdirSync(dir, { recursive: true })

app.setPath('userData', userData)
app.setPath('sessionData', path.join(userData, 'session'))
const crashDumps = path.join(cache, 'crash-dumps')
fs.mkdirSync(crashDumps, { recursive: true })
app.setPath('crashDumps', crashDumps)
process.env.TEMP = cache
process.env.TMP = cache
app.commandLine.appendSwitch('disk-cache-dir', path.join(cache, 'chromium'))

module.exports = { projectRoot, home, userData, cache, logs }
