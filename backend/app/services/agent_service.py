"""Two-stage tool calling: preview first, execute approved work, then speak in character."""

import asyncio
import base64
from dataclasses import dataclass
import json
from pathlib import Path
import time
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.database.session import SessionLocal
from app.models import Conversation, Message, WorkPermission
from app.services.agent_tools import PermissionManager, ToolExecutor, ToolRegistry
from app.services.character.prompt_builder import CharacterPromptBuilder
from app.services.character.relationship_memory import relationship_memory
from app.services.character.personalization import read_settings, relevant_manual
from app.services.conversation import touch_after_user_message, visible_messages
from app.services.attachment_service import AttachmentService
from app.services.trusted_actions import trusted_actions, action_for, risk_for
from app.services.desktop.preferences import read_preferences
from app.services.workspace_service import validate_root
from app.services.application_catalog import applications
from app.services.coding.write_intent import CodingWriteIntentResolver
from app.services.coding.workspace import CodingWorkspaceExecutor
from app.services.application_catalog import resolvable_applications
from app.services.application_alias import ApplicationAliasResolver
from app.services.local_action_intent import LocalActionIntentResolver
from app.services.llm.model_roles import ExecutionModelUnavailable
from app.services.desktop.lifecycle import TaskCancelled


@dataclass
class AgentContext:
    character_id: str
    conversation_id: str | None
    user_text: str
    prompt: list[dict]
    assistant_plan: dict
    calls: list[dict]
    created: float
    work_session_id: str | None = None
    prepared_attachments: list | None = None
    executor: ToolExecutor | None = None
    user_message_id: str | None = None
    coding_mode: str = 'virtual_only'
    local_resolution: dict | None = None
    renderer_id: str | None = None
    task_id: str | None = None


