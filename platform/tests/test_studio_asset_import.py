"""Content intake rejects invalid values without rewriting valid text."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.asset_import import MAX_CONTENT_CHARS, validated_content  # noqa: E402


def test_valid_text_is_preserved_exactly():
    content = '\ufeff  # 자산\r\n\t{"색상": "#008485", "label": "e\u0301 🎨"}  \n'
    assert validated_content(content) == content


@pytest.mark.parametrize("value", [None, False, 42, 1.5, b"text", [], {"content": "text"}])
def test_non_strings_are_rejected_without_coercion(value):
    with pytest.raises(ValueError, match="문자열"):
        validated_content(value)


@pytest.mark.parametrize("content", ["", " ", "\t\r\n", "\u3000"])
def test_empty_or_whitespace_only_content_is_rejected(content):
    with pytest.raises(ValueError, match="비어"):
        validated_content(content)


@pytest.mark.parametrize("character", ["a", "한", "🎨"])
def test_exact_limit_is_accepted_in_characters_not_utf8_bytes(character):
    content = character * 20_000
    assert len(content) == MAX_CONTENT_CHARS
    assert validated_content(content) == content


@pytest.mark.parametrize("character", ["a", "한", "🎨"])
def test_over_limit_is_rejected_instead_of_truncated(character):
    with pytest.raises(ValueError, match="20,000"):
        validated_content(character * 20_001)


def test_surrounding_whitespace_counts_toward_limit():
    with pytest.raises(ValueError, match="20,000"):
        validated_content(" " + "a" * 20_000)


def test_plain_text_does_not_require_json():
    content = "# 스타일 가이드\n큰 글자와 명확한 버튼을 사용합니다."
    assert validated_content(content) == content
