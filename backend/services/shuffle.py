"""Deterministic A-E option shuffling tied to a seed.

The seed is generated server-side per question fetch, embedded in the answer
token, and used both at render time and at answer-resolve time so we can map
the user's clicked display letter back to the original letter without any
additional state.
"""

import random
import secrets


def make_seed() -> int:
    return secrets.randbits(31)


def shuffle_letters(seed: int, letters: list[str]) -> list[str]:
    """Return `letters` permuted deterministically by `seed`.
    seed=0 is a sentinel meaning 'no shuffle' (returns letters as-is)."""
    if seed == 0:
        return list(letters)
    rng = random.Random(seed)
    out = list(letters)
    rng.shuffle(out)
    return out


def display_to_original(seed: int, display_letter: str, original_letters: list[str]) -> str:
    """Given the post-shuffle letter the user clicked, recover the original letter.

    `original_letters` is the canonical list (e.g. ['A','B','C','D','E']) in DB order.
    The shuffle maps display position → original letter:
        shuffled[i] = original_letters[perm[i]]
    The user clicks display_letter (e.g. 'C' = 3rd shown option). We find which
    original letter is at that display position.
    """
    shuffled = shuffle_letters(seed, original_letters)
    idx = ord(display_letter) - ord("A")
    if idx < 0 or idx >= len(shuffled):
        raise ValueError(f"display_letter {display_letter!r} out of range")
    return shuffled[idx]


def original_to_display(seed: int, original_letter: str, original_letters: list[str]) -> str:
    """Inverse: given the canonical correct letter, return what display letter it was shown as."""
    shuffled = shuffle_letters(seed, original_letters)
    idx = shuffled.index(original_letter)
    return chr(ord("A") + idx)


def build_orig_to_display_map(seed: int, original_letters: list[str]) -> dict[str, str]:
    """Build {original_letter: display_letter} mapping for the given shuffle."""
    shuffled = shuffle_letters(seed, original_letters)
    return {orig: chr(ord("A") + i) for i, orig in enumerate(shuffled)}


_LETTER_REF_RE = __import__("re").compile(r"\(([A-E])\)")


def remap_letter_refs(text: str | None, seed: int, original_letters: list[str]) -> str | None:
    """Rewrite "(A)", "(B)" etc. in `text` to match the shuffled display order.

    Statement-enumerator references like (1), (2), 一、 are NOT touched.
    """
    if not text:
        return text
    omap = build_orig_to_display_map(seed, original_letters)

    def _repl(m):
        orig = m.group(1)
        return f"({omap.get(orig, orig)})"

    return _LETTER_REF_RE.sub(_repl, text)
