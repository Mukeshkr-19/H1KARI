"""Tests for core.visual_transfer.contracts: frozen limits, enums, dataclasses, repr safety."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from core.visual_transfer.contracts import (
    AGGREGATE_MEMORY_CAP_BYTES,
    DECOMPRESSION_PIXEL_LIMIT,
    EXACT_FRAME_COUNT,
    HANDOFF_ID_PATTERN,
    MAX_DIMENSION,
    MAX_ENCODED_BYTES,
    MIN_DIMENSION,
    TRANSFER_ID_PATTERN,
    TRANSFER_TTL_SECONDS,
    ContractValidationError,
    ValidatedImageMetadata,
    VisualTransferBeginResult,
    VisualTransferDeclaration,
    VisualTransferErrorCode,
    VisualTransferOutcomeStatus,
    VisualTransferResult,
    VisualTransferState,
    validate_actor_scope,
    validate_handoff_id,
    validate_mime,
    validate_transfer_id,
)


# --- Canonical limits -------------------------------------------------------

def test_canonical_limits() -> None:
    assert MIN_DIMENSION == 1
    assert MAX_DIMENSION == 4096
    assert MAX_ENCODED_BYTES == 1_048_576
    assert DECOMPRESSION_PIXEL_LIMIT == 16_777_216
    assert EXACT_FRAME_COUNT == 1
    assert TRANSFER_TTL_SECONDS == 60
    assert AGGREGATE_MEMORY_CAP_BYTES == 8 * 1_048_576


def test_aggregate_cap_within_documented_ceiling() -> None:
    assert AGGREGATE_MEMORY_CAP_BYTES <= 8 * 1_048_576


def test_state_enum_values() -> None:
    expected = {
        "pending", "receiving", "validating", "completed",
        "cancelled", "failed", "expired",
    }
    assert {s.value for s in VisualTransferState} == expected


def test_error_code_enum_values() -> None:
    expected = {
        "unavailable", "invalid_request", "unauthorized", "transfer_not_found",
        "transfer_expired", "handoff_not_accepted", "mime_unsupported",
        "mime_mismatch", "size_exceeded", "dimensions_exceeded",
        "frame_count_invalid", "decompression_limit", "metadata_rejected",
        "malformed_image", "rate_limited",
    }
    assert {e.value for e in VisualTransferErrorCode} == expected


def test_outcome_status_enum_values() -> None:
    assert {o.value for o in VisualTransferOutcomeStatus} == {"ok", "error"}


# --- transfer_id / handoff_id / actor_scope validators ----------------------

@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("t-1", True),
        ("a", True),
        ("A", True),
        ("transfer.123", True),
        ("id:with-dots", True),
        ("", False),
        ("x" * 129, False),
        ("bad space", False),
        ("bad/slash", False),
    ],
)
def test_validate_transfer_id(value: str, valid: bool) -> None:
    if valid:
        assert validate_transfer_id(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_transfer_id(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("h-1", True),
        ("a", True),
        ("handoff_1", True),
        ("A", False),
        ("", False),
        ("x" * 81, False),
        ("bad space", False),
        ("-leading", False),
    ],
)
def test_validate_handoff_id(value: str, valid: bool) -> None:
    if valid:
        assert validate_handoff_id(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_handoff_id(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("scope-1", True),
        ("a", True),
        ("session_1", True),
        ("A", False),
        ("", False),
        ("x" * 129, False),
        ("bad space", False),
    ],
)
def test_validate_actor_scope(value: str, valid: bool) -> None:
    if valid:
        assert validate_actor_scope(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_actor_scope(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("image/png", True),
        ("image/jpeg", True),
        ("image/gif", False),
        ("image/png ", False),
        ("", False),
        (None, False),
        (123, False),
    ],
)
def test_validate_mime(value: object, valid: bool) -> None:
    if valid:
        assert validate_mime(value) == value
    else:
        with pytest.raises(ContractValidationError):
            validate_mime(value)


# --- VisualTransferDeclaration ----------------------------------------------

def _valid_decl_kwargs(**overrides: object) -> dict:
    base: dict = {
        "transfer_id": "t-1",
        "handoff_id": "h-1",
        "mime": "image/png",
        "declared_byte_length": 100,
        "declared_width": 10,
        "declared_height": 10,
        "frame_count": 1,
    }
    base.update(overrides)
    return base


def test_declaration_accepts_valid_values() -> None:
    decl = VisualTransferDeclaration(**_valid_decl_kwargs())
    assert decl.frame_count == EXACT_FRAME_COUNT


def test_declaration_accepts_empty_transfer_id_as_unassigned() -> None:
    """The server assigns transfer_id via begin(); empty means unassigned."""
    decl = VisualTransferDeclaration(**_valid_decl_kwargs(transfer_id=""))
    assert decl.transfer_id == ""


def test_declaration_rejects_invalid_nonempty_transfer_id() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(transfer_id="bad id"))


def test_declaration_rejects_oversized_bytes() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(declared_byte_length=MAX_ENCODED_BYTES + 1))


def test_declaration_rejects_zero_bytes() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(declared_byte_length=0))


def test_declaration_rejects_oversized_dimension() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(declared_width=MAX_DIMENSION + 1))
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(declared_height=MAX_DIMENSION + 1))


def test_declaration_rejects_zero_dimension() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(declared_width=0))
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(declared_height=0))


def test_declaration_rejects_decompression_limit() -> None:
    # 4096 * 4097 = 16,783,872 < 16,777,216? No: 4096*4097 = 16,781,312 > 16,777,216.
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(
            **_valid_decl_kwargs(declared_width=4096, declared_height=4097)
        )


def test_declaration_accepts_max_pixels() -> None:
    decl = VisualTransferDeclaration(
        **_valid_decl_kwargs(declared_width=4096, declared_height=4096)
    )
    assert decl.declared_width * decl.declared_height == DECOMPRESSION_PIXEL_LIMIT


def test_declaration_rejects_frame_count_not_one() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(frame_count=2))
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(frame_count=0))


def test_declaration_rejects_unsupported_mime() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferDeclaration(**_valid_decl_kwargs(mime="image/gif"))


def test_declaration_is_frozen() -> None:
    decl = VisualTransferDeclaration(**_valid_decl_kwargs())
    assert dataclasses.is_dataclass(decl)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decl.declared_width = 20  # type: ignore[misc]


def test_declaration_repr_is_content_free() -> None:
    decl = VisualTransferDeclaration(
        **_valid_decl_kwargs(handoff_id="secret-handoff")
    )
    rep = repr(decl)
    assert "secret-handoff" not in rep
    assert "100" not in rep
    assert "10" not in rep
    assert rep == "VisualTransferDeclaration(declared)"


# --- ValidatedImageMetadata -------------------------------------------------

def _valid_meta_kwargs(**overrides: object) -> dict:
    base: dict = {
        "mime": "image/png",
        "width": 10,
        "height": 10,
        "sha256": "sha256." + "a" * 64,
    }
    base.update(overrides)
    return base


def test_metadata_accepts_valid_values() -> None:
    meta = ValidatedImageMetadata(**_valid_meta_kwargs())
    assert meta.sha256.startswith("sha256.")


def test_metadata_rejects_bad_sha_prefix() -> None:
    with pytest.raises(ContractValidationError):
        ValidatedImageMetadata(**_valid_meta_kwargs(sha256="sha1." + "a" * 64))


def test_metadata_rejects_bad_sha_length() -> None:
    with pytest.raises(ContractValidationError):
        ValidatedImageMetadata(**_valid_meta_kwargs(sha256="sha256." + "a" * 63))


def test_metadata_rejects_uppercase_sha() -> None:
    with pytest.raises(ContractValidationError):
        ValidatedImageMetadata(**_valid_meta_kwargs(sha256="sha256." + "A" * 64))


def test_metadata_rejects_oversized_dimensions() -> None:
    with pytest.raises(ContractValidationError):
        ValidatedImageMetadata(**_valid_meta_kwargs(width=MAX_DIMENSION + 1))


def test_metadata_rejects_decompression_limit() -> None:
    with pytest.raises(ContractValidationError):
        ValidatedImageMetadata(**_valid_meta_kwargs(width=4096, height=4097))


def test_metadata_repr_is_content_free() -> None:
    meta = ValidatedImageMetadata(
        **_valid_meta_kwargs(
            sha256="sha256." + "f" * 64, width=123, height=456
        )
    )
    rep = repr(meta)
    assert "f" * 64 not in rep
    assert "123" not in rep
    assert "456" not in rep
    assert rep == "ValidatedImageMetadata(validated)"


# --- VisualTransferResult ---------------------------------------------------

def test_result_ok_requires_metadata() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferResult(
            status=VisualTransferOutcomeStatus.OK,
            state=VisualTransferState.COMPLETED,
            metadata=None,
        )


def test_nonterminal_ok_result_requires_no_metadata() -> None:
    result = VisualTransferResult(
        status=VisualTransferOutcomeStatus.OK,
        state=VisualTransferState.PENDING,
    )
    assert result.metadata is None


def test_begin_result_requires_server_generated_transfer_id_on_success() -> None:
    result = VisualTransferBeginResult(
        status=VisualTransferOutcomeStatus.OK,
        state=VisualTransferState.PENDING,
        transfer_id="Transfer:1",
    )
    assert result.transfer_id == "Transfer:1"
    assert "Transfer:1" not in repr(result)
    with pytest.raises(ContractValidationError):
        VisualTransferBeginResult(
            status=VisualTransferOutcomeStatus.OK,
            state=VisualTransferState.PENDING,
        )


def test_result_error_requires_error_code() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferResult(
            status=VisualTransferOutcomeStatus.ERROR,
            state=VisualTransferState.FAILED,
            error=None,
        )


def test_result_rejects_ambiguous() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferResult(
            status=VisualTransferOutcomeStatus.OK,
            state=VisualTransferState.COMPLETED,
            metadata=ValidatedImageMetadata(**_valid_meta_kwargs()),
            error=VisualTransferErrorCode.MALFORMED_IMAGE,
        )


def test_result_rejects_metadata_without_completed_state() -> None:
    with pytest.raises(ContractValidationError):
        VisualTransferResult(
            status=VisualTransferOutcomeStatus.OK,
            state=VisualTransferState.PENDING,
            metadata=ValidatedImageMetadata(**_valid_meta_kwargs()),
        )


def test_result_repr_is_content_free() -> None:
    ok_result = VisualTransferResult(
        status=VisualTransferOutcomeStatus.OK,
        state=VisualTransferState.COMPLETED,
        metadata=ValidatedImageMetadata(**_valid_meta_kwargs(sha256="sha256." + "b" * 64)),
    )
    rep = repr(ok_result)
    assert "b" * 64 not in rep
    assert rep == "VisualTransferResult(status='ok', state='completed')"

    err_result = VisualTransferResult(
        status=VisualTransferOutcomeStatus.ERROR,
        state=VisualTransferState.FAILED,
        error=VisualTransferErrorCode.MALFORMED_IMAGE,
    )
    rep_err = repr(err_result)
    assert rep_err == "VisualTransferResult(status='error', state='failed')"


# --- Module hygiene: no forbidden imports -----------------------------------

def test_contracts_module_has_no_forbidden_imports() -> None:
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "core"
        / "visual_transfer"
        / "contracts.py"
    )
    forbidden = {
        "subprocess", "socket", "threading", "asyncio", "requests",
        "http", "urllib", "browser", " PIL", "PIL", "cv2", "numpy",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert forbidden.isdisjoint(imported), f"forbidden imports: {forbidden & imported}"


def test_patterns_are_compiled_regex() -> None:
    assert TRANSFER_ID_PATTERN.pattern == r"^[A-Za-z0-9._:-]{1,128}$"
    assert HANDOFF_ID_PATTERN.pattern == r"^[a-z0-9][a-z0-9_.-]{0,79}$"
