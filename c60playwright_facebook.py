from playwright.sync_api import sync_playwright
import sys
import os

def run(playwright):
    # for testing run this python file directly and comment out and in

    url = sys.argv[1]
    tmp_dir = sys.argv[2]

    # 3 documnets
    # url = "https://www.facebook.com/khitthitnews/posts/pfbid0PTvT6iAccWqatvbDQNuqpFwL5WKzHuLK4QjP97Fwut637CV3XXQU53z1s2bJMAKwl"

    # single lady
    # url = "https://www.facebook.com/photo/?fbid=1329142910787472&set=a.132433247125117"

    # 1 violent
    # url = "https://www.facebook.com/khitthitnews/posts/pfbid02tX6o4TcNykMYyH4Wjbz3ckq5bH5rRr7aqLFCymkWwhVzPJGwq2mSCnp9jYZ8CVdTl"

    tmp_dir = "/mnt/c/dev/v6-auto-archiver/tmp"

    # needs have an up to date profile in there copied from dev
    # ./chrome from:
    # /home/dave/.cache/ms-playwright/chromium-1076/chrome-linux/
    data_dir = '/home/dave/.config/chromium'

    dev_executable_path = '/home/dave/.cache/ms-playwright/chromium-1076/chrome-linux/chrome'
    prod_executable_path = '/home/dave/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'
    if os.path.exists(dev_executable_path):
        print('1076 found')
        executable_path = dev_executable_path
    elif os.path.exists(prod_executable_path):
        print('1091 found')
        executable_path = prod_executable_path
    else:
        print('problem - no chromium found')
        exit()


    browser = playwright.chromium.launch_persistent_context(data_dir,
                                        headless=False,
                                        executable_path = executable_path,
                                        viewport={"width": 1224, "height": 3000}
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
