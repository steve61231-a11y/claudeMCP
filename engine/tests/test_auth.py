import pytest
from fastapi import HTTPException

from engine.config import settings
from engine.main import require_internal_token


def test_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "secret")
    with pytest.raises(HTTPException) as exc:
        require_internal_token("wrong")
    assert exc.value.status_code == 401


def test_rejects_missing_token_header(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "secret")
    with pytest.raises(HTTPException) as exc:
        require_internal_token(None)
    assert exc.value.status_code == 401


def test_accepts_correct_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "secret")
    require_internal_token("secret")


def test_unset_token_fails_closed_outside_local_dev(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "")
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(HTTPException) as exc:
        require_internal_token(None)
    assert exc.value.status_code == 503


def test_unset_token_allowed_in_local_dev(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "")
    monkeypatch.setattr(settings, "app_env", "local-dev")
    require_internal_token(None)


def test_startup_validation_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "internal_api_token", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    with pytest.raises(RuntimeError):
        settings.validate_for_startup()
