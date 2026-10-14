from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.session import Base
from app.models import Conversation, Message
from app.services.character.relationship_memory import relationship_memory


def test_relationship_memory_quotes_only_saved_user_statements(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'memory.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            current = Conversation(character_id="assistant")
            deleted = Conversation(character_id="assistant", is_deleted=True)
            db.add_all((current, deleted))
            db.flush()
            earlier = Message(
                character_id="assistant",
                conversation_id=current.id,
                role="user",
                content="你可以叫我小雨。",
            )
            db.add_all(
                (
                    earlier,
                    Message(
                        character_id="assistant",
                        conversation_id=current.id,
                        role="assistant",
                        content="我爱你。",
                    ),
                    Message(
                        character_id="assistant",
                        conversation_id=deleted.id,
                        role="user",
                        content="我们是恋人。",
                    ),
                )
            )
            db.commit()
            recalled = relationship_memory(db, "assistant")
            assert "你可以叫我小雨" in " ".join(recalled)
            assert "我们是恋人" not in " ".join(recalled)
            assert "我爱你" not in " ".join(recalled)
            assert relationship_memory(db, "assistant", {earlier.id}) == []
    finally:
        engine.dispose()
