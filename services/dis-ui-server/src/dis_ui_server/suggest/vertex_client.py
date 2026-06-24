"""Shared Vertex AI generative boundary for dis-ui-server.

The transport/auth scaffold lifted out of the mapping suggester so the existing
``/mapping-suggestions`` path and the future Atlas inference path share ONE
generative boundary instead of duplicating it:

- ``VertexClient.generate_json`` is the single blocking network call: it constructs
  ``genai.Client(vertexai=True, ...)`` (Vertex AI, GCP-native auth via Application
  Default Credentials, no API key), optionally impersonating a dedicated service
  account, and runs ``generate_content`` with structured-JSON output. The
  google-genai import is LAZY (inside the method) so this module loads without the
  package; only the real LLM path needs it.
- ``run_blocking`` is the shared off-the-event-loop wrapper: it runs a blocking
  ``str -> str`` callable via ``anyio.to_thread`` under a bounded ``fail_after``
  timeout. It takes the callable as an argument so a caller can keep its own
  overridable ``_call_model`` seam (the seam tests pin) while sharing this scaffold.

Both callers delegate their ``_call_model`` seam to ``VertexClient.generate_json``
and await ``run_blocking(self._call_model, ...)``, so the network seam stays
overridable on the caller exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable

import anyio

# The scope Vertex calls need; also the impersonation target scope.
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class VertexClient:
    """The Vertex AI transport: structured-JSON ``generate_content`` over ADC."""

    def __init__(
        self,
        project: str | None,
        location: str | None,
        *,
        model: str,
        impersonate_sa: str | None = None,
    ) -> None:
        # Vertex AI (GCP-native) auth: no key string. project + location select the
        # Vertex backend; credentials come from Application Default Credentials (the
        # Cloud Run service account). impersonate_sa (optional): impersonate this SA
        # for the Vertex calls only; unset -> ambient ADC used directly.
        self._project = project
        self._location = location
        self._model = model
        self._impersonate_sa = impersonate_sa

    @property
    def configured(self) -> bool:
        """True iff a Vertex backend is selectable (project + location both set)."""
        return bool(self._project and self._location)

    def generate_json(self, prompt: str) -> str:
        """Blocking Gemini call returning the raw JSON text. Lazy-imports the SDK.

        Vertex AI mode: ``vertexai=True`` selects the Vertex backend with the given
        project + location. With ``impersonate_sa`` set, the credentials are
        short-lived impersonated credentials for that SA (minted from the ambient
        ADC via google.auth); otherwise the ambient ADC (the Cloud Run service
        account) is used directly. No API key.
        """
        import google.genai as genai
        from google.genai import types

        if self._impersonate_sa:
            import google.auth
            from google.auth import impersonated_credentials

            source_credentials, _ = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
            # google.auth's impersonated_credentials.Credentials is not type-annotated, so the
            # strict-typed call is flagged; the args are exactly the documented Vertex pattern.
            credentials = impersonated_credentials.Credentials(  # type: ignore[no-untyped-call]
                source_credentials=source_credentials,
                target_principal=self._impersonate_sa,
                target_scopes=[_CLOUD_PLATFORM_SCOPE],
            )
            client = genai.Client(
                vertexai=True, project=self._project, location=self._location, credentials=credentials
            )
        else:
            client = genai.Client(vertexai=True, project=self._project, location=self._location)
        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return response.text or ""


async def run_blocking(call: Callable[[str], str], prompt: str, *, timeout_s: float) -> str:
    """Run a blocking ``str -> str`` call off the event loop under a timeout.

    ``call`` is passed in (rather than fixed) so a caller can keep its own
    overridable ``_call_model`` seam and still share this scaffold.
    """
    with anyio.fail_after(timeout_s):
        return await anyio.to_thread.run_sync(call, prompt)
