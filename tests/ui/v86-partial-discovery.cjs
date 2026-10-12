const assert=require('node:assert/strict'),{mergeApplications}=require('../../desktop/electron/application-discovery.cjs')
const scan=(applications)=>({applications,scanned_at:new Date().toISOString(),source_status:{targeted:{complete:false}}})
const exe=process.execPath
const first=mergeApplications(scan([{name:'Generic Player',executable:exe,source:'registry_uninstall'},{name:'Generic Player',executable:exe,source:'start_menu',shortcut_path:'F:\\test\\Generic Player.lnk'}]))
const record=first.applications[0];record.launch_candidates[0].launch_success_count=3;record.preferred_launch_method=record.launch_candidates[0].id
const next=mergeApplications(scan([{name:'Generic Player',executable:exe,source:'start_apps',launch_type:'start_app',app_user_model_id:exe}]),first.applications,{partial:true})
assert.equal(next.applications.length,1);const actual=next.applications[0]
assert.ok(actual.sources.includes('registry_uninstall')&&actual.sources.includes('start_menu')&&actual.sources.includes('start_apps'))
assert.ok(actual.launch_candidates.length>record.launch_candidates.length)
assert.equal(actual.preferred_launch_method,record.preferred_launch_method)
assert.equal(actual.launch_candidates.find(r=>r.id===record.launch_candidates[0].id).launch_success_count,3)
console.log(JSON.stringify({pass:true,partial_discovery_preserves_all_sources:true,candidates:actual.launch_candidates.length}))
