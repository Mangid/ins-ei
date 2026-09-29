import ast
from pathlib import Path

def test_backend_compiles():
    ast.parse(Path("app.py").read_text())

def test_portal_assets_present():
    base=Path("/app/customer-portal")
    assert (base/"index.html").is_file()
    assert (base/"portal.css").is_file()
    assert (base/"portal.js").is_file()
