import asyncio

from sqlalchemy import func, select

from app.models.knowledge import KnowledgeDocument, KnowledgeIndexJob
from app.services.knowledge.contracts import CollectionConfig, CreateCollection
from app.services.knowledge.indexer import IndexManager


def test_metadata_history_caps_and_explicit_rebuild_recovers(corpus):
    store, root, owner = corpus
    collection = store.create(
        CreateCollection(name="Bounded churn", config=CollectionConfig(max_documents=1))
    )
    collection = store.update(collection["id"], 1, add_root=str(root))
    store.grant(owner, "assistant", [collection["id"]])
    (root / "one.txt").write_text("original fixture", encoding="utf-8")

    async def run():
        manager = IndexManager(store)

        async def index(full=False):
            key = await manager.start(collection["id"], full=full)
            await manager.tasks[key]
            return manager.jobs(collection["id"])[0]

        assert (await index())["state"] == "COMPLETED"
        (root / "one.txt").rename(root / "two.txt")
        assert (await index())["state"] == "COMPLETED"
        (root / "two.txt").rename(root / "three.txt")
        assert (await index())["state"] == "NEEDS_REVIEW"
        with store.sessions() as db:
            assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 2
        with store.sessions.begin() as db:
            for _ in range(35):
                db.add(
                    KnowledgeIndexJob(collection_id=collection["id"], revision=2, state="COMPLETED")
                )
        assert (await index(True))["state"] == "COMPLETED"
        with store.sessions() as db:
            assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
            assert db.scalar(select(func.count()).select_from(KnowledgeIndexJob)) == 30
        assert (root / "three.txt").read_text(encoding="utf-8") == "original fixture"
        await manager.close()

    asyncio.run(run())
