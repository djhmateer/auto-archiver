from playwright.sync_api import sync_playwright
import sys

def run(playwright):
    url = sys.argv[1]
    tmp_dir = sys.argv[2]

    # needs have an up to date profile in there copied from dev
    # ./chrome from:
    # /home/dave/.cache/ms-playwright/chromium-1076/chrome-linux/
    data_dir = '/home/dave/.config/chromium'
    browser = playwright.chromium.launch_persistent_context(data_dir,
                                        headless=False,
                                        # dev
                                        # executable_path = '/home/dave/.cache/ms-playwright/chromium-1076/chrome-linux/chrome'
                                        # prod
                                        executable_path = '/home/dave/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'
                                        )
    page = browser.new_page()

    page.goto(url,  wait_until='domcontentloaded', timeout=60000)

    for i in range(1, 2):
        print(i)
        page.screenshot(path=tmp_dir + f'/{i}.png', full_page=True)
        page.wait_for_timeout(1000)

    exit()


def main():
    with sync_playwright() as playwright:
        run(playwright)

# Run the script
if __name__ == "__main__":
    main()
