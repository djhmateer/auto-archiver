from seleniumbase import SB

url = "https://example.com"

with SB(uc=True, headed=False, xvfb=True) as sb:
    sb.uc_open_with_reconnect(url, 4)
    sb.save_screenshot("screenshot.png")
    print(f"Screenshot saved to screenshot.png")
