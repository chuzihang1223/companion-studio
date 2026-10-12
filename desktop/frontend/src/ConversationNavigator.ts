import {getConversation,loadConversation,listBranches,loadWorkForConversation} from './api'
/** The stored type is the only source of truth for every history entry point. */
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
