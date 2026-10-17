"""Two owned jobs at most, durable per-document commits and explicit restart review."""

import asyncio
from contextlib import nullcontext
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import threading
import time
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select, text

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexJob, now
from app.services.knowledge.chunker import chunk_document
from app.services.knowledge.contracts import ACTIVE, KnowledgeError
from app.services.knowledge.filesystem import checked_path, read_document
from app.services.knowledge.isolated_parser import parse_isolated
from app.services.knowledge.store import dumps, tokens
from app.services.knowledge.trace import event


class IndexManager:
    def __init__(self, store, recorder=None):
        self.store, self.recorder = store, recorder
        self.tasks, self.signals = {}, {}

    def jobs(self, collection_id):
        with self.store.sessions() as db:
            self.store.collection(db, collection_id)
            rows = db.scalars(
                select(KnowledgeIndexJob)
                .where(KnowledgeIndexJob.collection_id == str(collection_id))
                .order_by(KnowledgeIndexJob.created_at.desc())
                .limit(30)
            )
            return [self.dto(row) for row in rows]

    @staticmethod
    def dto(row):
        return {
            "id": row.id,
            "collection_id": row.collection_id,
            "state": row.state,
            "progress": json.loads(row.progress_json),
            "last_error": row.last_error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def start(self, collection_id, *, full=False):
        if len(self.tasks) >= 2:
            raise KnowledgeError("KNOWLEDGE_JOB_CAPACITY")
        with self.store.sessions.begin() as db:
            row = self.store.collection(db, collection_id)
            if db.scalar(
                select(KnowledgeIndexJob.id).where(
                    KnowledgeIndexJob.collection_id == row.id, KnowledgeIndexJob.state.in_(ACTIVE)
                )
            ):
                raise KnowledgeError("KNOWLEDGE_JOB_ACTIVE")
            options, roots = self.store.config(row)
            if not roots:
                raise KnowledgeError("KNOWLEDGE_ROOT_REQUIRED")
            job = KnowledgeIndexJob(collection_id=row.id, revision=row.revision)
            db.add(job)
            db.flush()
            job_id = job.id
        self.signals[job_id] = (threading.Event(), threading.Event())
        self.tasks[job_id] = asyncio.create_task(self._run(job_id, full))
        return job_id

    async def _run(self, job_id, full):
        try:
            scope = (
                await self.recorder.execution_scope(job_id, None, None)
                if self.recorder
                else nullcontext()
            )
            with scope:
                await asyncio.to_thread(self.run_sync, job_id, full=full)
        finally:
            self.tasks.pop(job_id, None)
            self.signals.pop(job_id, None)

    def control(self, job_id, action):
        signals = self.signals.get(str(job_id))
        if signals is None:
            raise KnowledgeError("KNOWLEDGE_JOB_NOT_RUNNING")
        stop, pause = signals
        if action == "cancel":
            stop.set()
        elif action == "pause":
            pause.set()
        elif action == "continue":
            pause.clear()
        else:
            raise KnowledgeError("KNOWLEDGE_CONTROL_INVALID")

    def checkpoint(self, job_id, state, progress=None, error=None):
        with self.store.sessions.begin() as db:
            row = db.get(KnowledgeIndexJob, job_id)
            row.state, row.last_error = state, error
            if progress is not None:
                row.progress_json = dumps(progress)

    def check(self, job_id):
        stop, pause = self.signals.get(job_id, (threading.Event(), threading.Event()))
        if pause.is_set():
            self.checkpoint(job_id, "PAUSED")
        while pause.is_set() and not stop.wait(0.1):
            pass
        if stop.is_set():
            raise KnowledgeError("KNOWLEDGE_CANCELLED")
        return stop

    def discover(self, roots, options, job_id):
        inspected = count = 0
        excluded = {v.casefold() for v in options.excluded_names}
        for root in roots:
            checked_path(root, ".")

            def unavailable(_error):
                raise KnowledgeError("KNOWLEDGE_DIRECTORY_UNREADABLE")

            for folder, directories, files in os.walk(
                root["path"], followlinks=False, onerror=unavailable
            ):
                self.check(job_id)
                inspected += len(directories) + len(files)
                if inspected > 100000:
                    raise KnowledgeError("KNOWLEDGE_SCAN_LIMIT")
                allowed = []
                for name in sorted(directories):
                    relative = (Path(folder) / name).relative_to(root["path"]).as_posix()
                    if name.casefold() in excluded or any(
                        fnmatch(relative + "/", pattern) for pattern in options.exclude_globs
                    ):
                        continue
                    # A reparse point cannot expand the collection's authorization.
                    try:
                        checked_path(root, relative)
                    except (KnowledgeError, OSError, ValueError):
                        continue
                    allowed.append(name)
                directories[:] = allowed
                for name in sorted(files):
                    path = Path(folder) / name
                    relative = path.relative_to(root["path"]).as_posix()
                    if path.suffix.lower() not in options.allowed_types:
                        continue
                    if any(fnmatch(relative, pattern) for pattern in options.exclude_globs):
                        continue
                    if options.include_globs and not any(
                        fnmatch(relative, pattern) for pattern in options.include_globs
                    ):
                        continue
                    count += 1
                    if count > options.max_documents:
                        raise KnowledgeError("KNOWLEDGE_DOCUMENT_LIMIT")
                    yield root, relative

    def run_sync(self, job_id, *, full=False):
        progress = {
            "processed": 0,
            "new": 0,
            "modified": 0,
            "unchanged": 0,
            "deleted": 0,
            "errors": 0,
        }
        started, seen = time.perf_counter(), set()
        with self.store.sessions() as db:
            job = db.get(KnowledgeIndexJob, job_id)
            collection_id, revision = job.collection_id, job.revision
            options, roots = self.store.config(self.store.collection(db, collection_id))
        event("INDEX_JOB_STARTED", job_id=job_id, collection_id=collection_id)
        try:
            self.checkpoint(job_id, "SCANNING", progress)
            for root, relative in self.discover(roots, options, job_id):
                self.check(job_id)
                document_id = str(
                    uuid5(NAMESPACE_URL, collection_id + ":" + root["id"] + ":" + relative)
                )
                seen.add(document_id)
                try:
                    changed = self.index_one(
                        job_id, collection_id, revision, root, relative, document_id, options, full
                    )
                    progress[changed] += 1
                except KnowledgeError as error:
                    if error.code == "KNOWLEDGE_CANCELLED":
                        raise
                    progress["errors"] += 1
                    self.invalidate(collection_id, root["id"], relative, document_id, error.code)
                    event("DOCUMENT_FAILED", collection_id=collection_id, document_id=document_id)
                progress["processed"] += 1
                self.checkpoint(job_id, "SCANNING", progress)
            self.check(job_id)
            with self.store.sessions.begin() as db:
                collection = self.store.collection(db, collection_id)
                if collection.revision != revision:
                    raise KnowledgeError("KNOWLEDGE_REVISION_CONFLICT")
                for doc in db.scalars(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.collection_id == collection_id
                    )
                ):
                    if doc.id not in seen and doc.status != "DELETED":
                        self.store.purge_chunks(db, doc.id)
                        doc.status, doc.last_error = "DELETED", None
                        doc.version += 1
                        progress["deleted"] += 1
                collection.index_version += 1
                collection.status = "READY_WITH_ERRORS" if progress["errors"] else "READY"
                collection.last_indexed_at = now()
            progress["duration_ms"] = (time.perf_counter() - started) * 1000
            self.checkpoint(job_id, "COMPLETED", progress)
            event(
                "INDEX_JOB_COMPLETED",
                job_id=job_id,
                collection_id=collection_id,
                document_count=progress["processed"],
            )
        except KnowledgeError as error:
            self.checkpoint(
                job_id,
                "CANCELLED" if error.code == "KNOWLEDGE_CANCELLED" else "NEEDS_REVIEW",
                progress,
                error.code,
            )
        except Exception:
            self.checkpoint(job_id, "FAILED", progress, "KNOWLEDGE_INDEX_FAILED")

    def index_one(
        self, job_id, collection_id, revision, root, relative, document_id, options, full
    ):
        self.checkpoint(job_id, "PARSING")
        raw, metadata = read_document(root, relative, options.max_file_bytes)
        with self.store.sessions() as db:
            doc = db.get(KnowledgeDocument, document_id)
            unchanged = (
                doc
                and doc.status in {"INDEXED", "UNCHANGED"}
                and doc.content_hash == metadata["content_hash"]
                and not full
            )
        if unchanged:
            with self.store.sessions.begin() as db:
                doc = db.get(KnowledgeDocument, document_id)
                doc.status, doc.mtime_ns = "UNCHANGED", metadata["mtime_ns"]
            return "unchanged"
        stop = self.check(job_id)
        blocks = parse_isolated(raw, Path(relative).suffix.lower(), stop)
        event("DOCUMENT_PARSED", collection_id=collection_id, document_id=document_id)
        self.checkpoint(job_id, "CHUNKING")
        chunks = chunk_document(
            document_id, blocks, size=options.chunk_characters, overlap=options.overlap
        )
        self.check(job_id)
        # Parsing can take seconds. Re-open/re-hash before commit so a changed
        # document cannot acquire a new proof for an older byte sequence.
        _, current = read_document(root, relative, options.max_file_bytes)
        if current != metadata:
            raise KnowledgeError("KNOWLEDGE_FILE_CHANGED")
        self.checkpoint(job_id, "INDEXING")
        with self.store.sessions.begin() as db:
            collection = self.store.collection(db, collection_id)
            if collection.revision != revision:
                raise KnowledgeError("KNOWLEDGE_REVISION_CONFLICT")
            doc = db.get(KnowledgeDocument, document_id)
            state = "modified" if doc else "new"
            if doc is None:
                doc = KnowledgeDocument(
                    id=document_id,
                    collection_id=collection_id,
                    root_id=root["id"],
                    relative_path=relative,
                    file_type=Path(relative).suffix.lower(),
                    version=0,
                )
                db.add(doc)
            self.store.purge_chunks(db, document_id)
            total = db.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.collection_id == collection_id)
            )
            if total + len(chunks) > options.max_chunks:
                raise KnowledgeError("KNOWLEDGE_COLLECTION_CHUNK_LIMIT")
            for key, value in metadata.items():
                setattr(doc, key, value)
            doc.version += 1
            doc.status, doc.last_error, doc.indexed_at = "INDEXED", None, now()
            for chunk in chunks:
                db.add(
                    KnowledgeChunk(
                        id=chunk["id"],
                        document_id=document_id,
                        collection_id=collection_id,
                        version=doc.version,
                        ordinal=chunk["ordinal"],
                        content=chunk["content"],
                        content_hash=chunk["content_hash"],
                        location_json=dumps(chunk["location"]),
                    )
                )
                db.execute(
                    text(
                        "INSERT INTO knowledge_fts(chunk_id,collection_id,terms) VALUES(:id,:collection,:terms)"
                    ),
                    {
                        "id": chunk["id"],
                        "collection": collection_id,
                        "terms": " ".join(tokens(chunk["content"])),
                    },
                )
            collection.index_version += 1
        event(
            "DOCUMENT_INDEXED",
            collection_id=collection_id,
            document_id=document_id,
            chunk_count=len(chunks),
        )
        return state

    def invalidate(self, collection_id, root_id, relative, document_id, error):
        with self.store.sessions.begin() as db:
            doc = db.get(KnowledgeDocument, document_id)
            if doc is None:
                doc = KnowledgeDocument(
                    id=document_id,
                    collection_id=collection_id,
                    root_id=root_id,
                    relative_path=relative,
                    file_type=Path(relative).suffix.lower(),
                    version=0,
                )
                db.add(doc)
            self.store.purge_chunks(db, document_id)
            doc.version += 1
            doc.status, doc.last_error = "ERROR", error
            self.store.collection(db, collection_id).index_version += 1

    async def close(self):
        tasks = list(self.tasks.values())
        for stop, _ in self.signals.values():
            stop.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
