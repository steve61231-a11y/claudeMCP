"""A model's reply, held to the shape the code reads it as.

A reply is untrusted text that happened to parse as JSON: a key asked for as
a list can come back as a number, a sentence as a list, an object as None.
The whole Issue Map, driven end to end with a model answering junk for every
key (`tests/test_the_whole_map_survives_a_bad_model.py`), died on
`for v in 7` in the first analyst it met — so the shape is enforced where a
reply is read, by these few helpers, and never assumed.

Nothing here invents content: a value of the wrong kind becomes the empty
value of the right kind, which every caller already treats as "the analyst
said nothing here" and reports as such.
"""

from __future__ import annotations


def text(value) -> str:
    """A sentence or paragraph: a string, else empty. A number is not prose."""
    return value.strip() if isinstance(value, str) else ""


def dicts(value) -> list[dict]:
    """A list of objects: only the objects in a list, else empty."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def texts(value) -> list[str]:
    """A list of strings: only the non-empty strings in a list, else empty."""
    return [v.strip() for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def obj(value) -> dict:
    """An object, else empty."""
    return value if isinstance(value, dict) else {}


def number(value, default=None):
    """A number (never a bool), else `default`."""
    if isinstance(value, bool):
        return default
    return value if isinstance(value, (int, float)) else default


def ident(value) -> str:
    """An identifier: a string or a whole number ("F1", 3), else empty. A
    model writes `"id": 1` as readily as `"id": "1"`, and both name node 1;
    a boolean, a list or an object names nothing."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return value.strip() if isinstance(value, str) else ""


def idents(value) -> list[str]:
    """A list of identifiers, each as `ident` reads it."""
    return [i for i in (ident(v) for v in value) if i] if isinstance(value, list) else []
