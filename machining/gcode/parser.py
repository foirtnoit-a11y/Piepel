"""Lexer for Fanuc / Haas dialect G-code.

Turns raw NC text into `Block` records. Deliberately dumb: it understands the
*shape* of a program (words, comments, block-delete, tape marks) and nothing
about what the codes mean. Meaning lives in interp.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# A word is a letter followed by a number. Fanuc tolerates whitespace between
# them ("G 01"), and values may be signed, decimal, or leading-dot.
_WORD = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+\.?\d*|\.\d+))")

# Fanuc uses (parenthesised) comments; Haas also accepts ; to end of line.
_PAREN_COMMENT = re.compile(r"\(([^)]*)\)")


@dataclass(frozen=True)
class Word:
    letter: str  # always upper-case
    value: float

    def __str__(self) -> str:
        return f"{self.letter}{self.value:g}"


@dataclass
class Block:
    """One line of NC code."""

    line_no: int  # 1-based, matches what a text editor shows
    raw: str
    words: list[Word] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    block_delete: bool = False  # leading '/'
    tape_mark: bool = False  # the '%' at start/end of a program

    def get(self, letter: str) -> float | None:
        """First value for `letter`, or None."""
        letter = letter.upper()
        for w in self.words:
            if w.letter == letter:
                return w.value
        return None

    def all(self, letter: str) -> list[float]:
        """Every value for `letter`. G and M words legitimately repeat."""
        letter = letter.upper()
        return [w.value for w in self.words if w.letter == letter]

    def has(self, letter: str) -> bool:
        return self.get(letter) is not None

    def g_codes(self) -> list[float]:
        return self.all("G")

    def m_codes(self) -> list[float]:
        return self.all("M")

    def has_g(self, *codes: float) -> bool:
        gs = self.g_codes()
        return any(abs(g - c) < 1e-9 for g in gs for c in codes)

    def has_m(self, *codes: float) -> bool:
        ms = self.m_codes()
        return any(abs(m - c) < 1e-9 for m in ms for c in codes)

    @property
    def is_empty(self) -> bool:
        return not self.words and not self.tape_mark


def parse(text: str) -> list[Block]:
    """Parse a whole NC program into blocks, one per source line."""
    blocks: list[Block] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        blocks.append(parse_line(raw, i))
    return blocks


def parse_line(raw: str, line_no: int) -> Block:
    blk = Block(line_no=line_no, raw=raw.rstrip("\r\n"))
    line = blk.raw

    # Tape marks delimit the program on real controls.
    if line.strip() == "%":
        blk.tape_mark = True
        return blk

    # Pull out parenthesised comments wherever they appear.
    def _grab(m: re.Match) -> str:
        blk.comments.append(m.group(1).strip())
        return " "

    line = _PAREN_COMMENT.sub(_grab, line)

    # Semicolon comment runs to end of line (Haas / ISO EOB style).
    if ";" in line:
        line, _, tail = line.partition(";")
        tail = tail.strip()
        if tail:
            blk.comments.append(tail)

    stripped = line.strip()
    if stripped.startswith("/"):
        blk.block_delete = True
        stripped = stripped[1:]

    for m in _WORD.finditer(stripped):
        blk.words.append(Word(m.group(1).upper(), float(m.group(2))))

    return blk


def program_number(blocks: list[Block]) -> int | None:
    """The O-word that names the program, if present."""
    for b in blocks:
        o = b.get("O")
        if o is not None:
            return int(o)
    return None
