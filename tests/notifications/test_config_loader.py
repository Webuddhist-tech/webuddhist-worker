import json
from unittest.mock import patch

from worker_api.notifications.services.push.config_loader import (
    is_fcm_configured,
    load_service_account,
)


class TestLoadServiceAccount:
    def test_returns_empty_when_unset(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        assert load_service_account() == {}

    def test_loads_inline_json(self, monkeypatch):
        account = {
            "type": "service_account",
            "project_id": "webuddhist-app",
            "private_key_id": "key-id",
            "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            "client_email": "firebase@webuddhist-app.iam.gserviceaccount.com",
            "client_id": "123",
        }
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", json.dumps(account))
        loaded = load_service_account()
        assert loaded["project_id"] == "webuddhist-app"

    def test_loads_from_file(self, monkeypatch, tmp_path):
        account = {
            "type": "service_account",
            "project_id": "webuddhist-app",
            "private_key_id": "key-id",
            "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
            "client_email": "firebase@webuddhist-app.iam.gserviceaccount.com",
            "client_id": "123",
        }
        credential_file = tmp_path / "firebase.json"
        credential_file.write_text(json.dumps(account), encoding="utf-8")
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(credential_file))
        loaded = load_service_account()
        assert loaded["project_id"] == "webuddhist-app"


class TestIsFcmConfigured:
    def test_false_when_credentials_missing(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
        assert is_fcm_configured() is False

    def test_true_when_project_override_set(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "webuddhist-app")
        assert is_fcm_configured() is True

    @patch("worker_api.notifications.services.push.config_loader.load_service_account")
    def test_true_when_service_account_has_project_id(self, mock_load, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "")
        mock_load.return_value = {"project_id": "webuddhist-app"}
        assert is_fcm_configured() is True
