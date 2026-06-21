"""Wire shapes for the ``/mapping-templates`` resource (list, detail, create, patch).

The resource is the TEMPLATE (a lineage of versions, D68); versions are nested
data. ``mapping_rules`` is served as the RAW validated D49 document — the
``dis_mapping.SourceMapping`` model itself is the wire type, so the serve-shape
and the validate-shape are one model and cannot drift, and an edit round-trips
the exact locale/format declarations instead of forcing the UI to re-invent
them (never-default locale). Requests carry ``mapping_rules`` as a plain dict;
the handler validates explicitly via ``mapping_validation.validate_mapping_rules``
(a malformed document is a 400 ``MappingConfigError`` through the §2.3 envelope,
never a 500 from inside body parsing and never a stored-invalid-config write).

Status vocabulary is lowercased on the wire (§2.6); DRAFT IS surfaced on this
template surface (it is the editable head of a lineage) — a deliberate,
documented supersession of the older version-list rule (API_CONTRACT §7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dis_mapping import SourceMapping

MappingTemplateStatus = Literal["draft", "staged", "active", "deprecated"]


class MappingTemplateVersion(BaseModel):
    """One version row of the lineage, rules served raw (D49)."""

    mapping_version_id: int  # global BIGSERIAL — the D22 canonical-row pin / audit reference
    version: int  # = version_seq_per_source (per-template counter, §2.6 wire name)
    status: MappingTemplateStatus
    mapping_rules: SourceMapping
    field_count: int  # len(rename) — display convenience, not a lossy rules rendering
    transform_count: int  # entries across normalize + cast + derive
    predecessor_version_id: int | None
    created_at: datetime
    created_by_user_id: str | None  # raw UUID string or null (Blocker 5 / D56 pending)
    activated_at: datetime | None
    deprecated_at: datetime | None


class MappingTemplate(BaseModel):
    """Lineage summary — one list entry per template."""

    template_id: str  # UUID, lowercase string
    source_id: str
    template_name: str
    template_type: str  # packet axis (Slice 14d); lineage-fixed, set at creation
    latest_version: int
    active_version: int | None
    staged_version: int | None
    draft_version: int | None
    versions_count: int
    created_at: datetime  # earliest version's created_at (template birth)
    latest_version_created_at: datetime


class MappingTemplateDetail(MappingTemplate):
    """The template with its full version lineage (version desc, DRAFT + DEPRECATED included)."""

    versions: list[MappingTemplateVersion]


class MappingColumn(BaseModel):
    """One source-to-destination column declaration (Slice 16a request shape).

    Carries semantic intent plus source-format declarations, NOT engine ops. The
    backend re-derives every catalog/sink fact from ``template_type`` + ``dest_key``
    (investigation P1-P4), so the request never echoes the sink object. Strict
    (``extra="forbid"``): a malformed or unknown key is a 422 surfaced early, never
    silently dropped."""

    model_config = ConfigDict(extra="forbid")

    src_key: str = Field(min_length=1)  # source column header, as it appears in the file
    dest_key: str = Field(min_length=1)  # catalog `key` for this template_type, or "__ignore__"
    # Format declarations: present ONLY when needed; 16a checks they are well-formed IF
    # present, never whether one is REQUIRED for a given dest_key (that is 16c). The
    # datetime format is a free str on purpose — token validity is semantic (16c).
    src_datetime_format: str | None = None  # e.g. "DD-MM-YYYY"
    src_decimal_separator: Literal[".", ","] | None = None
    src_thousand_separator: Literal[".", ",", "'"] | None = None
    src_is_percentage: bool | None = None  # true only when the source is a percentage


class MappingTemplateCreate(BaseModel):
    """``POST /mapping-templates`` body (Slice 16a). Semantic intent per column; the
    handler shape-validates and returns a SYNTHETIC 201 (no persistence, no
    ``mapping_rules`` assembly — both land in 16c). ``source_id`` is validated
    well-formed only (no source registry exists, a deliberate slice limit)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z0-9_]{1,128}$")
    template_name: str = Field(min_length=1, max_length=200)
    # The packet axis (Slice 14d). Validated against the in-code vocabulary in the
    # handler (a clean 400 InvalidTemplateTypeError, not a pydantic 422). Lineage-fixed
    # at creation thereafter.
    template_type: str
    columns: list[MappingColumn] = Field(min_length=1)
    # PLATFORM impersonation only (Slice 17b): the acted-for tenant (internal UUID).
    # Honoured ONLY on a verified PLATFORM token via resolve_acted_for(); a TENANT request
    # carrying it is REJECTED (403), never silently ignored. The discriminator is the
    # verified token user_type, NOT this field.
    acting_for_tenant_id: UUID | None = None


class MappingTemplatePatch(BaseModel):
    """``PATCH /mapping-templates/{template_id}`` body — at least one field present.

    ``template_name`` renames the LINEAGE (all version rows; D17 immutability
    covers version content, not the lineage label). ``mapping_rules`` edits the
    DRAFT in place, or mints a new DRAFT version chained to the STAGED/ACTIVE
    head when none exists.
    """

    template_name: str | None = Field(default=None, min_length=1, max_length=200)
    mapping_rules: dict[str, Any] | None = None
    # PLATFORM impersonation only (Slice 17b); see MappingTemplateCreate. NOT a content
    # field — it does not satisfy the at-least-one-of (template_name / mapping_rules) rule.
    acting_for_tenant_id: UUID | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> MappingTemplatePatch:
        if self.template_name is None and self.mapping_rules is None:
            raise ValueError("PATCH body must carry template_name and/or mapping_rules")
        return self
