// Windows PowerShell 5 reads BOM-less scripts as ANSI. Chinese selectors must
// survive the exact encoding of the packaged worker, not just a JS unit test.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict')
const worker=fs.readFileSync(path.join(__dirname,'../../desktop/electron/automation/windows-worker.ps1'))
assert.deepEqual([...worker.subarray(0,3)],[0xef,0xbb,0xbf],'Windows worker must be UTF-8 with BOM')
assert.ok(worker.toString('utf8').includes("-eq '单曲'"))
console.log(JSON.stringify({pass:true,powershell5_chinese_selectors:true}))
