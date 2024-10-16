from playwright.sync_api import sync_playwright
import sys
import os
import time

def run(playwright):
    # for testing run this python file directly and comment out and in
    url = sys.argv[1]
    tmp_dir = sys.argv[2]

    # url = "https://m.vk.com/wall-77310446_451431?post_add"
    # tmp_dir = "/mnt/c/dev/v6-auto-archiver/tmp"


    dev_executable_path = '/home/dave/.cache/ms-playwright/firefox-1458/firefox/firefox'
    prod_executable_path = '/home/dave/.cache/ms-playwright/firefox-1429/firefox/firefox'
    if os.path.exists(dev_executable_path):
        executable_path = dev_executable_path
    elif os.path.exists(prod_executable_path):
        executable_path = prod_executable_path
    else:
        print('problem - no firefox found')
        exit()

    
    browser = playwright.firefox.launch(headless=False, executable_path=executable_path) 

    context = browser.new_context(
        #  user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    #     # viewport={"width": 1920, "height": 3000}  # Set the viewport to a longer screen size
         viewport={"width": 1224, "height": 2500}  # Set the viewport to a longer screen size
    )

   # Disable automation flags that might disable ads
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    """)

    page = context.new_page()
    # page.set_default_timeout(60000)  # Set timeout to 60 seconds (or longer)

    # print('goto url')
    page.goto(url, wait_until='domcontentloaded', timeout=60000)
    # page.goto(url, timeout=60000)

    # why is browser closing then when I run this it fails with: Taget page, context or browser has been closed
    # need a timeout to show video starting

    # https://m.vk.com/wall-77310446_451431?post_add closes after 4 or 5 secs.. don't know why
    # print('wait_for_timeout')
    page.wait_for_timeout(2000)


    # telegram needs a wait for media to load

    # print('take screenshot')
    page.screenshot(path=tmp_dir + f'/1.png', full_page=True)

    exit()


def main():
    with sync_playwright() as playwright:
        run(playwright)

# Run the script
if __name__ == "__main__":
    main()
