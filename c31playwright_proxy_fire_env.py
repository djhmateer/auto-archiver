from playwright.sync_api import sync_playwright
import sys
from dotenv import load_dotenv
import os

def run(playwright):
    url = sys.argv[1]
    print("url: ", url)

    tmp_dir = sys.argv[2]
    print("tmp_dir: ", tmp_dir)

    # Launch the browser

    # browser = playwright.chromium.launch(
    # browser = playwright.firefox.launch(
    #     headless=False,
    #     proxy={
    #         "server": "http://172.23.16.1:24002",  # Replace with your proxy server
    #     }
    # )

    # need an installed SSL cert to communicate with the residential proxy
    # so have to use a personal context ie a profile with the cert installed
    context = playwright.firefox.launch_persistent_context('/home/dave/.mozilla/firefox/raogzvo8.my-playwright-profile',
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

    # page.goto('https://www.youtube.com/watch?v=8zpqJtSf2fM')

    # timeout 0 means wait forever
    # todo - this may be a problem
    page.goto(url, timout=0)


    #dm added this - get rid of cookies popup
    # try:
    #    page.wait_for_selector('button[aria-label="Reject the use of cookies and other data for the purposes described"]')
    #    page.click('button[aria-label="Reject the use of cookies and other data for the purposes described"]')
    # except Exception as e:
    #    print("no cookies popup which may be fine")


    # want the video to play!
    # page.wait_for_timeout(120000)

    # Reload the page
    # page.reload()

    page.wait_for_timeout(3000)

    print("screenshot 1")
    page.screenshot(path=tmp_dir + '/1.png', full_page=True)
    # read more click button
    page.wait_for_selector('tp-yt-paper-button#expand')
    page.click('tp-yt-paper-button#expand')

    page.wait_for_timeout(3000)
    print("screenshot 2")
    # Take a full page screenshot with the ad hopefully
    page.screenshot(path=tmp_dir + '/2.png', full_page=True)

    page.wait_for_timeout(1000)

    print("screenshot 3")
    page.screenshot(path=tmp_dir + '/3.png', full_page=True)

    page.wait_for_timeout(1000)
    print("screenshot 4")
    page.screenshot(path=tmp_dir + '/4.png', full_page=True)

    # I have seen it not render the comments as still loading
    print("wait for coments to load")
    page.wait_for_timeout(9000)


    # Sort by newest first
    # Click the dropdown to open the sort menu
    page.click('#trigger')

    # Wait for the dropdown to be visible
    page.wait_for_selector('tp-yt-paper-item .item:has-text("Newest first")')

    # Click the "Newest first" option
    page.click('tp-yt-paper-item .item:has-text("Newest first")')

    page.wait_for_timeout(4000)
    print("screenshot 5")
    page.screenshot(path=tmp_dir + '/5.png', full_page=True)


    page.wait_for_timeout(4000)
    print("screenshot 6")
    page.screenshot(path=tmp_dir + '/6.png', full_page=True)

    # page.wait_for_timeout(25000)  # Wait for 5 seconds
    exit()

def main():
    with sync_playwright() as playwright:
        run(playwright)

# Run the script
if __name__ == "__main__":
    main()
