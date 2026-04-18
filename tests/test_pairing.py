import json
import time
import pytest
from datetime import timedelta
from pathlib import Path
from goose_signal_gateway.pairing import PairingStore


@pytest.fixture
def store(tmp_path):
    return PairingStore(tmp_path / "pairing.json", code_ttl=timedelta(minutes=60))


def test_unknown_sender_gets_code(store):
    code = store.request_code("+1111")
    assert code is not None
    assert len(code) <= 6
    assert code == code.upper()


def test_repeat_before_approval_returns_none(store):
    store.request_code("+1111")
    assert store.request_code("+1111") is None


def test_approved_sender_bypasses(store):
    store.request_code("+1111")
    code = store.list_pending()[0].code
    store.approve(code)
    assert store.is_approved("+1111")


def test_allowed_users_are_pre_approved(tmp_path):
    s = PairingStore(tmp_path / "p.json", allowed_users=["+9999"])
    assert s.is_approved("+9999")


def test_expired_code_invalid(tmp_path):
    s = PairingStore(tmp_path / "p.json", code_ttl=timedelta(seconds=1))
    code = s.request_code("+2222")
    # Manually expire it
    p = s._pending[code]
    s._pending[code] = p.__class__(
        code=p.code, source=p.source, issued_at=p.issued_at, expires_at=time.time() - 1
    )
    assert s.approve(code) is None
    assert not s.is_approved("+2222")


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "p.json"
    s1 = PairingStore(path)
    code = s1.request_code("+3333")
    s1.approve(code)

    s2 = PairingStore(path)
    assert s2.is_approved("+3333")


def test_pending_persists_across_reload(tmp_path):
    path = tmp_path / "p.json"
    s1 = PairingStore(path)
    code = s1.request_code("+4444")

    s2 = PairingStore(path)
    assert s2.approve(code) == "+4444"


def test_atomic_write_file_always_valid_json(tmp_path):
    path = tmp_path / "p.json"
    s = PairingStore(path)
    s.request_code("+5555")
    data = json.loads(path.read_text())
    assert "approved" in data
    assert "pending" in data


def test_file_permissions(tmp_path):
    path = tmp_path / "p.json"
    s = PairingStore(path)
    s.request_code("+6666")
    assert (path.stat().st_mode & 0o777) == 0o600


def test_deny_removes_pending(store):
    code = store.request_code("+7777")
    assert store.deny(code) is True
    assert store.approve(code) is None


def test_revoke_removes_approved(store):
    code = store.request_code("+8888")
    store.approve(code)
    assert store.revoke_approval("+8888") is True
    assert not store.is_approved("+8888")


def test_list_pending_shows_open_codes(store):
    store.request_code("+1001")
    store.request_code("+1002")
    pending = store.list_pending()
    assert len(pending) == 2
    sources = {p.source for p in pending}
    assert sources == {"+1001", "+1002"}
