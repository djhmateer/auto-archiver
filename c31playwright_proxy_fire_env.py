from playwright.sync_api import sync_playwright
import sys
from dotenv import load_dotenv
import os

def run(playwright):
    url = sys.argv[1]
    print("url: ", url)

    tmp_dir = sys.argv[2]
    print("tmp_dir: ", tmp_dir)

    # need an installed SSL cert to communicate with the residential proxy
    # so have to use a personal context ie a profile with the cert installed

    # DEV
    context = playwright.firefox.launch_persistent_context('/home/dave/.mozilla/firefox/raogzvo8.my-playwright-profile',

    # SERVER
    # context = playwright.firefox.launch_persistent_context('/home/dave/profile9',
        headless=False,
        proxy={
            "server": os.getenv('SERVER'),  # Replace with your proxy server
            "username": os.getenv('USERNAME'),
            "password": os.getenv("PASSWORD") 
        },
        viewport={"width": 1224, "height": 3000} 
    )

     # Set a user-agent to mimic a real user in the browser context
    # context = browser.new_context(
    #     user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    # )

    # fir oersustebt have to cmment out.
    # context = browser.new_context(
    #     user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    #     # viewport={"width": 1920, "height": 3000}  # Set the viewport to a longer screen size
    #     viewport={"width": 1224, "height": 3000}  # Set the viewport to a longer screen size
    # )

   # Disable automation flags that might disable ads
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    """)

    page = context.new_page()

    print("go to youtube")
    page.goto(url, wait_until='domcontentloaded')
    print("end goto")




    # get rid of cookies popup and also a handy timeout to let page load
    # try:
    #     page.wait_for_selector('button[aria-label="Reject the use of cookies and other data for the purposes described"]')
    #     page.click('button[aria-label="Reject the use of cookies and other data for the purposes described"]')
    # except Exception as e:
    #     print("no cookies popup which may be fine")


    # want the video to play!
    # page.wait_for_timeout(120000)

    # Reload the page
    # print("reload")
    # page.reload()
    # print("end reload")

    # sometimes there is an ad quite quickly
    # page.wait_for_timeout(1000)

    try_read_more = True
    for i in range(1, 9):
        print(i)
        page.screenshot(path=tmp_dir + f'/{i}.png', full_page=True)
        page.wait_for_timeout(1000)

        # read more click button
        if try_read_more:
            try:
                page.wait_for_selector('tp-yt-paper-button#expand', timeout=2000)
                page.click('tp-yt-paper-button#expand')
                try_read_more = False
            except Exception as e:
                print("no read more button - probably not good")

    # Sort by newest first comments
    # Click the dropdown to open the sort menu
    try:
        page.click('#trigger')
        # Wait for the dropdown to be visible
        page.wait_for_selector('tp-yt-paper-item .item:has-text("Newest first")')

        # Click the "Newest first" option
        page.click('tp-yt-paper-item .item:has-text("Newest first")')
    except:
        print("no newest first comments - not good")
        # exit()


    # do lots of screenshots
    for i in range(20, 50):
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
