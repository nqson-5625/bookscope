"""Create a local Docker database password without displaying it."""

from pathlib import Path
import re
import secrets


ROOT = Path(__file__).resolve().parents[1]
target = ROOT / ".env"
if target.exists():
    contents = target.read_text(encoding="utf-8")
else:
    contents = (ROOT / ".env.example").read_text(encoding="utf-8")

pattern = re.compile(r"^BOOKSCOPE_DB_PASSWORD=(.*)$", re.MULTILINE)
match = pattern.search(contents)
if match is None or not match.group(1).strip():
    password = secrets.token_urlsafe(32)
    if match is None:
        contents = contents.rstrip("\n") + f"\nBOOKSCOPE_DB_PASSWORD={password}\n"
    else:
        contents = contents[:match.start(1)] + password + contents[match.end(1):]
    target.write_text(contents, encoding="utf-8")
    print("Local database password saved in .env")
else:
    print("Existing local database password kept in .env")
