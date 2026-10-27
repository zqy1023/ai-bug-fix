from dataclasses import replace

import httpx

from app.infrastructure.config import ClaudeConfig, load_config
from app.infrastructure.models import _models_url, _probe_provider_models, model_options, validate_policy_models
from app.workflow.policy import AgentPolicy


def test_models_url_handles_anthropic_v1_base_url():
    assert _models_url("https://provider.example/v1") == "https://provider.example/v1/models"
    assert _models_url("https://provider.example") == "https://provider.example/v1/models"


def test_model_catalog_uses_provider_directory(monkeypatch):
    config = load_config()
    claude = ClaudeConfig(
        auth_token="token",
        base_url="https://provider.example/v1",
        model="configured-default",
        models=("configured-extra",),
    )

    def fake_get(url, **kwargs):
        assert url == "https://provider.example/v1/models"
        assert kwargs["headers"]["x-api-key"] == "token"
        return httpx.Response(
            200,
            json={"data": [{"id": "provider-model", "display_name": "Provider Model"}]},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.infrastructure.models.httpx.get", fake_get)
    runtime = replace(config, claude=claude)
    options = model_options(runtime)
    assert any(item.key == "provider-model" and item.available for item in options)
    assert any(item.key == "configured-default" and not item.available for item in options)
    assert options[0].label == options[0].model_id


def test_policy_rejects_model_not_returned_by_provider(monkeypatch):
    config = load_config()
    claude = ClaudeConfig(auth_token="token-2", base_url="https://provider.example/v1", model="usable-model")
    monkeypatch.setattr(
        "app.infrastructure.models.httpx.get",
        lambda url, **kwargs: httpx.Response(
            200,
            json={"data": [{"id": "usable-model"}]},
            request=httpx.Request("GET", url),
        ),
    )
    runtime = replace(config, claude=claude)
    validate_policy_models(
        AgentPolicy(
            analysis_model="usable-model",
            repair_model="usable-model",
            review_model="usable-model",
        ),
        runtime,
    )
    try:
        validate_policy_models(AgentPolicy(analysis_model="missing-model"), runtime)
    except ValueError as error:
        assert "missing-model" in str(error)
    else:
        raise AssertionError("未返回的模型必须被拒绝")
