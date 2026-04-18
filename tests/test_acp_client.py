import pytest
import respx
import httpx
from goose_signal_gateway.acp_client import (
    AcpClient,
    AcpStreamInterruptedError,
    SessionNotification,
)
from goose_signal_gateway.goosed_client import GoosedConfig

BASE = "https://127.0.0.1:19999"
CONFIG = GoosedConfig(port=19999, secret="test-secret")


def make_client():
    return AcpClient(CONFIG)


def sse(*events: str) -> bytes:
    """Build a raw SSE response body from JSON event strings."""
    lines = "".join(f"data: {e}\n\n" for e in events)
    return lines.encode()


@respx.mock
async def test_initialize_success():
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(200, text="ok"))
    async with make_client() as client:
        result = await client.initialize()
    assert result.healthy is True
    assert "19999" in result.server_url


@respx.mock
async def test_initialize_failure_raises():
    respx.get(f"{BASE}/status").mock(return_value=httpx.Response(503, text="down"))
    client = make_client()
    with pytest.raises(RuntimeError, match="health check failed"):
        await client.initialize()
    await client.close()


@respx.mock
async def test_session_new_returns_id():
    respx.post(f"{BASE}/agent/start").mock(
        return_value=httpx.Response(200, json={"id": "20260418_5", "working_dir": "/home/user"})
    )
    respx.post(f"{BASE}/agent/update_provider").mock(
        return_value=httpx.Response(200, json={})
    )
    client = make_client()
    sid = await client.session_new(cwd="/home/user")
    assert sid == "20260418_5"
    await client.close()


@respx.mock
async def test_session_new_metadata_accepted_without_error():
    respx.post(f"{BASE}/agent/start").mock(
        return_value=httpx.Response(200, json={"id": "s1", "working_dir": "/home/user"})
    )
    respx.post(f"{BASE}/agent/update_provider").mock(
        return_value=httpx.Response(200, json={})
    )
    client = make_client()
    sid = await client.session_new(
        cwd="/home/user",
        metadata={"source": "signal", "display_name": "Signal: +1"},
    )
    assert sid == "s1"
    await client.close()


@respx.mock
async def test_session_prompt_yields_chunks_in_order():
    import json
    chunk1 = json.dumps({"type": "Message", "message": {"id": "m1", "content": [{"type": "text", "text": "Hello"}]}})
    chunk2 = json.dumps({"type": "Message", "message": {"id": "m1", "content": [{"type": "text", "text": " world"}]}})
    finish = json.dumps({"type": "Finish", "reason": "stop"})
    body = sse(chunk1, chunk2, finish)

    respx.post(f"{BASE}/reply").mock(
        return_value=httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )
    client = make_client()
    notifs = []
    async for n in client.session_prompt("s1", "hi"):
        notifs.append(n)
    await client.close()

    chunks = [n for n in notifs if n.kind == "agent_message_chunk"]
    complete = [n for n in notifs if n.kind == "session_complete"]
    assert len(chunks) == 2
    assert chunks[0].payload["content"][0]["text"] == "Hello"
    assert chunks[1].payload["content"][0]["text"] == " world"
    assert len(complete) == 1


@respx.mock
async def test_session_prompt_raises_on_stream_interrupted():
    import json

    async def broken_stream(request):
        yield b"data: " + json.dumps({"type": "Ping"}).encode() + b"\n\n"
        raise httpx.RemoteProtocolError("connection reset", request=request)

    respx.post(f"{BASE}/reply").mock(side_effect=httpx.RemoteProtocolError("connection reset"))

    client = make_client()
    with pytest.raises(AcpStreamInterruptedError):
        async for _ in client.session_prompt("s1", "hi"):
            pass
    await client.close()


@respx.mock
async def test_auth_token_sent_in_header():
    respx.post(f"{BASE}/agent/start").mock(
        return_value=httpx.Response(200, json={"id": "s2", "working_dir": "/"})
    )
    respx.post(f"{BASE}/agent/update_provider").mock(
        return_value=httpx.Response(200, json={})
    )
    client = make_client()
    await client.session_new(cwd="/")
    req = respx.calls.last.request
    assert req.headers.get("x-secret-key") == "test-secret"
    await client.close()


@respx.mock
async def test_list_sessions():
    import json
    respx.get(f"{BASE}/sessions").mock(
        return_value=httpx.Response(200, json={
            "sessions": [
                {"id": "s1", "name": "chat", "working_dir": "/home"},
                {"id": "s2", "name": "code", "working_dir": "/tmp"},
            ]
        })
    )
    client = make_client()
    sessions = await client.list_sessions()
    assert len(sessions) == 2
    assert sessions[0].id == "s1"
    assert sessions[1].working_dir == "/tmp"
    await client.close()


async def test_session_load_raises_not_implemented():
    client = make_client()
    with pytest.raises(NotImplementedError):
        async for _ in client.session_load("s1"):
            pass
    await client.close()


async def test_resolve_permission_raises_not_implemented():
    client = make_client()
    with pytest.raises(NotImplementedError):
        await client.resolve_permission("s1", "req1", allow=True)
    await client.close()
