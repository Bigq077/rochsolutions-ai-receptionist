"""
Root conftest.py — loaded by pytest before any test module.
Calls load_dotenv() so environment variables from .env (including
ANTHROPIC_API_KEY, ASSEMBLYAI_API_KEY, etc.) are available to all tests
without needing to import app.main.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Use explicit path so the .env is found regardless of CWD when pytest starts.
load_dotenv(Path(__file__).parent / ".env", override=True)

# ---------------------------------------------------------------------------
# The SMS cost guard must never be switched on by a developer's .env.
# ---------------------------------------------------------------------------
# load_dotenv above runs with override=True, and the guard
# (app/notifications/sms_guard.py) is configured entirely by environment:
# SMS_TEST_NUMBERS diverts a listed recipient to a local inbox instead of
# Twilio. So a developer who lists their own handset -- which is the whole
# point of the variable -- silently changes what unrelated tests do.
#
# It already did. test_sms_log_names_the_real_destination redirects staff SMS
# to +447502211207 via EVAL_STAFF_SMS_TO, and that is exactly the handset a
# developer lists, so the guard intercepted the redirected send and the test's
# Twilio mock was never called. The suite went 98 -> 99 on nothing but a new
# line in .env.
#
# That is the documented failure mode of SMS_TEST_NUMBERS -- any listed number
# silently stops receiving SMS, with nothing at run time to say so -- landing
# on the test suite rather than on a patient. An argument for the warning in
# .env.example, not against the variable.
#
# Removed ONCE, here, rather than per-test. The first version of this was an
# autouse fixture that popped and restored these on every one of ~8,100 tests
# and imported sms_guard to reset its cache. Both were too big a lever: the
# import moved when app.notifications first loaded, and the per-test env
# churn interacted with monkeypatch teardown ordering. Each full-suite run
# then produced a DIFFERENT set of unrelated failures, every one of which
# passed in isolation. A one-time removal has no per-test side effects at all.
#
# A test that WANTS the guard active sets the variable with monkeypatch, which
# is unaffected by this.
# Set EMPTY, not removed. app/main.py line 3 calls load_dotenv() at import
# time, so any test importing app.main re-reads .env and would put a popped
# variable straight back. load_dotenv defaults to override=False and skips a
# key that is already PRESENT in os.environ -- presence, not truthiness -- so
# an empty string survives every later reload. Popping did not, which is why
# the first version of this still leaked.
for _sms_guard_var in ("SMS_TEST_NUMBERS", "SMS_SEGMENT_LIMIT", "SMS_SEGMENT_STRICT"):
    os.environ[_sms_guard_var] = ""
