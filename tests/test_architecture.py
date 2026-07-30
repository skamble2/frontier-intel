"""The layering is an enforced invariant, not a convention.

Same philosophy as fli/ops/checks.py: if a rule matters, a machine checks it.
Import direction must be
    core <- storage <- ingestion|knowledge|intelligence <- orchestration
with ops cross-cutting (importable by anyone, importing no layer back).
A violation here means a lower layer has grown a dependency on a higher one,
which is exactly what makes layers stop being independently runnable.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

FLI = Path(__file__).resolve().parent.parent / "fli"

# `delivery` is the LAST MILE: it reads scored intelligence and renders it per
# audience. Ranked with validation because both consume every layer below and
# are consumed only by orchestration — and critically, nothing in the core
# pipeline may import delivery, so a rendering change can never alter what the
# system extracted or how it scored.
RANK = {"core": 0, "ops": 0, "storage": 1,
        "ingestion": 2, "knowledge": 2, "intelligence": 2,
        "validation": 3, "delivery": 3, "orchestration": 4}

# ops is cross-cutting: anyone may import it, it may import only core.
ALLOWED_FROM_OPS = {"core", "storage"}


def _layer_of(path: Path) -> str | None:
    rel = path.relative_to(FLI).parts
    return rel[0] if len(rel) > 1 else None


def _imported_layers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("fli."):
                    out.add(a.name.split(".")[1])
            continue
        if mod and mod.startswith("fli.") and len(mod.split(".")) > 1:
            out.add(mod.split(".")[1])
    return out


class TestLayering(unittest.TestCase):
    def test_no_upward_imports(self):
        violations = []
        for py in sorted(FLI.rglob("*.py")):
            layer = _layer_of(py)
            if layer is None or layer not in RANK:
                continue
            for imported in _imported_layers(py):
                if imported not in RANK or imported == layer:
                    continue
                if layer == "ops":
                    if imported not in ALLOWED_FROM_OPS:
                        violations.append(f"{py.name}: ops -> {imported}")
                elif RANK[imported] > RANK[layer]:
                    violations.append(
                        f"{py.relative_to(FLI)}: {layer} -> {imported} (upward)")
        self.assertEqual([], violations, "\n".join(violations))

    def test_core_is_a_leaf(self):
        """core must not import any other fli layer - it is the bottom."""
        for py in sorted((FLI / "core").rglob("*.py")):
            self.assertEqual(
                set(), _imported_layers(py) - {"core"},
                f"{py.name} makes fli.core depend on another layer")


if __name__ == "__main__":
    unittest.main()
