from seleniumbase import SB
import random

# Use a random port to avoid conflicts
port = random.randint(9300, 9400)

with SB(
    uc=True,
    headless2=True,
    xvfb=True,
    incognito=True,
    chromium_arg=f"--remote-debugging-port={port},--no-sandbox,--disable-dev-shm-usage,--disable-gpu"
) as sb:
    sb.uc_open_with_reconnect("https://example.org", 4)
    sb.save_screenshot("example.png")
    print("Screenshot saved to example.png")

