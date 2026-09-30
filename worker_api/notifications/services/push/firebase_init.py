import json
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials

from worker_api.config import get
from worker_api.notifications.services.push.config_loader import load_service_account


def initialize_firebase() -> firebase_admin.App:
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    account = load_service_account()
    if not account:
        raise ValueError(
            "Firebase is not configured. Set GOOGLE_APPLICATION_CREDENTIALS to a "
            "Firebase service account JSON file path or inline JSON."
        )

    cred = credentials.Certificate(account)
    project_id = get("GOOGLE_CLOUD_PROJECT").strip() or account.get("project_id", "")
    if not project_id:
        raise ValueError(
            "Firebase project ID is missing. Add project_id to the service account "
            "JSON or set GOOGLE_CLOUD_PROJECT."
        )

    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    raw_credentials = get("GOOGLE_APPLICATION_CREDENTIALS").strip()
    if raw_credentials and not raw_credentials.startswith("{"):
        credential_path = Path(raw_credentials)
        if credential_path.is_file():
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(credential_path.resolve()))

    return firebase_admin.initialize_app(cred, {"projectId": project_id})
