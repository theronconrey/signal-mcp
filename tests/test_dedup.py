import asyncio
import pytest
from hollerback.dedup import MessageDeduplicator


@pytest.mark.asyncio
async def test_first_call_returns_false():
    d = MessageDeduplicator()
    assert await d.seen(1000, "hello", "+1111") is False


@pytest.mark.asyncio
async def test_second_call_returns_true():
    d = MessageDeduplicator()
    await d.seen(1000, "hello", "+1111")
    assert await d.seen(1000, "hello", "+1111") is True


@pytest.mark.asyncio
async def test_expires_after_ttl(monkeypatch):
    import time
    d = MessageDeduplicator(ttl_seconds=1)
    await d.seen(1000, "hello", "+1111")

    # Advance monotonic clock past TTL
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 2)

    assert await d.seen(1000, "hello", "+1111") is False


@pytest.mark.asyncio
async def test_evicts_oldest_at_max():
    d = MessageDeduplicator(max_entries=2)
    await d.seen(1, "a", "+1")
    await d.seen(2, "b", "+1")
    await d.seen(3, "c", "+1")  # evicts entry for (1, "a", "+1")
    # oldest is gone — seen again returns False
    assert await d.seen(1, "a", "+1") is False


@pytest.mark.asyncio
async def test_remember_outbound_deduplicates_echo():
    d = MessageDeduplicator()
    await d.remember_outbound(5000, "reply text", "+bot")
    assert await d.seen(5000, "reply text", "+bot") is True


@pytest.mark.asyncio
async def test_different_source_same_text_and_timestamp_are_distinct():
    d = MessageDeduplicator()
    assert await d.seen(1000, "hello", "+1111") is False
    assert await d.seen(1000, "hello", "+2222") is False
