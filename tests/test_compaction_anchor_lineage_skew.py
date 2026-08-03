from types import SimpleNamespace

from api.models import _state_db_anchor_index, reconciled_state_db_messages_for_session


def _anchor(ts=100.0, text="Boundary answer"):
    return {"role": "assistant", "ts": ts, "text": text, "attachments": 0}


def _message(role, content, timestamp, **extra):
    return {"role": role, "content": content, "timestamp": timestamp, **extra}


def test_anchor_accepts_unique_sidecar_state_db_timestamp_skew():
    rows = [
        _message("assistant", "Boundary answer", 96.49),
        _message("assistant", "Boundary answer", 96.49),
    ]

    assert _state_db_anchor_index(rows, _anchor()) == 1


def test_anchor_rejects_ambiguous_fuzzy_timestamp_clusters():
    rows = [
        _message("assistant", "Repeated boundary", 96.5),
        _message("assistant", "Repeated boundary", 103.0),
    ]

    assert _state_db_anchor_index(rows, _anchor(text="Repeated boundary")) is None


def test_continuation_verifies_parent_anchor_without_replaying_parent_history():
    marker = _message(
        "assistant",
        "[CONTEXT COMPACTION — REFERENCE ONLY] compact summary",
        110.0,
        _compressed_summary=True,
    )
    retained = _message("assistant", "Retained tail", 120.0)
    fresh = _message("user", "Fresh follow-up", 140.0)
    session = SimpleNamespace(
        session_id="continuation-child",
        parent_session_id="compression-parent",
        profile=None,
        is_cli_session=False,
        read_only=False,
        messages=[marker, retained],
        context_messages=[marker, retained],
        compression_anchor_message_key=_anchor(),
        truncation_watermark=None,
        truncation_boundary=None,
    )
    current_segment_rows = [
        _message("user", "Must remain compacted out", 90.0),
        fresh,
    ]
    lineage_rows = [
        _message("user", "Old parent history", 80.0),
        _message("assistant", "Boundary answer", 96.49),
        fresh,
    ]

    reconciled = reconciled_state_db_messages_for_session(
        session,
        prefer_context=True,
        state_messages=current_segment_rows,
        lineage_state_messages=lineage_rows,
    )

    assert [message["content"] for message in reconciled] == [
        marker["content"],
        retained["content"],
        fresh["content"],
    ]
