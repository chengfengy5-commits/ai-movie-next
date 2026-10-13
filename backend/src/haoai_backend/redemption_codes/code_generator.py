"""Default legacy-format redemption-code generator."""

from __future__ import annotations

import random
import string


def generate_code_string() -> str:
    """Return a code in the original HAOAI-XXXX-XXXX format."""

    alphabet = string.ascii_uppercase + string.digits
    first = "".join(random.choices(alphabet, k=4))
    second = "".join(random.choices(alphabet, k=4))
    return f"HAOAI-{first}-{second}"
