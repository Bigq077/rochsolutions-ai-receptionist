# test_sheets.py
"""
Test Google Sheets connection and write a test row.
Run this to verify your credentials and sheet ID are correct.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

print("=" * 60)
print("  GOOGLE SHEETS CONNECTION TEST")
print("=" * 60)

# Check environment variables
print("\n1. Checking environment variables...")

sheet_id = os.getenv("GOOGLE_SHEETS_ID", "").strip()
service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
summary_tab = os.getenv("GOOGLE_SHEETS_SUMMARY_TAB", "CallSummaries").strip()

if not sheet_id:
    print("❌ GOOGLE_SHEETS_ID is not set!")
    print("   Add this to your .env file:")
    print("   GOOGLE_SHEETS_ID=your-google-sheet-id-here")
    sys.exit(1)
else:
    print(f"✅ GOOGLE_SHEETS_ID: {sheet_id[:20]}...")

if not service_account:
    print("❌ GOOGLE_SERVICE_ACCOUNT_JSON is not set!")
    print("   Add your Google service account JSON to .env")
    sys.exit(1)
else:
    print(f"✅ GOOGLE_SERVICE_ACCOUNT_JSON: {len(service_account)} characters")

print(f"✅ Summary tab: {summary_tab}")

# Test import
print("\n2. Testing imports...")
try:
    from app.tools.handoff import append_summary_row
    from app.tools.call_summary import build_call_summary, summary_to_sheet_row
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

# Build test summary
print("\n3. Building test summary...")
try:
    test_session = {
        "call_sid": "TEST_CALL_123",
        "session_id": "test_session",
        "state": "TRIAGE",
        "selected_location": "alcester",
        "collected": {
            "name": "Test Patient",
            "phone": "+447870166861",
            "patient_type": "NEW",
        },
        "turns": [],
    }
    
    summary = build_call_summary(test_session)
    row = summary_to_sheet_row(summary)
    
    print(f"✅ Summary built: {len(row)} columns")
    print(f"   Outcome: {summary.get('outcome')}")
    print(f"   Call SID: {summary.get('meta', {}).get('call_sid')}")
except Exception as e:
    print(f"❌ Failed to build summary: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Send to Google Sheets
print("\n4. Sending test row to Google Sheets...")
try:
    success = append_summary_row(row, tab_name=summary_tab)
    
    if success:
        print(f"✅ SUCCESS! Test row sent to '{summary_tab}' tab")
        print(f"\n   Check your Google Sheet:")
        print(f"   https://docs.google.com/spreadsheets/d/{sheet_id}")
    else:
        print("❌ Failed to send row (check logs above)")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ Error sending to Sheets: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("  ✅ ALL TESTS PASSED!")
print("=" * 60)
print("\nYour call summaries should now work automatically.")
print("Make a test call to verify!")
