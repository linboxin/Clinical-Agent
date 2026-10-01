"""Defaults that matter for correctness."""

from app.config import Settings


def test_name_normalizer_is_off_by_default() -> None:
    assert Settings(_env_file=None).name_normalizer == "off"  # type: ignore[call-arg]
