"""
Quick test script for Facebook screenshot OCR detection.
Usage:
    poetry run python image-recog.py [path/to/screenshot.png]

Defaults to /mnt/c/tmp/disabled.png if no argument given.
"""
import sys
import os

# Allow running from the project root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from auto_archiver.modules.screenshot_enricher.screenshot_enricher import (
    check_screenshot_for_facebook_issues,
    FACEBOOK_WARNING_PHRASES,
)

screenshot_file = sys.argv[1] if len(sys.argv) > 1 else "/mnt/c/tmp/disabled.png"

print(f"Scanning: {screenshot_file}")
print(f"Looking for phrases: {FACEBOOK_WARNING_PHRASES}\n")

if not os.path.exists(screenshot_file):
    print(f"ERROR: File not found: {screenshot_file}")
    sys.exit(1)

issues = check_screenshot_for_facebook_issues(screenshot_file)

if issues:
    print(f"WARNING - detected {len(issues)} issue(s):")
    for phrase in issues:
        print(f"  - '{phrase}'")
else:
    print("No warning/login phrases detected.")
