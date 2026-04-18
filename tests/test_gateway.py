"""
Gateway integration tests using mock SignalClient and AcpClient.
"""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

from goose_signal_gateway.gateway import Gateway
from goose_signal_gateway.acp_client import SessionNotification
from goose_signal_gateway.signal_client import IncomingMessage


def make_notif(kind, session_id="s1", **payload):
    return SessionNotification(kind=kind, session_id=session_id, payload=payload)


def chunk(text, msg_id="m1"):
    return make_notif("agent_message_chunk", id=msg_id, content=[{"type": "text", "text": text}])


def complete():
    return make_notif("session_complete")


async def _stream(*notifs):
    for n in notifs:
        yield n


def build_gateway(tmp_path, pairing_enabled=False, **kwargs) -> Gateway:
    return Gateway(
        signal_account="+10000000000",
        session_map_path=tmp_path / "sessions.json",
        pairing_path=tmp_path / "pairing.json",
        pairing_enabled=pairing_enabled,
        **kwargs,
    )


def mock_signal():
    s = MagicMock()
    s.send = AsyncMock(return_value=1000)
    s.edit_message = AsyncMock()
    s.send_typing = AsyncMock()
    s.send_read_receipt = AsyncMock()
    s.close = AsyncMock()
    return s


def mock_acp(notifs):
    a = MagicMock()
    a.initialize = AsyncMock()
    a.close = AsyncMock()
    a.session_new = AsyncMock(return_value="s1")
    a.session_prompt = MagicMock(side_effect=lambda sid, text: _stream(*notifs))
    return a


async def drive(gw, signal, acp, msgs):
    """Wire mocks into gateway and drive messages through _handle."""
    gw._signal = signal
    gw._acp = acp
    from goose_signal_gateway.approvals import ApprovalCoordinator
    gw._approvals = ApprovalCoordinator(signal, acp)
    from goose_signal_gateway.session_map import SessionMap
    gw._sessions = await SessionMap.load(gw._session_map_path)
    for msg in msgs:
        await gw._handle(msg)


def msg(sender, text, ts=1234):
    return IncomingMessage(sender=sender, text=text, timestamp=ts)


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_message_in_reply_out(tmp_path):
    gw = build_gateway(tmp_path)
    signal = mock_signal()
    acp = mock_acp([chunk("Hello"), chunk(" world"), complete()])

    await drive(gw, signal, acp, [msg("+1111", "hi")])

    # typing sent and stopped
    signal.send_typing.assert_any_call("+1111")
    signal.send_typing.assert_any_call("+1111", stop=True)
    # final reply sent as a message (no placeholder)
    sent_texts = [c.args[1] for c in signal.send.call_args_list]
    assert any("Hello world" in t for t in sent_texts)
    # no orphaned placeholder
    assert "…" not in sent_texts


async def test_two_messages_same_dm_serialise(tmp_path):
    order = []
    gw = build_gateway(tmp_path)
    signal = mock_signal()

    async def slow_stream(sid, text):
        order.append(f"{text}_start")
        await asyncio.sleep(0.02)
        order.append(f"{text}_end")
        yield chunk(f"reply to {text}")
        yield complete()

    acp = mock_acp([])
    acp.session_prompt = slow_stream

    await drive(gw, signal, acp, [])

    t1 = asyncio.create_task(gw._handle(msg("+1111", "first", ts=1)))
    t2 = asyncio.create_task(gw._handle(msg("+1111", "second", ts=2)))
    await asyncio.gather(t1, t2)

    # second must start after first ends
    assert order.index("first_end") < order.index("second_start")


async def test_two_messages_different_dms_concurrent(tmp_path):
    order = []
    gw = build_gateway(tmp_path)
    signal = mock_signal()

    async def slow_stream(sid, text):
        order.append(f"{text}_start")
        await asyncio.sleep(0.02)
        order.append(f"{text}_end")
        yield chunk(f"reply to {text}")
        yield complete()

    acp = mock_acp([])
    acp.session_prompt = slow_stream

    await drive(gw, signal, acp, [])

    t1 = asyncio.create_task(gw._handle(msg("+1111", "from_a", ts=1)))
    t2 = asyncio.create_task(gw._handle(msg("+2222", "from_b", ts=2)))
    await asyncio.gather(t1, t2)

    # both started before either ended
    assert order.index("from_a_start") < order.index("from_b_end")
    assert order.index("from_b_start") < order.index("from_a_end")


async def test_unknown_sender_triggers_pairing(tmp_path):
    gw = build_gateway(tmp_path, pairing_enabled=True)
    signal = mock_signal()
    acp = mock_acp([])

    await drive(gw, signal, acp, [msg("+9999", "hello")])

    texts = [c.args[1] for c in signal.send.call_args_list]
    assert any("pairing" in t.lower() or "approve" in t for t in texts)
    acp.session_prompt.assert_not_called()


async def test_duplicate_dropped(tmp_path):
    gw = build_gateway(tmp_path)
    signal = mock_signal()
    acp = mock_acp([chunk("hi"), complete()])

    await drive(gw, signal, acp, [
        msg("+1111", "hello", ts=500),
        msg("+1111", "hello", ts=500),  # exact duplicate
    ])

    assert acp.session_prompt.call_count == 1


async def test_acp_stream_interrupted_reports_error(tmp_path):
    from goose_signal_gateway.acp_client import AcpStreamInterruptedError

    gw = build_gateway(tmp_path)
    signal = mock_signal()

    async def broken_stream(sid, text):
        yield chunk("partial")
        raise AcpStreamInterruptedError("connection reset")

    acp = mock_acp([])
    acp.session_prompt = broken_stream

    await drive(gw, signal, acp, [msg("+1111", "hi")])

    sent = [c.args[1] for c in signal.send.call_args_list]
    assert any("lost" in t.lower() or "try again" in t.lower() for t in sent)
    signal.send_typing.assert_any_call("+1111", stop=True)
