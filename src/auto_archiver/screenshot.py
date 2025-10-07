from seleniumbase import SB

url = "https://example.com"

# Test WITHOUT user_data_dir
print("Testing basic Chrome without user-data-dir...")
try:
    with SB(headed=False, xvfb=True, chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:
        sb.open(url)
        sb.save_screenshot("screenshot_basic.png")
        print(f"✓ Basic mode works - screenshot saved to screenshot_basic.png")
except Exception as e:
    print(f"✗ Basic mode failed: {e}")

# Test with undetected mode WITHOUT user_data_dir
print("\nTesting undetected Chrome without user-data-dir...")
try:
    with SB(uc=True, headed=False, xvfb=True, chromium_arg="--disable-dev-shm-usage,--no-sandbox") as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.save_screenshot("screenshot_uc.png")
        print(f"✓ Undetected mode works - screenshot saved to screenshot_uc.png")
except Exception as e:
    print(f"✗ Undetected mode failed: {e}")
