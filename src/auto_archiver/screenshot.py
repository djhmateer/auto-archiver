from seleniumbase import SB

url = "https://example.com"

# Test without undetected mode first
print("Testing basic Chrome...")
try:
    with SB(headed=False, xvfb=True) as sb:
        sb.open(url)
        sb.save_screenshot("screenshot_basic.png")
        print(f"✓ Basic mode works - screenshot saved to screenshot_basic.png")
except Exception as e:
    print(f"✗ Basic mode failed: {e}")

# Test with undetected mode
print("\nTesting undetected Chrome...")
try:
    with SB(uc=True, headed=False, xvfb=True) as sb:
        sb.uc_open_with_reconnect(url, 4)
        sb.save_screenshot("screenshot_uc.png")
        print(f"✓ Undetected mode works - screenshot saved to screenshot_uc.png")
except Exception as e:
    print(f"✗ Undetected mode failed: {e}")
