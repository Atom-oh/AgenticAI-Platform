"""Pure validation for text passed to Studio asset registration."""

MAX_CONTENT_CHARS = 20_000


def validated_content(value: object) -> str:
    """Return the original string or raise ValueError; never coerce or truncate."""
    if not isinstance(value, str):
        raise ValueError("자산 내용은 문자열이어야 합니다.")
    if not value.strip():
        raise ValueError("자산 내용이 비어 있습니다. 내용을 입력하세요.")
    if len(value) > MAX_CONTENT_CHARS:
        raise ValueError("자산 내용은 20,000자 이하여야 합니다. 내용을 줄여 주세요.")
    return value
