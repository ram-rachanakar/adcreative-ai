"""Regenerates shared/editor-parity/text-layout.json: golden outputs of the backend wrap/fit algorithm under a deterministic measure
(advance = 0.5 * size per char). The frontend test must reproduce them exactly, which pins the TS port to rendering/text.py.
Run: cd backend && .venv/bin/python tests/gen_text_parity.py"""
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.rendering import text as T

T._width = lambda t, fam, w, size, spacing: len(t) * size * 0.5 + spacing * max(0, len(t) - 1)
class _F:
    def getmetrics(self): return (10, 3)
T.fonts.load = lambda *a, **k: _F()

CASES = [
    dict(text="Hydrate all day with AeroBottle", family="Liberation Sans", weight=700, size=40, min_size=14, max_lines=3, line_height=1.15, box_w=300, box_h=120, spacing=0, uppercase=False),
    dict(text="Shop now", family="Liberation Sans", weight=700, size=20, min_size=9, max_lines=1, line_height=1.0, box_w=60, box_h=30, spacing=0, uppercase=True),
    dict(text="Supercalifragilisticexpialidocious", family="Liberation Serif", weight=400, size=30, min_size=10, max_lines=2, line_height=1.2, box_w=100, box_h=80, spacing=1, uppercase=False),
    dict(text="A very long headline that will never fit into this small box at all", family="DejaVu Sans", weight=400, size=24, min_size=12, max_lines=2, line_height=1.1, box_w=120, box_h=50, spacing=0, uppercase=False),
    dict(text="  spaced   out   words  ", family="Liberation Sans", weight=400, size=18, min_size=8, max_lines=3, line_height=1.3, box_w=90, box_h=200, spacing=2, uppercase=False),
]
out = []
for c in CASES:
    r = T.fit_text(c["text"], c["family"], c["weight"], c["size"], c["min_size"], c["max_lines"], c["line_height"], c["box_w"], c["box_h"], c["spacing"], c["uppercase"])
    out.append({"args": c, "expect": {"lines": r.lines, "font_size": r.font_size, "overflow": r.overflow, "shrunk": r.shrunk}})
dest = pathlib.Path(__file__).resolve().parents[2] / "shared" / "editor-parity" / "text-layout.json"
dest.write_text(json.dumps(out, indent=1))
print("wrote", dest, len(out))
