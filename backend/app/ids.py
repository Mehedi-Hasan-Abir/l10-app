"""Id generation for app-created rows. Seed rows keep their seed ids (`i5`, `t12`, ...)."""

import secrets


def new_id(prefix: str) -> str:
    """`new_id("i")` -> `"i_3fa9c1d07b"` (prefixes used: `i`, `t`, `mt`)."""
    return f"{prefix}_{secrets.token_hex(5)}"
