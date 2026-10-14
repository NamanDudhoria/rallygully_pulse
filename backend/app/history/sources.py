"""Which venue and month each workbook belongs to, and content fingerprints for duplicates.

Venue names in the sales files are trading names that changed over time. Each mapping below is
backed by evidence in the files themselves (see EVIDENCE), not by name similarity alone.
"""
import calendar
import hashlib
import re
from datetime import date

from openpyxl import load_workbook

# (pattern on the file name, venue code in Pulse or None if not a Pulse venue, label). Order matters:
# the Sec 83 Drive or Kick files must be matched before the Dwarka Drive or Kick ones.
FILE_VENUES = [
    (r"drive or kick[\W_]+83|dok 83", "S83", "Drive or Kick | Sec 83"),
    (r"drive (?:and|or) kick|dok dwarka", "DW19", "Drive or Kick | Dwarka (Sector 19)"),
    (r"tirang|dwarka 10|dwarka sec 10", "DW10", "Tirang Sports Arena | Dwarka (Sector 10)"),
    (r"klj 83|sec 83|_ 83 _|_ 83, ggn", "GG83", "The Blue Court | Sec 83 (KLJ)"),
    (r"(?<![a-z])eok(?![a-z])", "EOK", "East of Kailash"),
    (r"(?<![a-z])lcr(?![a-z])", "LCR", "Law College Road, Pune"),
    (r"dpsg", "DPSG", "1011 | DPSG Palam Vihar"),
    (r"panchsheel", "PSP", "Panchsheel Enclave"),
    (r"commons", "NOI49", "Commons x Delhi Padel Collective, Noida"),
    (r"powerplay", "BNR", "Powerplay Park, Pune"),
    (r"chandigarh", None, "Chandigarh Sec 37"),
]

EVIDENCE = {
    "DW10": "Tirang Mar–Sep and Dwarka 10 Oct–Feb totals equal the trackers' 'Sector 10 Dwarka' / 'Tirang Sports "
            "Arena | Dwarka' row (same row 1 in both FY trackers); no other Dwarka 10 file exists for Mar.",
    "GG83": "Sec 83 Oct–Mar totals equal the FY25-26 'Sector 83 Gurgaon' row; '83'/'83 GGN'/'KLJ 83' Apr–Sep equal "
            "the FY26-27 'The Blue Court | Sec 83' row (Pickleball + Turf, as in the venue list).",
    "DW19": "Drive and Kick / Drive or Kick Oct–Mar equal the FY25-26 'Sector 19 Dwarka Drive Kick' row; DOK Dwarka "
            "Apr–Sep equal FY26-27 'Drive or Kick | Dwarka'.",
    "S83": "Drive or Kick 83 Jun/Jul/Sep equal FY26-27 'Drive or Kick | Sec 83' (Turf + Padel = venue list 'Sec 83').",
    "NOI49": "Profit split lines name 'SD Vidya School's Share' — The SD Vidya School is Noida Sec 49 in the venue list.",
    "BNR": "ASSUMED: FY25-26 'Jan Target' lists 'Baner Pune' (5 courts) as upcoming; Powerplay Park, Pune starts the "
           "next month with Pickleball + Turf + Padel (the venue list's Baner sports) and Baner never appears again.",
}

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
MONTHS.update({"sept": 9, "june": 6, "july": 7})


def venue_for_file(name: str) -> tuple[str | None, str] | None:
    low = name.lower()
    for pat, code, label in FILE_VENUES:
        if re.search(pat, low):
            return code, label
    return None


def month_from_text(text: str | None) -> int | None:
    for w in re.findall(r"[A-Za-z]+", text or ""):
        m = MONTHS.get(w.lower())
        if m:
            return m
    return None


def year_for(month: int) -> int:
    """The files span Jul 2025 – Sep 2026: Oct–Dec are 2025, Jan–Sep 2026 (checked against cell dates)."""
    return 2025 if month >= 10 else 2026


def month_bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def fingerprint(path: str) -> str:
    """Hash of every non-empty cell value — identical content ⇒ identical hash, regardless of file metadata."""
    wb = load_workbook(path, data_only=True, read_only=True)
    h = hashlib.sha256()
    for ws in wb.worksheets:
        for r_i, row in enumerate(ws.iter_rows(values_only=True)):
            for c_i, v in enumerate(row):
                if v not in (None, ""):
                    h.update(f"{ws.title}|{r_i}|{c_i}|{v!r}\n".encode())
    wb.close()
    return h.hexdigest()
