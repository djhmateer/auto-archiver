from seleniumbase import SB

# Load the extension we created
    # extension_dir="secrets/twitter_extension"
with SB(
    uc=True,
    headless2=True,
    xvfb=True,
    # user_data_dir="secrets/antibot_user_data"
) as sb:
    sb.uc_open_with_reconnect("https://x.com/TangoBatDraws/status/1776776952298500381", 4)
    sb.save_screenshot("twitter_with_extension.png")
    print("Screenshot saved to twitter_with_extension.png")

