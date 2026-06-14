import ast
import builtins
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = [
    "main.py",
    "database.py",
    "embeds.py",
    "views.py",
    "config.py",
    "permissions.py",
    "participants.py",
    "mapping_analysis.py",
    "ocr.py",
]


def compile_files():
    for filename in PYTHON_FILES:
        py_compile.compile(str(ROOT / filename), doraise=True)


def check_missing_names(filename: str):
    source = (ROOT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=filename)
    assigned = set()
    used = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                assigned.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assigned.add(node.name)
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            assigned.update(arg.arg for arg in args)
            if node.args.vararg:
                assigned.add(node.args.vararg.arg)
            if node.args.kwarg:
                assigned.add(node.args.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            assigned.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            assigned.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assigned.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assigned.add(alias.asname or alias.name)

    ignored = set(dir(builtins)) | {"__name__"}
    return sorted(used - assigned - ignored)


def main():
    compile_files()
    missing = check_missing_names("main.py")
    if missing:
        raise SystemExit("Nombres no definidos en main.py: " + ", ".join(missing))
    print("Bot checks OK")


if __name__ == "__main__":
    main()
