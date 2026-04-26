import tempfile
from pathlib import Path

import pytest

import db


@pytest.fixture
def fresh_db():
    with tempfile.TemporaryDirectory() as tmp:
        db.init_db(Path(tmp) / "test.db")
        yield


def test_create_and_get_session(fresh_db):
    sid = db.create_session("chat1", "972529526517", status=db.STATUS_COLLECTING, plot=1096, building=6, apartment=1, stage=18)
    s = db.get_session(sid)
    assert s is not None
    assert s.chat_id == "chat1"
    assert s.plot == 1096
    assert s.has_destination


def test_session_without_destination(fresh_db):
    sid = db.create_session("chat1", "972529526517", status=db.STATUS_AWAITING_DESTINATION)
    s = db.get_session(sid)
    assert not s.has_destination


def test_active_session_isolated_per_sender(fresh_db):
    """Two senders in the same chat each get their own active session."""
    s_a = db.create_session("group1", "PHONE_A", status=db.STATUS_COLLECTING, plot=1, building=1, apartment=1, stage=1)
    s_b = db.create_session("group1", "PHONE_B", status=db.STATUS_COLLECTING, plot=2, building=2, apartment=2, stage=2)

    active_a = db.get_active_session("group1", "PHONE_A")
    active_b = db.get_active_session("group1", "PHONE_B")
    assert active_a.id == s_a
    assert active_b.id == s_b


def test_completed_session_not_active(fresh_db):
    db.create_session("chat1", "PHONE_A", status=db.STATUS_COMPLETED, plot=1, building=1, apartment=1, stage=1)
    assert db.get_active_session("chat1", "PHONE_A") is None


def test_update_session(fresh_db):
    sid = db.create_session("chat1", "PHONE_A", status=db.STATUS_COLLECTING)
    db.update_session(sid, plot=1096, building=6, apartment=1, stage=18, status=db.STATUS_AWAITING_CONFIRM)
    s = db.get_session(sid)
    assert s.status == db.STATUS_AWAITING_CONFIRM
    assert s.plot == 1096


def test_files_lifecycle(fresh_db):
    sid = db.create_session("chat1", "PHONE_A", status=db.STATUS_COLLECTING, plot=1, building=1, apartment=1, stage=1)
    fid = db.add_file_to_session(
        sid,
        whatsapp_msg_id="msg1",
        file_type="video",
        file_name="x.mp4",
        file_size=1024,
        mime_type="video/mp4",
        download_url="https://example.com/x.mp4",
    )
    files = db.list_files(sid)
    assert len(files) == 1
    assert files[0].uploaded is False

    db.mark_file_uploaded(fid, drive_file_id="d1", drive_file_link="https://drive/x", final_filename="1-1-1-1 - 26.04.26.mp4")
    files = db.list_files(sid)
    assert files[0].uploaded is True
    assert files[0].drive_file_id == "d1"


def test_folder_cache(fresh_db):
    db.upsert_folder(plot=1096, building=None, apartment=None, stage_num=None, stage_name=None,
                     drive_folder_id="root1", folder_name="מגרש 1096")
    db.upsert_folder(plot=1096, building=6, apartment=1, stage_num=18, stage_name="טיח פנים",
                     drive_folder_id="leaf1", folder_name="18 - טיח פנים")

    plot_row = db.lookup_folder(plot=1096)
    assert plot_row["drive_folder_id"] == "root1"

    leaf_row = db.lookup_folder(plot=1096, building=6, apartment=1, stage_num=18)
    assert leaf_row["drive_folder_id"] == "leaf1"
    assert leaf_row["stage_name"] == "טיח פנים"

    assert db.lookup_folder(plot=9999) is None


def test_idempotency(fresh_db):
    assert db.is_event_processed("evt1") is False
    db.mark_event_processed("evt1")
    assert db.is_event_processed("evt1") is True
    db.mark_event_processed("evt1")  # no error on duplicate
    assert db.is_event_processed("evt1") is True
