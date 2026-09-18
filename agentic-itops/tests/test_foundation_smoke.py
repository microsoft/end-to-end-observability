import json
from types import SimpleNamespace

import pytest

pytest.importorskip("azure.ai.projects", reason="Requires the optional Foundry deployment profile")
pytest.importorskip("openai", reason="Requires the optional Foundry deployment profile")

from scripts import smoke_foundation as smoke


PROJECT = "https://example.services.ai.azure.com/api/projects/pilot"
ACCOUNT = "https://example.openai.azure.com/"


@pytest.mark.parametrize("endpoint", [
    "http://example.services.ai.azure.com/api/projects/pilot",
    "https://example.services.ai.azure.com.attacker.test/api/projects/pilot",
    "https://user@example.services.ai.azure.com/api/projects/pilot",
    PROJECT + "?override=1",
    PROJECT + "/other",
    "https://example.services.ai.azure.com/api/projects/",
])
def test_invalid_project_endpoint_rejected(endpoint):
    with pytest.raises(ValueError):
        smoke.check_endpoint(endpoint)


@pytest.mark.parametrize("endpoint", [
    "http://example.openai.azure.com/",
    "https://example.openai.azure.com.attacker.test/",
    "https://user@example.openai.azure.com/",
    ACCOUNT + "openai/v1/",
    ACCOUNT + "?override=1",
])
def test_invalid_account_endpoint_rejected(endpoint):
    with pytest.raises(ValueError):
        smoke.check_openai_endpoint(endpoint)


def test_valid_endpoints():
    assert smoke.check_endpoint(PROJECT) == PROJECT
    assert smoke.check_openai_endpoint(ACCOUNT.rstrip("/")) == ACCOUNT


class Context:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def setup_probe(monkeypatch, tmp_path, vector=None):
    output = tmp_path / "receipt.json"
    calls = []
    usage = SimpleNamespace(model_dump=lambda **_: {"total_tokens": 10})

    class Client(Context):
        def __init__(self, endpoint):
            self.endpoint = endpoint
            self.responses = SimpleNamespace(create=self.respond)
            self.embeddings = SimpleNamespace(create=self.embed)

        def respond(self, **kwargs):
            calls.append(("responses", self.endpoint, kwargs))
            return SimpleNamespace(status="completed", output_text="FOUNDATION_READY",
                                   usage=usage, model="gpt-5.6-luna")

        def embed(self, **kwargs):
            calls.append(("embeddings", self.endpoint, kwargs))
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * 1536 if vector is None else vector)],
                model="text-embedding-3-small", usage=usage,
            )

    class Project(Context):
        def __init__(self, **kwargs):
            assert kwargs["endpoint"] == PROJECT
            assert kwargs["retry_total"] == 0

        def get_openai_client(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["timeout"] == 60.0
            return Client(kwargs.get("base_url", PROJECT))

    monkeypatch.setattr(smoke, "AzureCliCredential", lambda **_: Context())
    monkeypatch.setattr(smoke, "AIProjectClient", Project)
    monkeypatch.setattr("sys.argv", [
        "smoke_foundation", "--project-endpoint", PROJECT,
        "--openai-endpoint", ACCOUNT, "--tenant-id", "test-tenant",
        "--output", str(output),
    ])
    return output, calls


def test_probe_separates_project_responses_and_account_embeddings(monkeypatch, tmp_path):
    output, calls = setup_probe(monkeypatch, tmp_path)
    smoke.main()
    assert [(kind, endpoint) for kind, endpoint, _ in calls] == [
        ("responses", PROJECT), ("embeddings", ACCOUNT + "openai/v1/"),
    ]
    assert calls[0][2]["store"] is False
    assert calls[0][2]["max_output_tokens"] == 2048
    assert json.loads(output.read_text())["status"] == "passed"


def test_existing_receipt_prevents_any_model_request(monkeypatch, tmp_path):
    output, calls = setup_probe(monkeypatch, tmp_path)
    output.write_text("preserved")
    with pytest.raises(FileExistsError):
        smoke.main()
    assert not calls
    assert output.read_text() == "preserved"


@pytest.mark.parametrize("vector", [[0.1], [0.0] * 1536, [float("nan")] * 1536])
def test_invalid_vector_records_failure(monkeypatch, tmp_path, vector):
    output, _ = setup_probe(monkeypatch, tmp_path, vector)
    with pytest.raises(ValueError, match="Embedding vector"):
        smoke.main()
    assert json.loads(output.read_text())["status"] == "failed"


def test_cross_account_endpoints_rejected_before_calls(monkeypatch, tmp_path):
    output, calls = setup_probe(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", [
        "smoke_foundation", "--project-endpoint", PROJECT,
        "--openai-endpoint", "https://different.openai.azure.com/",
        "--tenant-id", "test-tenant", "--output", str(output),
    ])
    with pytest.raises(SystemExit):
        smoke.main()
    assert not calls
    assert not output.exists()
