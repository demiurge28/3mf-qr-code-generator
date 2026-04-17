"""Tests for :mod:`qr23mf.qr` (qr-matrix-core scope, traces FR-1, NFR-1)."""

from __future__ import annotations

from typing import get_args

import numpy as np
import pytest

from qr23mf.qr import EcLevel, QrMatrix, build_matrix

_EC_LEVELS: tuple[EcLevel, ...] = get_args(EcLevel)


@pytest.mark.parametrize("ec", _EC_LEVELS)
def test_build_matrix_all_ec_levels_produce_valid_square_bool_matrix(
    ec: EcLevel,
) -> None:
    """Each EC level must yield a square boolean matrix at a valid QR version."""
    result = build_matrix("https://example.com/qr23mf", ec=ec)
    assert isinstance(result, QrMatrix)
    assert result.ec == ec
    assert result.modules.dtype == np.bool_
    assert result.modules.ndim == 2
    assert result.modules.shape == (result.size, result.size)
    assert result.size > 0
    assert 1 <= result.version <= 40


def test_default_ec_is_m() -> None:
    """Omitting ``ec`` must default to ``"M"``."""
    result = build_matrix("hello")
    assert result.ec == "M"


def test_build_matrix_is_deterministic() -> None:
    """Two calls with identical inputs must produce byte-identical matrices."""
    a = build_matrix("deterministic-payload", ec="Q")
    b = build_matrix("deterministic-payload", ec="Q")
    assert a.size == b.size
    assert a.version == b.version
    assert a.ec == b.ec
    assert np.array_equal(a.modules, b.modules)


def test_returned_matrix_is_independent_copy() -> None:
    """Mutating the returned matrix must not break determinism on subsequent calls."""
    first = build_matrix("isolation-check", ec="M")
    first.modules[0, 0] = not first.modules[0, 0]  # mutate in place
    second = build_matrix("isolation-check", ec="M")
    # segno's internal matrix was not the one we mutated, so `second` is pristine.
    assert np.any(second.modules != first.modules)


def test_higher_ec_never_shrinks_version() -> None:
    """Increasing EC level can only keep or grow the QR version for a given payload."""
    payload = "monotonic-ec-check-" * 3
    versions = [build_matrix(payload, ec=level).version for level in _EC_LEVELS]
    assert versions == sorted(versions), f"versions should be monotonic non-decreasing: {versions}"


def test_near_capacity_text_promotes_to_larger_version() -> None:
    """A long payload should require a higher QR version than a short one."""
    short = build_matrix("hi", ec="M")
    long_payload = "x" * 300
    big = build_matrix(long_payload, ec="M")
    assert big.version > short.version
    assert big.size > short.size


def test_empty_string_raises_value_error() -> None:
    """Empty payload is not encodable; must raise a helpful ValueError."""
    with pytest.raises(ValueError, match="non-empty"):
        build_matrix("")


def test_non_string_input_raises_value_error() -> None:
    """Non-string payload (bypassing type checker) must raise ValueError."""
    with pytest.raises(ValueError, match="must be a string"):
        build_matrix(42)  # type: ignore[arg-type]


def test_unknown_ec_raises_value_error() -> None:
    """Unknown EC levels must raise ValueError and name the valid options."""
    with pytest.raises(ValueError, match=r"ec must be one of"):
        build_matrix("hello", ec="X")  # type: ignore[arg-type]


def test_qrmatrix_is_frozen() -> None:
    """:class:`QrMatrix` is immutable; assigning to fields must fail."""
    result = build_matrix("frozen", ec="L")
    with pytest.raises((AttributeError, TypeError)):
        result.size = 999  # type: ignore[misc]


def test_known_version1_payload_produces_21x21_matrix() -> None:
    """A short ASCII payload at EC=L fits in version 1 (21x21 modules, no quiet zone)."""
    result = build_matrix("qr", ec="L")
    assert result.version == 1
    assert result.size == 21
    assert result.modules.shape == (21, 21)
