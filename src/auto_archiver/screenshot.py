from seleniumbase import SB
import tempfile
import uuid

url = "https://example.com"

# Create a unique temp directory
unique_dir = f"/tmp/chrome_test_{uuid.uuid4().hex[:8]}"

# Test with explicit unique directory
print(f"Testing basic Chrome with unique dir: {unique_dir}")
try:
    with SB(headed=False, xvfb=True,
            chromium_arg=f"--disable-dev-shm-usage,--no-sandbox,--user-data-dir={unique_dir}") as sb:
        sb.open(url)
        sb.save_screenshot("screenshot_basic.png")
        print(f"✓ Basic mode works - screenshot saved to screenshot_basic.png")
except Exception as e:
    print(f"✗ Basic mode failed: {e}")
finally:
    import shutil
    shutil.rmtree(unique_dir, ignore_errors=True)

# Test with undetected mode
unique_dir2 = f"/tmp/chrome_test_uc_{uuid.uuid4().hex[:8]}"
print(f"\nTesting undetected Chrome with unique dir: {unique_dir2}")
try:
    with SB(uc=True, headed=False, xvfb=True,
            chromium_arg=f"--disable-dev-shm-usage,--no-sandbox,--user-data-dir={unique_dir2}") as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.save_screenshot("screenshot_uc.png")
        print(f"✓ Undetected mode works - screenshot saved to screenshot_uc.png")
except Exception as e:
    print(f"✗ Undetected mode failed: {e}")
finally:
    import shutil
    shutil.rmtree(unique_dir2, ignore_errors=True)
