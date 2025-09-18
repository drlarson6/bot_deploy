# translate_helper.py  (v3 of Google Cloud Translate)

import os
from typing import Optional

try:
    from google.cloud import translate  # v3 package
except Exception:  # library not installed / import fails
    translate = None  # we'll raise a clear error if used

def translate_text(
    text: str,
    target_language: str,
    project_id: Optional[str] = None,
    location: str = "global",
    mime_type: str = "text/plain",
) -> str:
    """
    Translate `text` to `target_language` using Google Cloud Translate v3.
    `project_id` defaults to GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT env if not passed.
    """
    if translate is None:
        raise RuntimeError(
            "google-cloud-translate v3 is not installed. "
            "Add `google-cloud-translate>=3.0.0` to requirements."
        )

    if not project_id:
        project_id = (
            os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCLOUD_PROJECT")
        )
    if not project_id:
        raise RuntimeError(
            "Project ID not found. Set GOOGLE_CLOUD_PROJECT (or GCLOUD_PROJECT)."
        )

    client = translate.TranslationServiceClient()
    parent = f"projects/{project_id}/locations/{location}"

    resp = client.translate_text(
        contents=[text],
        target_language_code=target_language,
        parent=parent,
        mime_type=mime_type,
    )
    return resp.translations[0].translated_text