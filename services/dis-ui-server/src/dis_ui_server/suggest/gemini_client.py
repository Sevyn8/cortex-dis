"""The Gemini-backed mapping suggester, with the mechanical fallback built in.

Auth is Vertex AI / GCP-native: the suggester takes (project, location), constructs
``genai.Client(vertexai=True, project=..., location=...)``, and authenticates via Application
Default Credentials (the Cloud Run service account). There is no API key string.

Optional SA impersonation: when ``impersonate_sa`` is set, the Vertex calls (and ONLY those)
impersonate that service account (gemini-dis) via short-lived
``google.auth.impersonated_credentials`` minted from the ambient ADC; the service still runs as
its own SA for everything else. Unset -> the ambient ADC is used directly.

``GeminiSuggester.suggest`` returns ``(source, model, suggestions)``:

- project/location unset, or any model error/timeout/parse failure -> the mechanical
  ``fallback_matcher`` result with ``source="fallback"``. The frontend must always receive
  suggestions, so missing config or LLM trouble degrades, never raises.
- Vertex configured and a clean structured response -> ``source="llm"``, with every
  ``suggested_target`` and alternative VALIDATED against the catalog key set (the
  model cannot invent a field; invalid targets are nulled / dropped).

The blocking google-genai call runs off the event loop via ``anyio.to_thread``
with a bounded timeout (the GCS/Pub/Sub pattern). The google-genai import is
LAZY (inside ``_call_model``) so this module loads without the package; only the
real LLM path needs it. ``_call_model`` is the single network seam tests override.
"""

from __future__ import annotations

import json
from typing import Any

import anyio

from dis_core.logging import get_logger
from dis_ui_server.config import SERVICE_NAME
from dis_ui_server.schemas.mapping_fields import TemplateMappingField
from dis_ui_server.schemas.mapping_suggestions import ColumnProfile, Suggestion, SuggestionSource
from dis_ui_server.suggest.fallback_matcher import match_columns

_log = get_logger(SERVICE_NAME)

_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_TIMEOUT_S = 15.0
# The scope Vertex calls need; also the impersonation target scope.
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class GeminiSuggester:
    """Produces per-column mapping suggestions via Gemini, falling back mechanically."""

    def __init__(
        self,
        project: str | None,
        location: str | None,
        *,
        impersonate_sa: str | None = None,
        model: str = _DEFAULT_MODEL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        # Vertex AI (GCP-native) auth: no key string. project + location select the Vertex
        # backend; credentials come from Application Default Credentials (the Cloud Run service
        # account), not from a configured secret. Both unset -> mechanical fallback.
        # impersonate_sa (optional): impersonate this SA for the Vertex calls only; unset ->
        # ambient ADC used directly.
        self._project = project
        self._location = location
        self._impersonate_sa = impersonate_sa
        self._model = model
        self._timeout_s = timeout_s

    async def suggest(
        self,
        columns: list[ColumnProfile],
        catalog: list[TemplateMappingField],
    ) -> tuple[SuggestionSource, str | None, list[Suggestion]]:
        """Return (source, model, suggestions); never raises on LLM trouble."""
        if not self._project or not self._location:
            return ("fallback", None, match_columns(columns, catalog))
        try:
            prompt = self._build_prompt(columns, catalog)
            with anyio.fail_after(self._timeout_s):
                text = await anyio.to_thread.run_sync(self._call_model, prompt)
            suggestions = self._parse_and_validate(text, columns, catalog)
            return ("llm", self._model, suggestions)
        except Exception as exc:  # timeout, transport, parse, anything: degrade
            _log.bind(stage="mapping_suggestions", error=type(exc).__name__).warning(
                "gemini suggestion failed; using mechanical fallback"
            )
            return ("fallback", None, match_columns(columns, catalog))

    # -- the single network seam (lazy import; tests override this) -----------------

    def _call_model(self, prompt: str) -> str:
        """Blocking Gemini call returning the raw JSON text. Lazy-imports the SDK.

        Vertex AI mode: ``vertexai=True`` selects the Vertex backend with the given project +
        location. With ``impersonate_sa`` set, the credentials are short-lived impersonated
        credentials for that SA (minted from the ambient ADC via google.auth); otherwise the
        ambient ADC (the Cloud Run service account) is used directly. No API key. The
        generate_content + structured-output (response_mime_type) call is identical either way,
        so the prompt/parse/validation logic is unchanged.
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

    # -- pure helpers (directly testable) ------------------------------------------

    def _build_prompt(self, columns: list[ColumnProfile], catalog: list[TemplateMappingField]) -> str:
        """Compose the prompt: the catalog is the CLOSED target set, JSON out only."""
        targets = [
            {
                "key": field.key,
                "display_name": field.display_name,
                "datatype": field.datatype,
                "section": field.section,
                "description": field.description,
            }
            for field in catalog
        ]
        profile = [
            {
                "source_column": column.name,
                "inferred_datatype": column.inferred_datatype,
                "null_pct": column.null_pct,
                "sample_values": column.sample_values,
            }
            for column in columns
        ]
        instructions = (
            "You map source CSV columns to canonical retail fields. For EACH source "
            "column, choose the single best target from the allowed targets, or null if "
            "no target fits. You MUST only use a target 'key' from the allowed list; "
            "never invent a field. Return ONLY JSON of the shape: "
            '{"suggestions": [{"source_column": str, "suggested_target": str or null, '
            '"confidence": number 0..1, "reasoning": short string, '
            '"alternatives": [target key, ...]}]}.'
        )
        return json.dumps(
            {
                "instructions": instructions,
                "allowed_targets": targets,
                "columns": profile,
            }
        )

    def _parse_and_validate(
        self,
        text: str,
        columns: list[ColumnProfile],
        catalog: list[TemplateMappingField],
    ) -> list[Suggestion]:
        """Parse the model JSON and constrain every target to a real catalog key."""
        valid_keys = {field.key for field in catalog}
        parsed = json.loads(text)
        raw_items = parsed.get("suggestions", []) if isinstance(parsed, dict) else []
        by_column: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if isinstance(item, dict) and isinstance(item.get("source_column"), str):
                by_column[item["source_column"]] = item

        suggestions: list[Suggestion] = []
        for column in columns:
            item = by_column.get(column.name, {})
            target = item.get("suggested_target")
            # GUARDRAIL: only a real catalog key survives; anything else -> null.
            if not isinstance(target, str) or target not in valid_keys:
                target = None
            confidence = item.get("confidence", 0.0)
            confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
            confidence = min(1.0, max(0.0, confidence))
            reasoning = item.get("reasoning")
            reasoning = reasoning if isinstance(reasoning, str) and reasoning else None
            raw_alts = item.get("alternatives")
            alternatives: list[str] | None = None
            if isinstance(raw_alts, list):
                alts = [a for a in raw_alts if isinstance(a, str) and a in valid_keys]
                alternatives = alts or None
            suggestions.append(
                Suggestion(
                    source_column=column.name,
                    suggested_target=target,
                    confidence=confidence,
                    reasoning=reasoning,
                    alternatives=alternatives,
                )
            )
        return suggestions
