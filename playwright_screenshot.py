from playwright.sync_api import sync_playwright
import sys
import time
from pathlib import Path

# Use https://playwright.dev/python/docs/intro to take a screenshot
# called by
# xvfb-run python3 playwright_screenshot.py {url}
# from facebook_archiver.py

# default to using a proxy https://brightdata.com/integration/playwright as is more reliable
# assume 1 argument which is the url
# assume proxy-username.txt and proxy-password.txt are stored in secrets directory


url = sys.argv[1]
# print(f"{url=}")

def run(use_proxy):
    with sync_playwright() as p:
        if (use_proxy):
            username = Path('secrets/proxy-username.txt').read_text()
            password = Path('secrets/proxy-password.txt').read_text()
            browser = p.chromium.launch(    
                headless=False,
                proxy={
                    "server": 'http://zproxy.lum-superproxy.io:22225',
                    "username": username,
                    "password": password
                },
                args=['--start-maximized']
            )
        else:
           browser = p.chromium.launch(    
                headless=False,
                args=['--start-maximized']
            ) 

        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36"

        context = browser.new_context(
            user_agent=ua,
            # otherwise login and register buttons from facebook may overlay the page content
            viewport={"width":1200, "height":2000}
        )

        page = context.new_page()

        # maybe there will be a cookie popup
        # so click accept cookies on facebook.com to try to alleviate
        try:
            response = page.goto("http://www.facebook.com", wait_until='networkidle')
            time.sleep(5)
            print(f'response done')
            foo = page.locator("//button[@data-cookiebanner='accept_only_essential_button']")
            print(f'page locator done')
            foo.click()
            print(f'click done - fb click worked')
            # linux server needs a sleep otherwise facebook cookie won't have worked and we'll get a popup on next page
            time.sleep(5)
        except Exception as e:
            print(f'Failed on fb accept cookies {url=} with {e=}')


        # https://github.com/microsoft/playwright/issues/12182
        # sometimes a timeout
        def page_goto(url):
            return page.goto(url, timeout=80000, wait_until='networkidle')
        counter = 0
        Found = False
        while (counter < 10):
            try:
                response = page_goto(url)
                Found = True
                break # out of while
            except:
                print(f"timeout detected - sleeping for 60s - retrying counter {counter}")
                counter += 1
                time.sleep(60)

        if (Found == False):
            print(f"problem - 10 retries and can't get page")
            return False

        if response.request.redirected_from is None:
            print("all good - no redirect")
            page.screenshot(path=f"screenshot.png", full_page=True)
            browser.close()
            return True
        else: 
            print(f'normal control flow. redirect to login problem! This happens on /permalink and /user/photo direct call {response.request.redirected_from.url}')

            browser.close()
            return False


bar = run(True)

if bar == False:
    print(f'proxy failed so trying no proxy')
    bar2 = run(False)
    print(f'no proxy result {bar2=}')
else:
    print("proxy worked")
