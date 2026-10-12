import pytest
from app.services.character.self_reference import SelfReferenceResolver


@pytest.mark.parametrize('message,intent', [
    ('你是怎么诞生的？', 'software_self'),
    ('你是谁做出来的？', 'software_self'),
    ('你里面用了什么？', 'software_self'),
    ('按照角色剧情，你是怎么诞生的？', 'character_lore'),
    ('你在游戏世界观中以前是什么？', 'character_lore'),
    ('这个软件技术上是怎么实现的？', 'technical_self'),
    ('你今天开心吗？', 'character_conversation'),
    ('今天我们玩什么游戏？', 'character_conversation'),
])
def test_self_reference_uses_explicit_context(message, intent):
    assert SelfReferenceResolver().resolve(message) == intent


def test_software_context_contains_verified_facts_without_invented_dates():
    context = SelfReferenceResolver().context('你是怎么诞生的？')
    assert '逐步' in context and 'Electron' in context
    assert '没有已知的具体个人开发者' in context
    assert SelfReferenceResolver().context('我想你了') == ''
