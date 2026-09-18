import pytest

from agentic_itops.foundry_contracts import AgentVersionReference


def test_explicit_agent_version_body():
    assert AgentVersionReference("ops-orchestrator", "12").extra_body() == {
        "agent_reference": {"type": "agent_reference", "name": "ops-orchestrator", "version": "12"}
    }


@pytest.mark.parametrize("version", ["", "latest", "0", "-1", "1.0", "01", None, 1, True])
def test_rejects_unpinned_versions(version):
    with pytest.raises(ValueError, match="version"):
        AgentVersionReference("ops-orchestrator", version)


@pytest.mark.parametrize("name", ["", "../other", "https://other.invalid", None, "a" * 129])
def test_rejects_invalid_agent_names(name):
    with pytest.raises(ValueError, match="name"):
        AgentVersionReference(name, "1")
