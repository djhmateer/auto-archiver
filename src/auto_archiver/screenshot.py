from seleniumbase import SB

url = "https://example.com"

# Test with chromium args to fix shared memory issues
print("Testing basic Chrome with no-sandbox...")
try:
    with SB(headed=False, xvfb=True, chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:
        sb.open(url)
        sb.save_screenshot("screenshot_basic.png")
        print(f"✓ Basic mode works - screenshot saved to screenshot_basic.png")
except Exception as e:
    print(f"✗ Basic mode failed: {e}")

# Test with undetected mode
print("\nTesting undetected Chrome with no-sandbox...")
try:
    with SB(uc=True, headed=False, xvfb=True, chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.save_screenshot("screenshot_uc.png")
        print(f"✓ Undetected mode works - screenshot saved to screenshot_uc.png")
except Exception as e:
    print(f"✗ Undetected mode failed: {e}")
