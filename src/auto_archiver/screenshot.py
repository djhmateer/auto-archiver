from seleniumbase import SB
import tempfile
import os

url = "https://example.com"

# Create unique temp directory for user data
temp_dir = tempfile.mkdtemp(prefix="sb_test_")
print(f"Using temp directory: {temp_dir}")

# Test with chromium args to fix shared memory issues
print("\nTesting basic Chrome with explicit user-data-dir...")
try:
    with SB(headed=False, xvfb=True, user_data_dir=temp_dir,
            chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:
        sb.open(url)
        sb.save_screenshot("screenshot_basic.png")
        print(f"✓ Basic mode works - screenshot saved to screenshot_basic.png")
except Exception as e:
    print(f"✗ Basic mode failed: {e}")

# Clean up and create new temp dir for undetected mode
import shutil
shutil.rmtree(temp_dir, ignore_errors=True)
temp_dir2 = tempfile.mkdtemp(prefix="sb_test_uc_")

# Test with undetected mode
print(f"\nTesting undetected Chrome with temp dir: {temp_dir2}")
try:
    with SB(uc=True, headed=False, xvfb=True, user_data_dir=temp_dir2,
            chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.save_screenshot("screenshot_uc.png")
        print(f"✓ Undetected mode works - screenshot saved to screenshot_uc.png")
except Exception as e:
    print(f"✗ Undetected mode failed: {e}")
finally:
    shutil.rmtree(temp_dir2, ignore_errors=True)
