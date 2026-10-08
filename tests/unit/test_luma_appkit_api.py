"""The kit's public surface must agree with itself, without importing GTK.

`luma_appkit/__init__.py` names its widgets twice: in `__all__` and in the set
inside `__getattr__` that lazily resolves them. A name in one and not the other
is either invisible to `import *` or unimportable by name — and the second
kind fails only on a machine with the toolkit, which is not where the unit
suite usually runs. This reads both lists and the modules' definitions as
source, so the disagreement is caught here.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "src/luma-platform/appkit/luma_appkit"


def _defined(module: Path) -> set[str]:
    tree = ast.parse(module.read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


class PublicSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.init = (KIT / "__init__.py").read_text()
        tree = ast.parse(self.init)
        self.exported = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                self.exported = {ast.literal_eval(e) for e in node.value.elts}
        self.lazy = set()
        for match in re.finditer(r"if name in \{(.*?)\}:", self.init, re.S):
            self.lazy |= set(re.findall(r'"(\w+)"', match.group(1)))
        self.lazy |= set(re.findall(r'if name == "(\w+)":', self.init))
        self.eager = set(re.findall(r"^from \.\w+ import ([^\n]+)", self.init, re.M))
        self.eager = {n.strip() for line in self.eager for n in line.split(",")}
        self.widgets = set().union(*(_defined(p) for p in KIT.glob("*.py")))

    def test_every_lazy_name_is_exported_and_defined(self) -> None:
        self.assertEqual(sorted(self.lazy - self.exported), [], "lazily resolved but not in __all__")
        self.assertEqual(sorted(self.lazy - self.widgets), [], "lazily resolved but neither widgets.py nor menus.py defines it")

    def test_every_exported_name_resolves(self) -> None:
        unresolvable = self.exported - self.lazy - self.eager
        self.assertEqual(sorted(unresolvable), [], "in __all__ but neither imported nor lazily resolved")

    def test_widgets_import_every_gi_namespace_they_use(self) -> None:
        for module in (KIT / "widgets.py", ROOT / "src/prairie-core/prairie_ui/widgets.py"):
            code = "\n".join(line.split("#")[0] for line in module.read_text().splitlines())
            imported: set[str] = set()
            for m in re.finditer(r"from gi\.repository import ([^\n]+)", code):
                imported |= {x.strip().split(" as ")[0] for x in m.group(1).split(",") if x.strip()}
            used = set(re.findall(r"\b(GObject|Gdk|Gtk|Adw|Pango|PangoCairo|GLib|Gio|Graphene|Gsk|LumaUI)\.", code))
            self.assertEqual(sorted(used - imported), [], f"{module.name} uses a gi namespace it does not import")


if __name__ == "__main__":
    unittest.main()
