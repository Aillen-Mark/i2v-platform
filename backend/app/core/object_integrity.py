"""Bounded byte-count and SHA-256 verification for object streams."""

from dataclasses import dataclass
from hashlib import sha256


class ObjectSizeMismatch(ValueError):
    """The stream exceeded its byte budget."""


@dataclass(frozen=True, slots=True)
class ObjectDigest:
    size: int
    sha256: str


def stream_digest(chunks, maximum: int, *, output=None, check=None) -> ObjectDigest:
    size = 0
    digest = sha256()
    for chunk in chunks:
        if check is not None:
            check(len(chunk))
        size += len(chunk)
        if size > maximum:
            raise ObjectSizeMismatch("stream exceeded declared or configured size")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    return ObjectDigest(size, digest.hexdigest())
