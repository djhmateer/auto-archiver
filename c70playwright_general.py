from playwright.sync_api import sync_playwright
import sys
import os

def run(playwright):
    url = sys.argv[1]
    tmp_dir = sys.argv[2]

    # needs have an up to date profile in there copied from dev
    # ./chrome from:
    # /home/dave/.cache/ms-playwright/chromium-1076/chrome-linux/
    data_dir = '/home/dave/.config/chromium'

    dev_executable_path = '/home/dave/.cache/ms-playwright/chromium-1076/chrome-linux/chrome'
    prod_executable_path = '/home/dave/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'
    laptop_executable_path = '/home/dave/.cache/ms-playwright/chromium-1129/chrome-linux/chrome'
    if os.path.exists(dev_executable_path):
        print('1076 found')
        executable_path = dev_executable_path
    elif os.path.exists(prod_executable_path):
        print('1091 found')
        executable_path = prod_executable_path
    elif os.path.exists(laptop_executable_path):
        print('1129 found')
        executable_path = laptop_executable_path
    else:
        print('problem - no chromium found')
        exit()

    # chromium I've seen fail on telegram videos with an Error code: No providers
    browser = playwright.chromium.launch_persistent_context(data_dir,
                                        headless=False,
                                        executable_path = executable_path,
                                        viewport={"width": 1224, "height": 3000}
                                        )
    page = browser.new_page()

    try:
        page.goto(url,  wait_until='domcontentloaded', timeout=20000)
    except:
        print('problem with page.goto')
        exit()

    # telegram needs a wait for media to load
    page.wait_for_timeout(4000)

    page.screenshot(path=tmp_dir + '/c70.png', full_page=True)
    page.wait_for_timeout(1000)

    exit()


def main():
    with sync_playwright() as playwright:
        run(playwright)

# Run the script
if __name__ == "__main__":
    main()
