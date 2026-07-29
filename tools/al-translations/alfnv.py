# AL name hash (FNV variant) as used in Business Central XLIFF trans-unit ids.
#
# Reimplemented from nab-al-tools by Johannes Wikman, MIT licensed,
# Copyright (c) 2019 Johannes Wikman - extension/src/AlFunctions.ts, alFnv():
#   https://github.com/jwikman/nab-al-tools
# which documents it as the Roslyn hash method used by the BC platform.
#
# Not textbook FNV-1a: UTF-16LE input, signed 32-bit arithmetic, + Int32.MaxValue.
# Test vector: al_fnv("") == 18652612
def al_fnv(text: str) -> int:
    """AL/Roslyn-FNV wie in nab-al-tools `alFnv`: UTF-16LE, signed int32, + Int32.MaxValue."""
    data = text.encode("utf-16-le")
    h = 0x811C9DC5
    for b in data:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000          # als signed int32 deuten
    return h + 2147483647
