import asyncio
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from hollerback.approvals import ApprovalCoordinator, _TIMEOUT_MESSAGE, _WAITING_MESSAGE, _DESKTOP_MESSAGE
from hollerback.session_map import ConversationKey


def make_coordinator(timeout_seconds=30):
    signal = MagicMock()
    signal.send = AsyncMock()
    acp = MagicMock()
    acp.resolve_permission = AsyncMock(side_effect=NotImplementedError)
    coord = ApprovalCoordinator(signal, acp, timeout=timedelta(seconds=timeout_seconds))
    return coord, signal, acp


KEY = ConversationKey(kind="dm", identifier="+1111")


async def test_permission_request_sends_signal_message():
    coord, signal, _ = make_coordinator()

    async def approve_after():
        await asyncio.sleep(0.01)
        await coord.handle_reply(KEY, "yes")

    asyncio.create_task(approve_after())
    result = await coord.request("s1", "r1", KEY, "bash", {"cmd": "ls"})

    assert result is True
    signal.send.assert_called()
    first_call_text = signal.send.call_args_list[0].args[1]
    assert "bash" in first_call_text
    assert "yes" in first_call_text.lower()


async def test_yes_reply_resolves_allow():
    coord, _, _ = make_coordinator()

    async def approve():
        await asyncio.sleep(0.01)
        await coord.handle_reply(KEY, "yes")

    asyncio.create_task(approve())
    result = await coord.request("s1", "r1", KEY, "tool", {})
    assert result is True


async def test_no_reply_resolves_deny():
    coord, _, _ = make_coordinator()

    async def deny():
        await asyncio.sleep(0.01)
        await coord.handle_reply(KEY, "no")

    asyncio.create_task(deny())
    result = await coord.request("s1", "r1", KEY, "tool", {})
    assert result is False


async def test_timeout_resolves_deny_and_sends_message():
    coord, signal, _ = make_coordinator(timeout_seconds=0.05)
    result = await coord.request("s1", "r1", KEY, "tool", {})

    assert result is False
    texts = [call.args[1] for call in signal.send.call_args_list]
    assert any(_TIMEOUT_MESSAGE in t for t in texts)


async def test_unrelated_reply_during_wait_sends_waiting_message():
    coord, signal, _ = make_coordinator()

    async def sequence():
        await asyncio.sleep(0.01)
        await coord.handle_reply(KEY, "maybe")   # not yes/no
        await asyncio.sleep(0.01)
        await coord.handle_reply(KEY, "yes")     # real answer

    asyncio.create_task(sequence())
    result = await coord.request("s1", "r1", KEY, "tool", {})

    assert result is True
    texts = [call.args[1] for call in signal.send.call_args_list]
    assert any(_WAITING_MESSAGE in t for t in texts)


async def test_external_resolution_cancels_pending_wait():
    coord, signal, _ = make_coordinator()

    async def external():
        await asyncio.sleep(0.01)
        await coord.handle_external_resolution("s1", "r1", allow=True)

    asyncio.create_task(external())
    result = await coord.request("s1", "r1", KEY, "tool", {})

    assert result is True
    texts = [call.args[1] for call in signal.send.call_args_list]
    assert any(_DESKTOP_MESSAGE in t for t in texts)


async def test_handle_reply_no_pending_returns_false():
    coord, _, _ = make_coordinator()
    result = await coord.handle_reply(KEY, "yes")
    assert result is False


async def test_resolve_permission_not_implemented_silently_skipped():
    """ACP resolve_permission raising NotImplementedError should not propagate."""
    coord, _, acp = make_coordinator()
    acp.resolve_permission = AsyncMock(side_effect=NotImplementedError)

    async def approve():
        await asyncio.sleep(0.01)
        await coord.handle_reply(KEY, "yes")

    asyncio.create_task(approve())
    # Should complete without raising
    result = await coord.request("s1", "r1", KEY, "tool", {})
    assert result is True
