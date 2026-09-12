"""Ambiguous JSON is not valid detection or request evidence."""
import json


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("non-finite JSON value")


def loads(value):
    return json.loads(value, object_pairs_hook=_unique, parse_constant=_constant)
