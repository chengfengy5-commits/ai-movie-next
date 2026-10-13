"""Legacy-format random redemption-code generation."""

from __future__ import annotations

import random
import string


def _generate_code_string() -> str:
    """Generate one code in the legacy HAOAI-XXXX-XXXX format."""

    alphabet = string.ascii_uppercase + string.digits
    part1 = "".join(random.choices(alphabet, k=4))
    part2 = "".join(random.choices(alphabet, k=4))
    return f"HAOAI-{part1}-{part2}"
