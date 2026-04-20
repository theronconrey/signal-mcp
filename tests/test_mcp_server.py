import pytest

from hollerback.mcp_server import MultiKeyTokenVerifier


@pytest.fixture
def single_agent():
    return MultiKeyTokenVerifier([("alice", "secret-alice-key")])


@pytest.fixture
def two_agents():
    return MultiKeyTokenVerifier([
        ("alice", "secret-alice-key"),
        ("bob", "secret-bob-key"),
    ])


async def test_wrong_token_rejected(single_agent):
    result = await single_agent.verify_token("not-the-right-key")
    assert result is None


async def test_right_token_accepted(single_agent):
    result = await single_agent.verify_token("secret-alice-key")
    assert result is not None
    assert result.client_id == "alice"


async def test_empty_string_rejected(single_agent):
    result = await single_agent.verify_token("")
    assert result is None


async def test_multi_agent_correct_agent_identified(two_agents):
    result = await two_agents.verify_token("secret-bob-key")
    assert result is not None
    assert result.client_id == "bob"


async def test_multi_agent_wrong_token_rejected(two_agents):
    result = await two_agents.verify_token("not-a-valid-key")
    assert result is None


async def test_prefix_of_valid_key_rejected(single_agent):
    result = await single_agent.verify_token("secret-alice")
    assert result is None
