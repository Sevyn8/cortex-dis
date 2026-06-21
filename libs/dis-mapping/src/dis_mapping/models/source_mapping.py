"""``SourceMapping`` — the validated in-memory form of ``config.source_mappings.mapping_rules``.

The live JSONB shape is ``{version, rename, normalize, cast, derive}`` (introspected
slice-05; the field is named ``normalize``, not ``transforms`` — see decisions.md
D49). The live row's sub-objects are empty (``{}``), so the inner shape is defined
HERE, not by live data (the column comment delegates: "documented in
libs/dis-mapping"). Onboarding (Slice 14) generates against this model.

- ``rename``: source column -> canonical column.
- ``normalize``: canonical column -> ORDERED LIST of atomic transforms, applied in
  declared sequence (an empty list is a valid no-op).
- ``cast``: canonical column -> target type (runs after normalize; D20 ordering is
  load-bearing).
- ``derive``: canonical column -> ORDERED LIST starting with a generator
  (``copy`` / ``constant`` / ``date_from_datetime``) followed by normalize-vocabulary
  ops (derive is bounded to the same declarative vocabulary; slice-05).

ALL config validation happens at construction (`MappingConfigError`, code-quality
rule 4) — including cross-spec composition typing for derive lists, which is
decidable here because every intermediate dtype is known from the cast specs.

What this model deliberately has no field for: ``tenant_id`` / ``store_id`` /
``trace_id`` / ``mapping_version_id`` — those are consumer-injected after the
engine runs (hard rule 5, D8, D22); the engine cannot stamp what it never holds.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from dis_core.errors import MappingConfigError
from dis_mapping.models.transform import (
    NORMALIZE_OPS,
    CastSpec,
    TransformSpec,
    validate_cast_spec,
    validate_derive_generator_args,
    validate_normalize_args,
)


class SourceMapping(BaseModel):
    """One source's mapping rules. Frozen; ``extra="forbid"`` (typo'd keys fail loud)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    rename: dict[str, str] = {}
    normalize: dict[str, list[TransformSpec]] = {}
    cast: dict[str, CastSpec] = {}
    derive: dict[str, list[TransformSpec]] = {}

    @property
    def target_columns(self) -> tuple[str, ...]:
        """The canonical columns this mapping produces, in declaration order.

        This is the contribution's exact column set: rename targets then derive
        targets. Nothing else is ever emitted (slice-05 criterion 2/7).
        """
        return tuple(self.rename.values()) + tuple(self.derive.keys())

    # -- construction validation --------------------------------------------------

    @model_validator(mode="after")
    def _validate_rules(self) -> SourceMapping:
        if self.version < 1:
            raise MappingConfigError(f"mapping version must be >= 1, got {self.version}")

        # Rename targets must be unique: two source columns mapping to one
        # canonical column would silently overwrite each other.
        targets = list(self.rename.values())
        duplicates = sorted({t for t in targets if targets.count(t) > 1})
        if duplicates:
            raise MappingConfigError(f"rename targets are not unique: {duplicates}")
        rename_targets = set(targets)

        # Normalize and cast operate post-rename, pre-derive: their keys must be
        # rename targets (derive outputs do not exist yet at those stages).
        for column in self.normalize:
            if column not in rename_targets:
                raise MappingConfigError(
                    f"normalize names column {column!r} which no rename produces",
                    column=column,
                )
        for column in self.cast:
            if column not in rename_targets:
                raise MappingConfigError(
                    f"cast names column {column!r} which no rename produces",
                    column=column,
                )

        # Derive targets are new columns; colliding with a rename target would
        # silently overwrite mapped data.
        for column in self.derive:
            if column in rename_targets:
                raise MappingConfigError(
                    f"derive target {column!r} collides with a rename target",
                    column=column,
                )

        # Per-op arg validation, across the WHOLE list of every column.
        for column, specs in self.normalize.items():
            for spec in specs:
                validate_normalize_args(spec, column)
        for column, cast_spec in self.cast.items():
            validate_cast_spec(cast_spec, column)
        for column, specs in self.derive.items():
            self._validate_derive_list(column, specs, rename_targets)
        return self

    def _validate_derive_list(
        self, column: str, specs: list[TransformSpec], rename_targets: set[str]
    ) -> None:
        if not specs:
            raise MappingConfigError(
                f"derive list for column {column!r} is empty: it must start with a generator "
                "op (copy / constant / date_from_datetime)",
                column=column,
            )
        generator, rest = specs[0], specs[1:]
        validate_derive_generator_args(generator, column)

        # Generator source columns must be rename targets (derive cannot chain off
        # another derive target — build to current need; slice-05 scope boundary).
        if generator.op in ("copy", "date_from_datetime"):
            source = generator.args["source_column"]
            if source not in rename_targets:
                raise MappingConfigError(
                    f"derive {generator.op!r} on column {column!r}: source_column {source!r} "
                    "is not a rename target",
                    column=column,
                )
            if generator.op == "date_from_datetime":
                cast_spec = self.cast.get(source)
                if cast_spec is None or cast_spec.type != "datetime":
                    raise MappingConfigError(
                        f"derive 'date_from_datetime' on column {column!r}: source_column "
                        f"{source!r} must be cast to datetime (its UTC date is the derived "
                        "value; cf. the event_date CHECK)",
                        column=column,
                    )

        # Composition typing: normalize-vocabulary ops transform STRINGS, so any
        # ops after the generator require a string intermediate. Every generator's
        # output dtype is known at construction: copy -> the source's cast type
        # (string when un-cast, since normalize output is canonical-string);
        # constant -> the literal's python type; date_from_datetime -> date.
        for position, spec in enumerate(rest, start=1):
            if spec.op not in NORMALIZE_OPS:
                raise MappingConfigError(
                    f"derive list for column {column!r}: op {spec.op!r} at position "
                    f"{position} is not in the normalize vocabulary (derive is "
                    "bounded to the same declarative vocabulary)",
                    column=column,
                )
            validate_normalize_args(spec, column)
        if rest:
            if generator.op == "date_from_datetime":
                raise MappingConfigError(
                    f"derive list for column {column!r}: 'date_from_datetime' yields a date, "
                    "but the following normalize ops transform strings",
                    column=column,
                )
            if generator.op == "constant" and not isinstance(generator.args["value"], str):
                raise MappingConfigError(
                    f"derive list for column {column!r}: 'constant' yields a non-string "
                    "literal, but the following normalize ops transform strings",
                    column=column,
                )
            if generator.op == "copy":
                source_cast = self.cast.get(generator.args["source_column"])
                if source_cast is not None and source_cast.type != "string":
                    raise MappingConfigError(
                        f"derive list for column {column!r}: 'copy' of a column cast to "
                        f"{source_cast.type!r} yields a non-string value, but the following "
                        "normalize ops transform strings",
                        column=column,
                    )
