"""Bounded control-plane contracts for Phase 4 vision analysis.

Vision analysis is an additive v1 control contract layered on the accepted
handoff and the authenticated bounded binary-transfer path. No image bytes
travel through JSON; no provider selection, upload, capture, OCR execution,
or external action occurs at prepare time.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from core.protocol import (
    CLIENT_MESSAGES,
    PROTOCOL_SCHEMA,
    PROTOCOL_VERSION,
    SERVER_MESSAGES,
    validate_client_message,
    validate_server_message,
)


REQUEST_ID = "vision-req-1"
HANDOFF_ID = "handoff-1"
ANALYSIS_ID = "analysis-1"
TRANSFER_ID = "transfer:1"

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_PATH = REPO_ROOT / "protocol" / "hikari-v1.json"


# ---------------------------------------------------------------------------
# Valid message fixtures
# ---------------------------------------------------------------------------

VALID_PREPARE_OCR = {
    "type": "vision_analysis_prepare",
    "request_id": REQUEST_ID,
    "handoff_id": HANDOFF_ID,
    "capability": "ocr",
}

VALID_PREPARE_DESCRIBE = {
    "type": "vision_analysis_prepare",
    "request_id": REQUEST_ID,
    "handoff_id": HANDOFF_ID,
    "capability": "describe",
}

VALID_CANCEL = {
    "type": "vision_analysis_cancel",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
}

VALID_STATUS = {
    "type": "vision_analysis_status",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
}

VALID_VISUAL_TRANSFER_BEGIN = {
    "type": "visual_transfer_begin",
    "request_id": REQUEST_ID,
    "handoff_id": HANDOFF_ID,
    "mime_type": "image/png",
    "size_bytes": 1_048_576,
    "width": 4096,
    "height": 4096,
    "frame_count": 1,
}

VALID_VISUAL_TRANSFER_BEGIN_WITH_ANALYSIS = {
    **VALID_VISUAL_TRANSFER_BEGIN,
    "analysis_id": ANALYSIS_ID,
}

VALID_READY = {
    "type": "vision_analysis_ready",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "expires_at": 1000.25,
}

VALID_UPDATE_AWAITING = {
    "type": "vision_analysis_update",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "state": "awaiting_image",
}

VALID_UPDATE_ANALYZING = {
    "type": "vision_analysis_update",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "state": "analyzing",
}

VALID_UPDATE_CANCELLED = {
    "type": "vision_analysis_update",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "state": "cancelled",
}

VALID_UPDATE_EXPIRED = {
    "type": "vision_analysis_update",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "state": "expired",
}

VALID_OBSERVATION_SINGLE = {
    "type": "vision_observation",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "observations": [
        {"kind": "text", "text": "Recognized text.", "confidence_milli": 500}
    ],
}

VALID_OBSERVATION_SIXTEEN = {
    "type": "vision_observation",
    "request_id": REQUEST_ID,
    "analysis_id": ANALYSIS_ID,
    "observations": [
        {"kind": "description", "text": "A bounded description.", "confidence_milli": 750}
    ]
    * 16,
}

VALID_ERROR = {
    "type": "vision_analysis_error",
    "request_id": REQUEST_ID,
    "code": "analysis_failed",
}

VALID_ERROR_WITH_ANALYSIS = {
    "type": "vision_analysis_error",
    "request_id": REQUEST_ID,
    "code": "analysis_failed",
    "analysis_id": ANALYSIS_ID,
}


VALID_CLIENT_MESSAGES = (
    VALID_PREPARE_OCR,
    VALID_PREPARE_DESCRIBE,
    VALID_CANCEL,
    VALID_STATUS,
    VALID_VISUAL_TRANSFER_BEGIN_WITH_ANALYSIS,
)

VALID_SERVER_MESSAGES = (
    VALID_READY,
    VALID_UPDATE_AWAITING,
    VALID_UPDATE_ANALYZING,
    VALID_UPDATE_CANCELLED,
    VALID_UPDATE_EXPIRED,
    VALID_OBSERVATION_SINGLE,
    VALID_OBSERVATION_SIXTEEN,
    VALID_ERROR,
    VALID_ERROR_WITH_ANALYSIS,
)


# ---------------------------------------------------------------------------
# Every valid message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", VALID_CLIENT_MESSAGES)
def test_vision_client_messages_validate(message: dict[str, object]):
    assert validate_client_message(message) is None


@pytest.mark.parametrize("message", VALID_SERVER_MESSAGES)
def test_vision_server_messages_validate(message: dict[str, object]):
    assert validate_server_message(message) is None


# ---------------------------------------------------------------------------
# Exact key rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "field", "value"),
    [
        (VALID_PREPARE_OCR, "extra", "x"),
        (VALID_PREPARE_OCR, "analysis_id", ANALYSIS_ID),
        (VALID_CANCEL, "extra", "x"),
        (VALID_STATUS, "extra", "x"),
        (VALID_READY, "extra", "x"),
        (VALID_UPDATE_ANALYZING, "extra", "x"),
        (VALID_OBSERVATION_SINGLE, "extra", "x"),
        (VALID_ERROR, "extra", "x"),
    ],
)
def test_vision_messages_reject_unknown_fields(
    message: dict[str, object], field: str, value: object
):
    assert validate_client_message({**message, field: value}) is not None or (
        validate_server_message({**message, field: value}) is not None
    )


def test_vision_observation_item_rejects_unknown_keys():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {
                "kind": "text",
                "text": "hello",
                "confidence_milli": 500,
                "extra": "rejected",
            }
        ],
    }
    assert validate_server_message(message) is not None


def test_vision_observation_item_rejects_optional_fields():
    """Observation objects have no optional fields — exact_keys is enforced."""
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "hello", "confidence_milli": 500, "bounding_box": [0, 0]}
        ],
    }
    assert validate_server_message(message) is not None


# ---------------------------------------------------------------------------
# IDs and boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "field", "value"),
    [
        (VALID_PREPARE_OCR, "request_id", "Bad ID"),
        (VALID_PREPARE_OCR, "request_id", "UPPER"),
        (VALID_PREPARE_OCR, "request_id", "x" * 81),
        (VALID_PREPARE_OCR, "handoff_id", "Bad ID"),
        (VALID_PREPARE_OCR, "handoff_id", "UPPER"),
        (VALID_PREPARE_OCR, "handoff_id", "x" * 81),
        (VALID_CANCEL, "request_id", "Bad ID"),
        (VALID_CANCEL, "analysis_id", "Bad ID"),
        (VALID_CANCEL, "analysis_id", "x" * 81),
        (VALID_STATUS, "request_id", "Bad ID"),
        (VALID_STATUS, "analysis_id", "Bad ID"),
        (VALID_STATUS, "analysis_id", "x" * 81),
    ],
)
def test_vision_client_ids_are_canonical_and_bounded(
    message: dict[str, object], field: str, value: object
):
    assert validate_client_message({**message, field: value}) is not None


@pytest.mark.parametrize(
    ("message", "field", "value"),
    [
        (VALID_READY, "request_id", "Bad ID"),
        (VALID_READY, "analysis_id", "Bad ID"),
        (VALID_READY, "analysis_id", "x" * 81),
        (VALID_UPDATE_ANALYZING, "request_id", "Bad ID"),
        (VALID_UPDATE_ANALYZING, "analysis_id", "Bad ID"),
        (VALID_OBSERVATION_SINGLE, "request_id", "Bad ID"),
        (VALID_OBSERVATION_SINGLE, "analysis_id", "Bad ID"),
        (VALID_ERROR, "request_id", "Bad ID"),
        (VALID_ERROR_WITH_ANALYSIS, "analysis_id", "Bad ID"),
    ],
)
def test_vision_server_ids_are_canonical_and_bounded(
    message: dict[str, object], field: str, value: object
):
    assert validate_server_message({**message, field: value}) is not None


def test_vision_prepare_boundary_ids_validate():
    """Exactly 80-character canonical IDs are accepted."""
    rid = "a" + "b" * 79
    hid = "c" + "d" * 79
    message = {
        "type": "vision_analysis_prepare",
        "request_id": rid,
        "handoff_id": hid,
        "capability": "ocr",
    }
    assert validate_client_message(message) is None


def test_vision_ready_boundary_ids_validate():
    rid = "a" + "b" * 79
    aid = "e" + "f" * 79
    message = {
        "type": "vision_analysis_ready",
        "request_id": rid,
        "analysis_id": aid,
        "expires_at": 1000.25,
    }
    assert validate_server_message(message) is None


# ---------------------------------------------------------------------------
# Observation list 1 / 16 / 17
# ---------------------------------------------------------------------------


def _observation(count: int) -> dict[str, object]:
    return {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x", "confidence_milli": 0}
        ]
        * count,
    }


def test_vision_observation_accepts_one_item():
    assert validate_server_message(_observation(1)) is None


def test_vision_observation_accepts_sixteen_items():
    assert validate_server_message(_observation(16)) is None


def test_vision_observation_rejects_seventeen_items():
    assert validate_server_message(_observation(17)) is not None


def test_vision_observation_rejects_empty_array():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [],
    }
    assert validate_server_message(message) is not None


# ---------------------------------------------------------------------------
# Confidence -1 / 0 / 1000 / 1001 and booleans
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence_milli", [0, 1000])
def test_vision_confidence_accepts_boundaries(confidence_milli: int):
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x", "confidence_milli": confidence_milli}
        ],
    }
    assert validate_server_message(message) is None


@pytest.mark.parametrize("confidence_milli", [-1, 1001])
def test_vision_confidence_rejects_out_of_range(confidence_milli: int):
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x", "confidence_milli": confidence_milli}
        ],
    }
    assert validate_server_message(message) is not None


@pytest.mark.parametrize("confidence_milli", [True, False])
def test_vision_confidence_rejects_booleans(confidence_milli: bool):
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x", "confidence_milli": confidence_milli}
        ],
    }
    assert validate_server_message(message) is not None


def test_vision_confidence_rejects_float():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x", "confidence_milli": 500.5}
        ],
    }
    assert validate_server_message(message) is not None


# ---------------------------------------------------------------------------
# Finite expiry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expires_at", [math.nan, math.inf, -math.inf])
def test_vision_ready_rejects_non_finite_expiry(expires_at: float):
    message = {**VALID_READY, "expires_at": expires_at}
    assert validate_server_message(message) is not None


def test_vision_ready_rejects_boolean_expiry():
    message = {**VALID_READY, "expires_at": True}
    assert validate_server_message(message) is not None


def test_vision_ready_accepts_integer_expiry():
    message = {**VALID_READY, "expires_at": 1000}
    assert validate_server_message(message) is None


# ---------------------------------------------------------------------------
# Safe errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "invalid_request",
        "analysis_not_found",
        "handoff_not_accepted",
        "transfer_mismatch",
        "analysis_expired",
        "analysis_cancelled",
        "capability_unavailable",
        "analysis_failed",
        "unavailable",
    ],
)
def test_vision_error_accepts_fixed_codes(code: str):
    message = {"type": "vision_analysis_error", "request_id": REQUEST_ID, "code": code}
    assert validate_server_message(message) is None


@pytest.mark.parametrize(
    "code",
    [
        "raw exception",
        "provider_error",
        "stack_trace",
        "ocr_timeout",
        "model_unavailable",
        "destination_blocked",
        "",
    ],
)
def test_vision_error_rejects_unknown_codes(code: str):
    message = {"type": "vision_analysis_error", "request_id": REQUEST_ID, "code": code}
    assert validate_server_message(message) is not None


def test_vision_error_optional_analysis_id_is_accepted():
    message = {
        "type": "vision_analysis_error",
        "request_id": REQUEST_ID,
        "code": "analysis_not_found",
        "analysis_id": ANALYSIS_ID,
    }
    assert validate_server_message(message) is None


def test_vision_error_rejects_raw_message_detail_stack():
    for field in ("message", "detail", "stack"):
        message = {
            "type": "vision_analysis_error",
            "request_id": REQUEST_ID,
            "code": "analysis_failed",
            field: "raw failure text",
        }
        assert validate_server_message(message) is not None


def test_vision_error_rejects_provider_model_destination():
    for field in ("provider", "model", "destination"):
        message = {
            "type": "vision_analysis_error",
            "request_id": REQUEST_ID,
            "code": "analysis_failed",
            field: "cloud",
        }
        assert validate_server_message(message) is not None


# ---------------------------------------------------------------------------
# Forbidden identity / authority / content fields
# ---------------------------------------------------------------------------


FORBIDDEN_IDENTITY_FIELDS = [
    ("actor_id", "owner"),
    ("session_id", "session"),
    ("device_id", "device:1"),
]


FORBIDDEN_AUTHORITY_FIELDS = [
    ("approval_id", "approval"),
    ("grant_id", "grant"),
    ("execution_ticket", "ticket"),
]


FORBIDDEN_CONTENT_FIELDS = [
    ("bytes", "raw"),
    ("data", "raw"),
    ("base64", "AA=="),
    ("data_url", "data:image/png;base64,AA=="),
    ("filename", "image.png"),
    ("path", "/tmp/image.png"),
    ("url", "https://example.invalid/image.png"),
    ("task_payload", {"content": "private"}),
    ("content_hash", "sha256." + "a" * 64),
]


@pytest.mark.parametrize(
    ("field", "value"), FORBIDDEN_IDENTITY_FIELDS + FORBIDDEN_AUTHORITY_FIELDS
)
def test_vision_client_rejects_identity_and_authority_fields(
    field: str, value: object
):
    message = {**VALID_PREPARE_OCR, field: value}
    assert validate_client_message(message) is not None


@pytest.mark.parametrize(("field", "value"), FORBIDDEN_CONTENT_FIELDS)
def test_vision_client_rejects_content_fields(field: str, value: object):
    message = {**VALID_PREPARE_OCR, field: value}
    assert validate_client_message(message) is not None


@pytest.mark.parametrize(
    ("field", "value"), FORBIDDEN_IDENTITY_FIELDS + FORBIDDEN_AUTHORITY_FIELDS
)
def test_vision_server_rejects_identity_and_authority_fields(
    field: str, value: object
):
    message = {**VALID_READY, field: value}
    assert validate_server_message(message) is not None


@pytest.mark.parametrize(("field", "value"), FORBIDDEN_CONTENT_FIELDS)
def test_vision_server_rejects_content_fields(field: str, value: object):
    message = {**VALID_READY, field: value}
    assert validate_server_message(message) is not None


def test_vision_observation_rejects_bytes_and_image_content():
    for field in ("bytes", "data", "base64", "data_url", "image", "raw"):
        message = {
            "type": "vision_observation",
            "request_id": REQUEST_ID,
            "analysis_id": ANALYSIS_ID,
            "observations": [
                {"kind": "text", "text": "x", "confidence_milli": 500}
            ],
            field: "raw",
        }
        assert validate_server_message(message) is not None


def test_vision_prepare_rejects_ocr_image_contents():
    """OCR/image contents must not appear in request messages."""
    for field in ("image", "image_bytes", "ocr_text", "ocr_result", "captured_image"):
        message = {**VALID_PREPARE_OCR, field: "raw"}
        assert validate_client_message(message) is not None


def test_vision_prepare_rejects_content_hash_as_authorization():
    message = {
        **VALID_PREPARE_OCR,
        "content_hash": "sha256." + "a" * 64,
    }
    assert validate_client_message(message) is not None


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability", ["ocr", "describe"])
def test_vision_prepare_accepts_valid_capabilities(capability: str):
    message = {**VALID_PREPARE_OCR, "capability": capability}
    assert validate_client_message(message) is None


@pytest.mark.parametrize(
    "capability", ["translate", "OCR", "Describe", "", "ocr_describe", "vision"]
)
def test_vision_prepare_rejects_invalid_capabilities(capability: str):
    message = {**VALID_PREPARE_OCR, "capability": capability}
    assert validate_client_message(message) is not None


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["awaiting_image", "analyzing", "cancelled", "expired"]
)
def test_vision_update_accepts_valid_states(state: str):
    message = {**VALID_UPDATE_ANALYZING, "state": state}
    assert validate_server_message(message) is None


@pytest.mark.parametrize(
    "state", ["pending", "completed", "failed", "ready", "", "ANALYZING"]
)
def test_vision_update_rejects_invalid_states(state: str):
    message = {**VALID_UPDATE_ANALYZING, "state": state}
    assert validate_server_message(message) is not None


# ---------------------------------------------------------------------------
# Observation kind enum and text bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["text", "description"])
def test_vision_observation_accepts_valid_kinds(kind: str):
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [{"kind": kind, "text": "x", "confidence_milli": 500}],
    }
    assert validate_server_message(message) is None


@pytest.mark.parametrize("kind", ["ocr", "caption", "label", "", "TEXT"])
def test_vision_observation_rejects_invalid_kinds(kind: str):
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [{"kind": kind, "text": "x", "confidence_milli": 500}],
    }
    assert validate_server_message(message) is not None


def test_vision_observation_text_accepts_boundary_lengths():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x" * 2000, "confidence_milli": 500}
        ],
    }
    assert validate_server_message(message) is None


def test_vision_observation_text_rejects_empty():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [{"kind": "text", "text": "", "confidence_milli": 500}],
    }
    assert validate_server_message(message) is not None


def test_vision_observation_text_rejects_oversized():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "x" * 2001, "confidence_milli": 500}
        ],
    }
    assert validate_server_message(message) is not None


def test_vision_observation_text_rejects_control_characters():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "bad\x01char", "confidence_milli": 500}
        ],
    }
    assert validate_server_message(message) is not None


def test_vision_observation_text_allows_newline_and_tab():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "line one\n\tline two", "confidence_milli": 500}
        ],
    }
    assert validate_server_message(message) is None


def test_vision_observation_text_rejects_unicode_format_characters():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "text", "text": "bad\u200btext", "confidence_milli": 500}
        ],
    }
    assert validate_server_message(message) is not None


# ---------------------------------------------------------------------------
# Compatibility of visual_transfer_begin without analysis_id
# ---------------------------------------------------------------------------


def test_visual_transfer_begin_without_analysis_id_remains_valid():
    """Omitting the optional analysis_id is unchanged v1 behavior."""
    assert validate_client_message(VALID_VISUAL_TRANSFER_BEGIN) is None


def test_visual_transfer_begin_with_analysis_id_is_valid():
    assert validate_client_message(VALID_VISUAL_TRANSFER_BEGIN_WITH_ANALYSIS) is None


def test_visual_transfer_begin_rejects_invalid_analysis_id():
    message = {**VALID_VISUAL_TRANSFER_BEGIN, "analysis_id": "Bad ID"}
    assert validate_client_message(message) is not None


def test_visual_transfer_begin_rejects_oversized_analysis_id():
    message = {**VALID_VISUAL_TRANSFER_BEGIN, "analysis_id": "x" * 81}
    assert validate_client_message(message) is not None


# ---------------------------------------------------------------------------
# Compatibility of all existing Phase 1-4 messages
# ---------------------------------------------------------------------------


def test_vision_protocol_is_additive_v1():
    assert PROTOCOL_VERSION == 1
    assert PROTOCOL_SCHEMA["name"] == "hikari.websocket"
    # Existing messages remain present and unchanged in direction.
    assert "pair" in CLIENT_MESSAGES
    assert "message" in CLIENT_MESSAGES
    assert "handoff_prepare" in CLIENT_MESSAGES
    assert "visual_transfer_begin" in CLIENT_MESSAGES
    assert "pong" in SERVER_MESSAGES
    assert "response" in SERVER_MESSAGES
    assert "handoff_offer" in SERVER_MESSAGES
    assert "visual_transfer_ready" in SERVER_MESSAGES


def test_vision_messages_carry_no_bytes():
    assert all("bytes" not in message for message in VALID_CLIENT_MESSAGES)
    assert all("bytes" not in message for message in VALID_SERVER_MESSAGES)
    assert all("base64" not in message for message in VALID_CLIENT_MESSAGES)
    assert all("base64" not in message for message in VALID_SERVER_MESSAGES)
    assert all("data_url" not in message for message in VALID_CLIENT_MESSAGES)
    assert all("data_url" not in message for message in VALID_SERVER_MESSAGES)


def test_vision_request_messages_carry_no_image_content():
    """No OCR/image contents in request messages."""
    request_messages = (VALID_PREPARE_OCR, VALID_PREPARE_DESCRIBE, VALID_CANCEL, VALID_STATUS)
    for message in request_messages:
        for key in message:
            assert key not in {
                "image",
                "image_bytes",
                "ocr_text",
                "ocr_result",
                "captured_image",
                "filename",
                "path",
                "url",
            }


def test_vision_analysis_id_is_not_in_client_prepare():
    """analysis_id is server-generated and must not appear in prepare requests."""
    assert "analysis_id" not in VALID_PREPARE_OCR
    assert "analysis_id" not in VALID_PREPARE_DESCRIBE


# ---------------------------------------------------------------------------
# JSON validation
# ---------------------------------------------------------------------------


def test_protocol_json_file_is_valid_json():
    data = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["name"] == "hikari.websocket"


def test_protocol_json_contains_vision_messages():
    data = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    client = data["client_to_server"]
    server = data["server_to_client"]
    assert "vision_analysis_prepare" in client
    assert "vision_analysis_cancel" in client
    assert "vision_analysis_status" in client
    assert "vision_analysis_ready" in server
    assert "vision_analysis_update" in server
    assert "vision_observation" in server
    assert "vision_analysis_error" in server


def test_protocol_json_visual_transfer_begin_has_optional_analysis_id():
    data = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    optional = data["client_to_server"]["visual_transfer_begin"]["optional"]
    assert "analysis_id" in optional
    spec = optional["analysis_id"]
    assert spec["type"] == "string"
    assert spec["max_length"] == 80
    assert spec["pattern"] == "^[a-z0-9][a-z0-9_.-]{0,79}$"


def test_protocol_json_vision_prepare_has_no_optional_fields():
    data = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    spec = data["client_to_server"]["vision_analysis_prepare"]
    assert spec["optional"] == {}
    assert set(spec["required"]) == {"request_id", "handoff_id", "capability"}


def test_protocol_json_vision_error_has_optional_analysis_id_only():
    data = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    spec = data["server_to_client"]["vision_analysis_error"]
    assert set(spec["optional"]) == {"analysis_id"}
    assert set(spec["required"]) == {"request_id", "code"}


def test_protocol_json_vision_observation_is_bounded():
    data = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    obs = data["server_to_client"]["vision_observation"]["required"]["observations"]
    assert obs["type"] == "array"
    assert obs["min_items"] == 1
    assert obs["max_items"] == 16
    item = obs["items"]
    assert item["exact_keys"] is True
    assert set(item["required"]) == {"kind", "text"}
    assert set(item["optional"]) == {"confidence_milli"}
    assert item["optional"]["confidence_milli"]["minimum"] == 0
    assert item["optional"]["confidence_milli"]["maximum"] == 1000
    assert item["required"]["text"]["min_length"] == 1
    assert item["required"]["text"]["max_length"] == 2000


def test_vision_observation_accepts_unavailable_confidence():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [{"kind": "text", "text": "measured text"}],
    }
    assert validate_server_message(message) is None


def test_vision_description_rejects_newline_and_tab():
    message = {
        "type": "vision_observation",
        "request_id": REQUEST_ID,
        "analysis_id": ANALYSIS_ID,
        "observations": [
            {"kind": "description", "text": "line one\nline two"}
        ],
    }
    assert validate_server_message(message) is not None
