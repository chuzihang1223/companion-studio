"""Real same-file engines: fairness and leases follow the actual root transaction."""

import threading
import time

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.database.write_gate import WriteGateTimeout, register_write_engine


@pytest.fixture
def writers(tmp_path):
    engines = [
        create_engine(
            f"sqlite:///{(tmp_path / 'shared.db').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 0.1},
        )
        for _ in range(2)
    ]
    for engine in engines:

        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")

    gate = register_write_engine(engines[0])
    assert register_write_engine(engines[1]) is gate
    with engines[0].begin() as db:
        db.exec_driver_sql("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    yield engines, [sessionmaker(e) for e in engines], gate
    assert gate.snapshot()["waiting"] == 0 and not gate.snapshot()["owned"]
    for engine in engines:
        assert engine.pool.checkedout() == 0
        engine.dispose()


def wait_for(condition):
    end = time.monotonic() + 3
    while not condition() and time.monotonic() < end:
        time.sleep(0.001)
    assert condition()


def test_two_engines_queue_writers_in_actual_arrival_order(writers):
    _, factories, gate = writers
    observed, errors = [], []

    def write(index):
        try:
            with factories[index % 2]() as db:
                db.execute(text("INSERT INTO probe(value) VALUES (:v)"), {"v": str(index)})
                observed.append(index)
                db.commit()
        except BaseException as error:
            errors.append(error)

    threads = []
    with factories[0]() as owner:
        owner.execute(text("INSERT INTO probe(value) VALUES ('owner')"))
        for index in range(6):
            worker = threading.Thread(target=write, args=(index,))
            worker.start()
            threads.append(worker)
            wait_for(lambda: gate.snapshot()["waiting"] == index + 1)
        owner.commit()
    for worker in threads:
        worker.join(3)
        assert not worker.is_alive()
    assert not errors and observed == list(range(6))
    with factories[0]() as db:
        assert db.scalar(text("SELECT count(*) FROM probe")) == 7


def test_read_only_viewer_does_not_take_a_write_lease(writers):
    _, factories, gate = writers
    with factories[0]() as writer:
        writer.execute(text("INSERT INTO probe(value) VALUES ('pending')"))
        with factories[1]() as reader:
            assert reader.scalar(text("SELECT count(*) FROM probe")) == 0
            assert "companion_write_lease" not in reader.info
        assert gate.snapshot()["owned"] and gate.snapshot()["waiting"] == 0
        writer.rollback()


def test_savepoint_does_not_release_the_outer_writer(writers):
    _, factories, gate = writers
    with factories[0]() as db:
        db.execute(text("INSERT INTO probe(value) VALUES ('outer')"))
        with db.begin_nested():
            db.execute(text("INSERT INTO probe(value) VALUES ('nested')"))
        assert gate.snapshot()["owned"]
        db.rollback()
        assert not gate.snapshot()["owned"]
    with factories[1]() as db:
        assert db.scalar(text("SELECT count(*) FROM probe")) == 0


def test_failed_transaction_and_close_release_exactly_once(writers):
    _, factories, gate = writers
    with factories[0]() as db:
        with pytest.raises(Exception):
            db.execute(text("INSERT INTO probe(value) VALUES (NULL)"))
        assert gate.snapshot()["owned"]
    assert not gate.snapshot()["owned"]
    with factories[1]() as db:
        db.execute(text("INSERT INTO probe(value) VALUES ('after rollback')"))
        db.commit()
    assert gate.snapshot()["transactions"] == 2


def test_same_thread_nested_connection_write_fails_without_deadlocking(writers):
    _, factories, _ = writers
    with factories[0]() as first:
        first.execute(text("INSERT INTO probe(value) VALUES ('first')"))
        with factories[1]() as second:
            with pytest.raises(WriteGateTimeout):
                second.execute(text("INSERT INTO probe(value) VALUES ('nested connection')"))
        first.rollback()
