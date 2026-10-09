"""Point the app at a throwaway database before any app module is imported."""
import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["PULSE_DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["PULSE_RUN_SCHEDULER"] = "0"
os.environ["PULSE_DISTRICT_INBOX_DIR"] = tempfile.mkdtemp()
