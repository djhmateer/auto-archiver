from playwright.sync_api import sync_playwright
import sys

def run(playwright):
    url = sys.argv[1]
    print("url: ", url)

    tmp_dir = sys.argv[2]
    print("tmp_dir: ", tmp_dir)

    # Launch the browser
    # browser = playwright.chromium.launch(headless=False)
    browser = playwright.firefox.launch(headless=False)


    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        viewport={"width": 1024, "height": 3000}  # Set the viewport to a longer screen size
    )

   # Disable automation flags that might disable ads
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    """)

    page = context.new_page()

    # page.goto('https://www.youtube.com/watch?v=JikrA8KqeEA')
    page.goto(url)

    # page.wait_for_timeout(10000)

    # page.screenshot(path='1x.png', full_page=True)

    # on server in US I didn't need this
    #dm added this - get rid of cookies popup
    try:
       page.wait_for_selector('button[aria-label="Reject the use of cookies and other data for the purposes described"]')
       page.click('button[aria-label="Reject the use of cookies and other data for the purposes described"]')
    except Exception as e:
       print("no cookies popup which may be fine")


    page.wait_for_timeout(10000)

    # Reload the page
    # have seen this fail after 30 seconds
    try:
        page.reload()
    except Exception as e:
        sys.stderr.write(f"Reload failed after 30 seconds\n {e}")
        sys.stderr.write(f"Trying again")

        page.reload()

    page.wait_for_timeout(1000)

    page.screenshot(path=tmp_dir + '/1.png', full_page=True)
    # read more click button
    page.wait_for_selector('tp-yt-paper-button#expand')
    page.click('tp-yt-paper-button#expand')


    # Take a full page screenshot with the ad hopefully
    page.screenshot(path=tmp_dir + '/2.png', full_page=True)

    page.wait_for_timeout(1000)

    page.screenshot(path=tmp_dir + '/3.png', full_page=True)

    page.wait_for_timeout(1000)
    page.screenshot(path=tmp_dir + '/4.png', full_page=True)

    # page.wait_for_timeout(60000)  # Wait for x seconds

    # Close the browser
    browser.close()

def main():
    with sync_playwright() as playwright:
        run(playwright)

# Run the script
# if __name__ == "__main__":
main()
