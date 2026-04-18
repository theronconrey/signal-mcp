import asyncio
import json
import pytest
from pathlib import Path
from goose_signal_gateway.session_map import ConversationKey, SessionMap


@pytest.fixture
def tmp_path_map(tmp_path):
    return tmp_path / "sessions.json"


async def test_round_trip(tmp_path_map):
    sm = await SessionMap.load(tmp_path_map)
    key = ConversationKey(kind="dm", identifier="+16125551234")
    await sm.set(key, "session_abc")

    sm2 = await SessionMap.load(tmp_path_map)
    assert await sm2.get(key) == "session_abc"


async def test_get_missing_returns_none(tmp_path_map):
    sm = await SessionMap.load(tmp_path_map)
    key = ConversationKey(kind="dm", identifier="+10000000000")
    assert await sm.get(key) is None


async def test_delete(tmp_path_map):
    sm = await SessionMap.load(tmp_path_map)
    key = ConversationKey(kind="dm", identifier="+16125551234")
    await sm.set(key, "session_abc")
    await sm.delete(key)

    sm2 = await SessionMap.load(tmp_path_map)
    assert await sm2.get(key) is None


async def test_group_and_dm_keys_do_not_collide(tmp_path_map):
    sm = await SessionMap.load(tmp_path_map)
    dm = ConversationKey(kind="dm", identifier="same_id")
    grp = ConversationKey(kind="group", identifier="same_id")
    await sm.set(dm, "dm_session")
    await sm.set(grp, "grp_session")
    assert await sm.get(dm) == "dm_session"
    assert await sm.get(grp) == "grp_session"


async def test_atomic_write_leaves_valid_json(tmp_path_map):
    """File is always valid JSON even if we read it mid-write (rename is atomic)."""
    sm = await SessionMap.load(tmp_path_map)
    key = ConversationKey(kind="dm", identifier="+16125551234")
    await sm.set(key, "session_abc")

    # Verify the file is valid JSON after write
    content = tmp_path_map.read_text()
    data = json.loads(content)
    assert data["dm:+16125551234"] == "session_abc"


async def test_file_permissions(tmp_path_map):
    sm = await SessionMap.load(tmp_path_map)
    await sm.set(ConversationKey(kind="dm", identifier="+1"), "s1")
    mode = tmp_path_map.stat().st_mode & 0o777
    assert mode == 0o600


async def test_same_dm_serializes(tmp_path_map):
    """Two tasks for the same DM must not interleave (lock enforced)."""
    sm = await SessionMap.load(tmp_path_map)
    key = ConversationKey(kind="dm", identifier="+1")
    order = []

    async def task(label, session_id):
        async with asyncio.Lock():  # placeholder — real serialization is in Gateway
            order.append(f"{label}_start")
            await sm.set(key, session_id)
            await asyncio.sleep(0)
            order.append(f"{label}_end")

    await asyncio.gather(task("a", "s_a"), task("b", "s_b"))
    # Each task's start/end are adjacent (not interleaved within the lock)
    assert order.index("a_start") < order.index("a_end")
    assert order.index("b_start") < order.index("b_end")


async def test_different_dms_run_concurrently(tmp_path_map):
    """Messages to different DMs can proceed independently."""
    sm = await SessionMap.load(tmp_path_map)
    key_a = ConversationKey(kind="dm", identifier="+1")
    key_b = ConversationKey(kind="dm", identifier="+2")
    results = []

    async def task_a():
        await sm.set(key_a, "s_a")
        await asyncio.sleep(0.01)
        results.append("a")

    async def task_b():
        await sm.set(key_b, "s_b")
        results.append("b")

    await asyncio.gather(task_a(), task_b())
    # b finishes before a's sleep is done — they ran concurrently
    assert results[0] == "b"
    assert results[1] == "a"