class AgentService:
    def __init__(self):
        self.registry = ToolRegistry()
        self.executor = ToolExecutor(settings.data_root / "agent_workspace")
        self.pending: dict[str, AgentContext] = {}

    async def plan(self, app, character_id: str, conversation_id: str | None, user_text: str,
                   attachments: list | None = None, workspace_folder: str | None = None, renderer_id: str | None = None) -> dict:
        character = app.state.characters.get(character_id)
        if character is None:
            raise LookupError("角色不存在")
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id) if conversation_id else None
            if conversation_id and (conversation is None or conversation.is_deleted or conversation.character_id != character_id):
                raise LookupError("聊天不存在")
            previous = visible_messages(db, conversation) if conversation else []
            history = [{"role": row.role, "content": row.content} for row in previous[-20:]]
            memory = relationship_memory(db, character_id, {row.id for row in previous[-20:]})
            overlay = read_settings(db, character_id)
            manual = relevant_manual(db, character_id, user_text)
        prompt = CharacterPromptBuilder().build(character, history, user_text,
                                                 memory_context=memory, personalization=overlay,
                                                 manual_memory=manual)
        attachment_service = AttachmentService()
        roles = getattr(app.state, 'model_roles', None)
        active_config = getattr(roles.service('character_model') if roles else app.state.llm_service, "config", None)
        prepared = attachment_service.prepare(attachments or [], vision=bool(
            active_config and active_config.capabilities.get("vision")))
        file_context = attachment_service.context(prepared, user_text)
        if file_context:
            prompt[-1]["content"] += "\n\n" + file_context
        if any(item.kind == "image" for item in prepared):
            prompt[-1]["content"] = [{"type": "text", "text": prompt[-1]["content"]}, *(
                {"type": "image_url", "image_url": {"url": f"data:{item.media_type};base64,{base64.b64encode(item.content).decode('ascii')}"}}
                for item in prepared if item.kind == "image")]
        external_workspace = None
        coding_mode = CodingWriteIntentResolver().resolve(user_text)
        executor = CodingWorkspaceExecutor(None, coding_mode, str(uuid4()))
        if conversation_id:
            try:
                stored_workspace = app.state.work_state.for_conversation(conversation_id).get('workspace_root')
            except LookupError:
                stored_workspace = None
            if workspace_folder and workspace_folder != stored_workspace:
                raise ValueError('请先通过工作文件夹栏选择并保存目录')
            workspace_folder = stored_workspace
        elif workspace_folder:
            raise ValueError('请先创建工作会话，再通过工作文件夹栏选择并保存目录')
        if workspace_folder:
            external_workspace = Path(validate_root(workspace_folder))
            executor = CodingWorkspaceExecutor(external_workspace, coding_mode, str(uuid4()))
            # Reject protected application directories before exposing any project to a provider.
            executor._path('.')
            prompt[0]["content"] += (f"\n\nSELECTED_WORKSPACE\n用户选定项目目录：{external_workspace}。"
                                      "所有工具路径均是此目录下的相对路径；按本机权限策略执行。")
        if coding_mode == 'write_workspace' and external_workspace is None:
            raise ValueError('请先选择要保存或修改的项目文件夹；当前草稿仍保留在工作区')
        prompt[0]["content"] += ("\n\nAGENT CAPABILITY\n用户要求实际执行任务。必须使用提供的工具；"
                                   "不要声称已完成尚未执行的操作。只在工具返回结果后，才按当前角色资料的语气说明结果。"
                                   "没有明确保存/修改项目意图时，代码只进入虚拟草稿；运行代码按本机权限策略执行。")
        prompt[0]['content'] += ('\nCODING_WRITE_INTENT\n当前用户意图为 '+coding_mode+
            '。virtual_only/preview_patch 的文件写入会被执行层转为草稿，不能升级为本地应用。'
            '选择项目目录仅允许按策略读取。write_workspace 才可实际修改选定目录。')
        prompt[0]['content'] += '\n桌面偏好：默认音乐客户端 '+read_preferences()['music_player']+'。要求播放时使用 music_play，不能只启动或搜索。修改整个项目使用 coding_task。'
        relevant = [r for r in applications() if r.get('name', '').lower() in user_text.lower()]
        if relevant:
            prompt[0]['content'] += '\n相关应用（只用 app_id）：' + json.dumps([{'app_id': r['app_id'], 'name': r['name']} for r in relevant[:20]], ensure_ascii=False)
        local_resolution, deterministic = None, None
        intent = LocalActionIntentResolver().resolve(user_text)
        if intent:
            local_resolution = {'operation':intent.operation, 'target':intent.target, 'complex':intent.complex}
            if intent.operation.startswith('app.'):
                matches = ApplicationAliasResolver().match(intent.target, resolvable_applications())
                if not matches and getattr(app.state, 'desktop_bridge', None):
                    # One registered-source refresh, then continue the original local action.
                    try:
                        resolution = await app.state.desktop_bridge.request('applications.resolve', {'target':intent.target}, '', timeout=60)
                        matches = ApplicationAliasResolver().match(intent.target, resolvable_applications())
                        local_resolution['discovery'] = resolution.get('levels', [])
                        local_resolution['discovered'] = True
                    except Exception:
                        local_resolution['rescan_failed'] = True
                selected = ApplicationAliasResolver.unique(matches)
                local_resolution['candidates'] = matches
                if selected:
                    arguments = {'app':selected['app_id']}
                    local_resolution['name'] = selected['name']
                else:
                    arguments = None
                    local_resolution['error'] = ('找到多个相近应用，请选择一个启动入口。' if matches else
                        '没有检测到该应用，可以在设置中选择快捷方式或 EXE。')
            else:
                arguments = intent.arguments if intent.arguments is not None else {'url':intent.target} if intent.operation == 'browser.open_url' else {}
            if arguments is not None:
                deterministic = {'role':'assistant', 'content':None, 'tool_calls':[{'id':'local-'+str(uuid4()),
                    'type':'function','function':{'name':intent.operation.replace('.', '_'),
                                                'arguments':json.dumps(arguments, ensure_ascii=False)}}]}
        if intent and not intent.complex:
            assistant_plan = deterministic or {'role':'assistant','content':None}
        else:
            try:
                roles = getattr(app.state, 'model_roles', None)
                assistant_plan = await (roles.plan_tools(prompt, self.registry.schemas_for(user_text),role='planning_model') if roles else
                                       app.state.llm_service.plan_tools(prompt, self.registry.schemas_for(user_text)))
                if not assistant_plan.get('tool_calls'):
                    raise ExecutionModelUnavailable()
            except ExecutionModelUnavailable as error:
                assistant_plan = deterministic or {'role':'assistant','content':None}
                local_resolution = {**(local_resolution or {}), 'remaining_unavailable':True,
                                    'error':str(error)}
        calls = self.registry.parse_calls(assistant_plan)
        for call in calls:
            call['coding_mode'] = coding_mode
            if intent and call['name'] == intent.operation:
                call['target_text'] = intent.target
        writes = [call for call in calls if call['name'] in {'code.edit_file', 'filesystem.write'}]
        if len(writes) > 1:
            for call in writes:
                call['bulk_overwrite'] = True
        if external_workspace:
            for call in calls:
                if call["name"].startswith(("code.", "filesystem.")):
                    call["requires_confirmation"] = True
                    call["risk_level"] = "medium"
        for call in calls:
            action = action_for(call, executor.workspace)
            call['action_id'] = action
            call['risk_level'] = risk_for(action)
            call['permission_required'] = call['requires_confirmation'] or call['risk_level'] != 'low' or bool(external_workspace)
            call['requires_confirmation'] = not trusted_actions.allowed(action)
            call['permission_required'] = True
        if not calls and local_resolution is None:
            local_resolution = {'error':str(ExecutionModelUnavailable()), 'remaining_unavailable':True}
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if now - value.created < 300}
        plan_id = str(uuid4())
        # Store the user's request before waiting for permissions or running tools.
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id) if conversation_id else Conversation(
                character_id=character_id, conversation_type='work', preferred_mode='work')
            if conversation_id is None:
                db.add(conversation)
                db.flush()
            conversation_id = conversation.id
            had_user = any(row.role == 'user' for row in visible_messages(db, conversation))
            user = Message(character_id=character_id, conversation_id=conversation_id, role='user', content=user_text,
                           attachments_json=json.dumps(attachment_service.save(prepared), ensure_ascii=False))
            db.add(user)
            touch_after_user_message(conversation, user_text, had_user)
            db.commit()
            user_message_id = user.id
        work = getattr(app.state, "work_state", None)
        work_session_id = work.create(user_text, conversation_id, calls, str(external_workspace) if external_workspace else None) if work else None
        if work_session_id:
            executor.bind(work_session_id)
        if work_session_id and external_workspace:
            work.module(work_session_id, 'files', '项目文件', {'root': str(external_workspace)})
        self.pending[plan_id] = AgentContext(character_id, conversation_id, user_text,
                                              prompt, assistant_plan, calls, now, work_session_id, prepared, executor, user_message_id, coding_mode, local_resolution)
        self.pending[plan_id].renderer_id=renderer_id
        if getattr(app.state, 'task_runtime', None) and work_session_id:
            app.state.task_runtime.prepare(self.pending[plan_id])
        if work and work_session_id:
            work.save_plan(work_session_id, {'plan_id': plan_id, 'character_id': character_id,
                'conversation_id': conversation_id, 'user_text': user_text, 'prompt': prompt,
                'assistant_plan': assistant_plan, 'calls': calls, 'work_session_id': work_session_id,
                'user_message_id': user_message_id, 'workspace': str(executor.workspace),
                'coding_mode': coding_mode, 'sandbox_task_id': executor.task_id, 'local_resolution':local_resolution,'renderer_id':renderer_id})
        return {"plan_id": plan_id, "calls": calls, "workspace": str(executor.workspace),
                'conversation_id': conversation_id, 'user_message_id': user_message_id,
                "work_session_id": work_session_id, "local_resolution": local_resolution}

    async def select_application(self, app, plan_id: str, app_id: str) -> dict:
        context = self.pending.get(plan_id)
        stored = app.state.work_state.restore_plan(plan_id)
        if context is None and stored:
            context = self._restore_context(app, stored)
            self.pending[plan_id] = context
        if context is None or stored is None or time.monotonic()-context.created > 300:
            raise LookupError('任务计划已过期，请重新发送')
        resolution = context.local_resolution or {}
        candidates = resolution.get('candidates', [])
        if context.calls or not any(row['app_id'] == app_id for row in candidates):
            raise ValueError('请从当前任务的候选应用中选择')
        current = next((row for row in resolvable_applications() if row['app_id'] == app_id), None)
        if not current or current.get('installed') is False:
            raise ValueError('这个启动入口已经不可用，请重新扫描电脑应用')
        message = {'role':'assistant','content':None,'tool_calls':[{
            'id':'local-'+str(uuid4()),'type':'function','function':{
                'name':resolution['operation'].replace('.', '_'),
                'arguments':json.dumps({'app':app_id})}}]}
        call = self.registry.parse_calls(message)[0]
        call.update(coding_mode=context.coding_mode, target_text=resolution['target'])
        call['action_id'] = action_for(call, context.executor.workspace)
        call['risk_level'] = risk_for(call['action_id'])
        call['permission_required'] = True
        call['requires_confirmation'] = not trusted_actions.allowed(call['action_id'])
        context.calls, context.assistant_plan = [call], message
        context.local_resolution = {**resolution, 'name':current.get('name'), 'candidates':[],
                                    'error':None, 'selected_app_id':app_id}
        stored.update(calls=context.calls, assistant_plan=message, local_resolution=context.local_resolution)
        app.state.work_state.save_plan(context.work_session_id, stored)
        if call['requires_confirmation']:
            with SessionLocal() as db:
                db.add(WorkPermission(session_id=context.work_session_id,call_id=call['id'],tool_name=call['name']))
                db.commit()
        app.state.work_state.emit(context.work_session_id, 'application_selected', {
            'summary':'已选择 '+str(current.get('name') or resolution['target'])},
            status='waiting_permission' if call['requires_confirmation'] else 'planning')
        app.state.task_runtime.prepare(context)
        return {'plan_id':plan_id,'calls':context.calls,'workspace':str(context.executor.workspace),
                'conversation_id':context.conversation_id,'work_session_id':context.work_session_id,
                'local_resolution':context.local_resolution}

    @staticmethod
    def _restore_context(app, stored):
        mode=stored.get('coding_mode') or CodingWriteIntentResolver().resolve(stored['user_text'])
        root=app.state.work_state.snapshot(stored['work_session_id']).get('workspace_root')
        executor=CodingWorkspaceExecutor(Path(root) if root else None,mode,
            stored.get('sandbox_task_id') or str(uuid4())).bind(stored['work_session_id'])
        return AgentContext(stored['character_id'],stored['conversation_id'],stored['user_text'],
            stored['prompt'],stored['assistant_plan'],stored['calls'],time.monotonic(),
            stored['work_session_id'],[],executor,stored['user_message_id'],mode,stored.get('local_resolution'),stored.get('renderer_id'))

    async def execute(self, app, plan_id: str, approved_ids: set[str], expression: str,
                      voice_enabled: bool, speed_factor: float, on_text=None, on_started=None) -> dict:
        context = self.pending.pop(plan_id, None)
        if context is None:
            stored = app.state.work_state.restore_plan(plan_id)
            if stored:
                context = self._restore_context(app, stored)
        if context is None or time.monotonic() - context.created > 300:
            raise LookupError("任务计划已过期，请重新发送")
        context.task_id = plan_id
        try:
            registry = getattr(app.state, 'runtime_registry', None)
            if registry:
                async with registry.run(plan_id, context.conversation_id, context.work_session_id, context.renderer_id):
                    return await self._execute_context(app,context,approved_ids,expression,voice_enabled,speed_factor,on_text,on_started)
            return await self._execute_context(app,context,approved_ids,expression,voice_enabled,speed_factor,on_text,on_started)
        except asyncio.CancelledError:
            if context.work_session_id:
                app.state.work_state.emit(context.work_session_id,'task_cancelled',{'summary':'任务已停止'},status='cancelled')
            raise
        except TaskCancelled:
            handle=getattr(app.state,'task_runtime',None).handles.get(context.work_session_id)
            status='interrupted' if handle and handle.exit_reason else 'cancelled'
            app.state.work_state.emit(context.work_session_id,'task_'+status,{'summary':'目标应用已关闭' if status=='interrupted' else '任务已停止'},status=status)
            raise
        except Exception:
            if context.work_session_id and not app.state.work_state.is_stopped(context.work_session_id):
                app.state.work_state.emit(context.work_session_id,'task_failed',{'summary':'任务请求未完成，请查看错误提示'},status='failed')
            raise
        finally:
            trusted_actions.finish_task(context.work_session_id)
            if getattr(app.state,'task_runtime',None):
                app.state.task_runtime.handles.pop(context.work_session_id,None)

    async def _execute_context(self,app,context,approved_ids,expression,voice_enabled,speed_factor,on_text=None,on_started=None):
        work = getattr(app.state, "work_state", None)
        work_id = context.work_session_id
        if work and work_id:
            work.decide(work_id, approved_ids)
        observations = []
        details = []
        runtime = getattr(app.state, 'task_runtime', None)
        goal_verified = None
        for call in ([] if runtime else context.calls):
            if work and work_id and work.is_stopped(work_id):
                break
            if work and work_id:
                work.emit(work_id, "tool_started", {"name": call["name"], "target": str(call["arguments"].get("path") or call["arguments"].get("url") or call["arguments"].get("app") or "")[:200]})
            action = action_for(call, (context.executor or self.executor).workspace)
            if not trusted_actions.permitted(action, call['id'], approved_ids, work_id):
                result = {"error": "用户未确认此操作，未执行"}
                status = "denied"
            else:
                try:
                    result = await asyncio.to_thread((context.executor or self.executor).execute, call)
                    status = "failed" if result.get("found") is False else "queued" if result.get("action") in {"open_url", "launch_app", "query_app", "focus_app", "close_app"} else "done"
                except Exception as exc:
                    result = {"error": str(exc)[:400]}
                    status = "failed"
            observations.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
            details.append({"name": call["name"], "status": status, "result": result})
            if work and work_id:
                work.emit(work_id, "tool_completed", {"name": call["name"], "status": status,
                                                     'result': result,
                                                     "summary": str(result.get("error") or result.get("action") or result.get("path") or "完成")[:200]})
        if runtime:
            observations, details, goal_verified = await runtime.run(context, approved_ids)
        if work and work_id:
            if work.is_stopped(work_id):
                raise ValueError("工作任务已停止")
            work.emit(work_id, "workflow_updated", {"status": "summarizing"}, status="summarizing", step=None if runtime else 2)
        final_prompt = [*context.prompt, context.assistant_plan, *observations,
                        {"role": "system", "content": "现在根据工具实际结果回复用户。保留当前角色资料定义的人格，"
                         "通常一至三句；网页工具只把打开请求交给桌面界面，不能声称已确认加载。"
                         "本地音乐工具只找到候选曲目，不能声称已经发声；第三方音乐由桌面界面请求打开已安装的客户端，用户需在客户端内手动选择播放。"
                         "应用工具只提出请求，桌面界面随后处理；不能声称已确认安装、运行、聚焦或关闭。失败时诚实说明，不朗读工具 JSON。"}]
        if runtime:
            final_prompt[-1] = {'role':'system', 'content':
                f'桌面任务已经实际执行，目标验证结果为 {goal_verified}。只依据观察和验证回应。'
                'verified=true 时才说完成或放好啦；失败必须说具体未完成的步骤，不猜测成功。'
                '按当前角色资料自然回应，一到三句即可，不朗读工具 JSON。'}
        if runtime and runtime.handles.get(work_id):await runtime.handles[work_id].checkpoint()
        if context.coding_mode != 'write_workspace':
            final_prompt[-1]['content'] += '\n本轮编码仅是虚拟草稿/建议，不能声称已保存或修改本地项目；即使测试通过也只是隔离 sandbox 的结果。'
        assistant_id=str(uuid4())
        if on_started:
            await on_started({'message_id':assistant_id,'character_id':context.character_id,
                'conversation_id':context.conversation_id,'user_text':context.user_text,'messages':context.prompt,
                'task_id':context.task_id,'work_session_id':work_id,'renderer_id':context.renderer_id})
        music_resolved = len(details) == 1 and details[0]['name'] in {'music.play','music.search'} and context.calls[0]['arguments'].get('provider') in {'qqmusic','netease'}
        if music_resolved:
            from app.services.desktop.music_task import music_feedback
            answer = music_feedback(context.calls[0], details[0]['result'], details[0]['status'] != 'done')
            if context.local_resolution and context.local_resolution.get('remaining_unavailable'):
                goal_verified = False
                answer += ' 后续多步骤任务需要配置可用的 Agent 执行模型。'
        elif context.local_resolution and (not context.local_resolution.get('complex') or
                                         context.local_resolution.get('remaining_unavailable')):
            info = context.local_resolution
            failed = next((row for row in details if row['status'] != 'done'), None)
            if failed:
                answer = '这一步还没完成：' + str(failed['result'].get('error') or '没有验证到应用就绪。')
            elif not context.calls:
                answer = info.get('error') or '请选择要操作的应用。'
                if info.get('candidates'):
                    answer += '\n'+'\n'.join('· '+row['name'] for row in info['candidates'])
            elif info['operation'] == 'app.launch':
                result = details[0]['result']
                game_launcher = any(row.get('launch_type')=='launcher' for row in result.get('launch_candidates',[]))
                answer = (info.get('name') or info['target']) + ('的启动器' if game_launcher else '') + ('已经打开啦，窗口已就绪。' if result.get('window') else '的进程已启动；窗口还在准备。')
            elif info['operation'] == 'app.focus':
                answer = '已经切换到'+(info.get('name') or info['target'])+'啦。'
            elif info['operation'] == 'app.close':
                answer = '已按你的确认关闭'+(info.get('name') or info['target'])+'。'
            elif info['operation'] == 'browser.open_url':
                answer = '网页已经打开，内容放在工作区啦。'
            elif info['operation'] == 'browser.search_site':
                answer = '搜索结果已经打开，放在网页模块啦。'
            elif info['operation'] in {'music.play','music.search'}:
                answer = '已经找到并验证播放啦。' if info['operation']=='music.play' else '歌曲搜索结果已经找到了。'
            else:
                answer = '状态已经读到了，详细结果放在工作区啦。'
            if context.calls and info.get('remaining_unavailable'):
                goal_verified = False
                answer += ' 后续多步骤任务需要配置可用的 Agent 执行模型。'
        else:
            roles = getattr(app.state, 'model_roles', None)
            service=roles.service('character_model') if roles else app.state.llm_service
            if on_text:
                from app.services.voice.canonical_text import CanonicalResponseText
                canonical=CanonicalResponseText()
                async for delta in service.stream_chat(final_prompt):
                    visible,_=canonical.feed(delta)
                    if visible:await on_text(visible)
                visible,_=canonical.feed('',True)
                if visible:await on_text(visible)
                answer=canonical.text
            else:
                answer=await service.chat(final_prompt)
        if on_text and (music_resolved or context.local_resolution and (not context.local_resolution.get('complex') or context.local_resolution.get('remaining_unavailable'))):
            await on_text(answer)
        if runtime and runtime.handles.get(work_id):await runtime.handles[work_id].checkpoint()
        if work and work_id and work.is_stopped(work_id):raise ValueError('工作任务已停止')
        if not answer.strip():
            raise ValueError("模型没有返回最终回复")
        try:
            with SessionLocal() as db:
                conversation = db.get(Conversation, context.conversation_id) if context.conversation_id else Conversation(character_id=context.character_id)
                if conversation is None or conversation.is_deleted:
                    raise LookupError("聊天已被删除")
                if not context.conversation_id:
                    db.add(conversation)
                    db.flush()
                had_user = bool(context.conversation_id) and any(row.role == "user" for row in visible_messages(db, conversation))
                metadata = AttachmentService().save(context.prepared_attachments or [])
                user = db.get(Message, context.user_message_id) if context.user_message_id else Message(
                    character_id=context.character_id, conversation_id=conversation.id, role='user', content=context.user_text,
                    attachments_json=json.dumps(metadata, ensure_ascii=False))
                assistant = Message(id=assistant_id,character_id=context.character_id, conversation_id=conversation.id,
                                    role="assistant", content=answer.strip())
                db.add_all((user, assistant))
                touch_after_user_message(conversation, context.user_text, had_user)
                db.commit()
                db.refresh(user)
                db.refresh(assistant)
                response = {"user_message_id": user.id, "message_id": assistant.id,
                            "conversation_id": conversation.id,
                            "content": assistant.content, "created_at": assistant.created_at.isoformat(),
                            "tools": details}
                if runtime:
                    response['managed_by_runtime'] = True
                    response['goal_verified'] = goal_verified
        except SQLAlchemyError:
            raise RuntimeError("Agent 回复保存失败")
        if voice_enabled and on_text is None:
            try:
                response["voice_job"] = app.state.voice_jobs.enqueue(
                    response["message_id"], expression=expression, speed_factor=speed_factor)
            except Exception as exc:
                response["voice_error"] = str(exc)[:200]
        if work and work_id:
            work.link_conversation(work_id, response["conversation_id"])
            if runtime:
                current = work.snapshot(work_id)
                work.step_status(work_id, len(current['steps'])-1, 'done' if goal_verified else 'failed')
                work.emit(work_id, 'task_completed' if goal_verified else 'task_failed',
                          {'message_id': response['message_id'], 'verified':goal_verified}, status='completed' if goal_verified else 'failed')
            else:
                work.emit(work_id, "work_completed", {"message_id": response["message_id"]}, status="done", step=4)
            response["work_session_id"] = work_id
        return response
