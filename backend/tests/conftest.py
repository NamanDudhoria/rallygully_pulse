"""Point the app at a throwaway database before any app module is imported."""
import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
# PULSE_TEST_DATABASE_URL runs the suite on another engine (e.g. a scratch Postgres database); default is SQLite.
os.environ["PULSE_DATABASE_URL"] = os.environ.get("PULSE_TEST_DATABASE_URL") or f"sqlite:///{_db.name}"
os.environ["PULSE_RUN_SCHEDULER"] = "0"
os.environ["PULSE_DISTRICT_INBOX_DIR"] = tempfile.mkdtemp()
