import {getConversation,loadConversation,listBranches,loadWorkForConversation} from './api'
/** 历史入口只依据后端保存的会话类型选择聊天/工作模式。 */
export class ConversationNavigator {
  async open(id: string) {
    const conversation = await getConversation(id)
    const [messages,branches,work] = await Promise.all([
      loadConversation(id),listBranches(id),
      conversation.conversation_type === 'chat' ? Promise.resolve(null) : loadWorkForConversation(id),
    ])
    const mode = conversation.conversation_type === 'chat' ? 'chat' as const : 'agent' as const
    void window.desktopAPI?.logDiagnostic('conversation-routing.log',{id,type:conversation.conversation_type,mode,work:work?.id})
    return {conversation,messages,branches,work,mode}
  }
}
