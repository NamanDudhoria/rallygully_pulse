"""Parse player lists pasted from a sheet / WhatsApp, or uploaded as .xlsx / .csv.

Accepts a header row (Name, Phone/Mobile/Number, Level) or none; without one each
row's columns are recognised by content. Output rows keep their source line so the
preview can point at what needs fixing.
"""
import csv
import io
import re

from ..audit import DomainError
from .customers import normalize_phone

MAX_ROWS = 500
MAX_BYTES = 2 * 1024 * 1024

LEVEL_WORDS = {
    "beginner": "beginner", "beg": "beginner", "b": "beginner", "novice": "beginner", "basic": "beginner",
    "intermediate": "intermediate", "inter": "intermediate", "int": "intermediate", "i": "intermediate",
    "mid": "intermediate", "medium": "intermediate",
    "advanced": "advanced", "adv": "advanced", "a": "advanced", "pro": "advanced", "expert": "advanced",
}
HEADERS = {
    "name": ("name", "player", "full name", "player name", "customer"),
    "phone": ("phone", "mobile", "number", "contact", "whatsapp", "phone number", "mobile number", "ph", "mob"),
    "level": ("level", "skill", "skill level", "category", "grade"),
}
_PHONE_RE = re.compile(r"(\+?\d[\d\s\-()]{8,16}\d)")


def level_of(value: str) -> str | None:
    return LEVEL_WORDS.get((value or "").strip().lower().rstrip("."))


def _phone_or_none(value: str) -> str | None:
    try:
        return normalize_phone(value)
    except DomainError:
        return None


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))  # Excel stores phone numbers as floats
    return str(v).strip()


def rows_from_text(text: str) -> list[list[str]]:
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            out.append([])
            continue
        if "\t" in line:
            cells = line.split("\t")
        elif "," in line or ";" in line:
            cells = next(csv.reader([line], delimiter=";" if line.count(";") > line.count(",") else ","))
        else:
            # Free text such as "Meera Kapoor 98666 66666 advanced" or "+91 98666 66666 - Meera"
            m = _PHONE_RE.search(line)
            if m:
                rest = (line[:m.start()] + " " + line[m.end():]).strip(" -:|·")
                words = rest.split()
                lvl = [w for w in words if level_of(w) and len(w) > 1]
                name = " ".join(w for w in words if w not in lvl).strip(" -:|·")
                cells = [name, m.group(1)] + lvl[:1]
            else:
                cells = [line]
        out.append([c.strip() for c in cells])
    return out


def rows_from_file(filename: str, data: bytes) -> list[list[str]]:
    if len(data) > MAX_BYTES:
        raise DomainError("File is larger than 2 MB.", 422)
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001 — any parse failure is a user-facing format error
            raise DomainError("Couldn't read that Excel file. Save it as .xlsx or .csv and try again.", 422) from None
        ws = wb.worksheets[0]
        rows = [[_cell(v) for v in r] for r, _ in zip(ws.iter_rows(values_only=True), range(MAX_ROWS + 50))]
        wb.close()
        return rows
    if name.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", errors="replace")
        return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text))]
    if name.endswith(".xls"):
        raise DomainError("Old .xls files aren't supported — save as .xlsx or .csv.", 422)
    raise DomainError("Upload an .xlsx or .csv file.", 422)


def _header_map(row: list[str]) -> dict[str, int] | None:
    if any(_phone_or_none(c) for c in row):
        return None
    found = {}
    for i, c in enumerate(row):
        key = c.strip().lower()
        for field, names in HEADERS.items():
            if field not in found and key in names:
                found[field] = i
    return found if "phone" in found else None


def parse(rows: list[list[str]]) -> list[dict]:
    """→ [{line, name, phone, level, error}] with blank rows dropped."""
    indexed = [(i + 1, r) for i, r in enumerate(rows) if any(c.strip() for c in r)]
    if not indexed:
        return []
    header = _header_map(indexed[0][1])
    if header:
        indexed = indexed[1:]
    if len(indexed) > MAX_ROWS:
        raise DomainError(f"Up to {MAX_ROWS} players at a time.", 422)
    out = []
    for line, r in indexed:
        name = phone = level = None
        if header:
            get = lambda f: r[header[f]].strip() if f in header and header[f] < len(r) else ""  # noqa: E731
            phone, name = _phone_or_none(get("phone")), get("name") or None
            level = level_of(get("level"))
            raw_phone = get("phone")
        else:
            raw_phone = ""
            rest = []
            for c in r:
                if not c:
                    continue
                if phone is None and _phone_or_none(c):
                    phone, raw_phone = _phone_or_none(c), c
                elif level is None and level_of(c):
                    level = level_of(c)
                else:
                    rest.append(c)
            name = next((c for c in rest if not re.fullmatch(r"[\d\s+\-().]+", c)), None)
            if phone is None:
                raw_phone = next((c for c in rest if re.search(r"\d{5,}", c)), "")
        error = None if phone else ("Phone number missing" if not raw_phone else f"'{raw_phone}' isn't a valid mobile")
        out.append({"line": line, "name": name, "phone": phone or raw_phone or None, "level": level, "error": error})
    return out
