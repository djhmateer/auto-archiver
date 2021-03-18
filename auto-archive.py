import gspread
import youtube_dl
from pathlib import Path
import sys
import datetime
import boto3
import os
from dotenv import load_dotenv
from botocore.errorfactory import ClientError
import argparse
import math
import ffmpeg

load_dotenv()

def col_to_index(col):
    col = list(col)
    ndigits = len(col)
    alphabet = ' ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    v = 0
    i = ndigits - 1

    for digit in col:
        index = alphabet.find(digit)
        v += (26 ** i) * index
        i -= 1

    return v - 1

def index_to_col(index):
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

    if index > 25:
        t = index
        dig = 0
        while t > 25:
            t = math.floor(t / 26)
            dig += 1
        return alphabet[t - 1] + index_to_col(index - t * int(math.pow(26, dig)))
    else:
        return alphabet[index]

def get_thumbnails(filename, s3_client):
    if not os.path.exists(filename.split('.')[0]):
        os.mkdir(filename.split('.')[0])

    stream = ffmpeg.input(filename)
    stream = ffmpeg.filter(stream, 'fps', fps=0.5).filter('scale', 512, -1)
    stream.output(filename.split('.')[0] + '/out%d.jpg').run()

    thumbnails = os.listdir(filename.split('.')[0] + '/')
    cdn_urls = []

    for fname in thumbnails:
        thumbnail_filename = filename.split('.')[0] + '/' + fname
        key = filename.split('/')[1].split('.')[0] + '/' + fname

        cdn_url = 'https://{}.{}.cdn.digitaloceanspaces.com/{}'.format(
            os.getenv('DO_BUCKET'), os.getenv('DO_SPACES_REGION'), key)

        with open(thumbnail_filename, 'rb') as f:
            s3_client.upload_fileobj(f, Bucket=os.getenv(
                'DO_BUCKET'), Key=key, ExtraArgs={'ACL': 'public-read'})

        cdn_urls.append(cdn_url)
        os.remove(thumbnail_filename)

    key_thumb = cdn_urls[int(len(cdn_urls)*0.25)]

    index_page = f'''<html><head><title>{filename}</title></head>
        <body>'''

    for t in cdn_urls:
        index_page += f'<img src="{t}" />'

    index_page += f"</body></html>"
    index_fname = filename.split('.')[0] + '/index.html'

    with open(index_fname, 'w') as f:
        f.write(index_page)

    thumb_index = filename.split('/')[1].split('.')[0] + '/index.html'

    s3_client.upload_fileobj(open(index_fname, 'rb'), Bucket=os.getenv(
        'DO_BUCKET'), Key=thumb_index, ExtraArgs={'ACL': 'public-read', 'ContentType': 'text/html'})

    thumb_index_cdn_url = 'https://{}.{}.cdn.digitaloceanspaces.com/{}'.format(
        os.getenv('DO_BUCKET'), os.getenv('DO_SPACES_REGION'), thumb_index)

    return (key_thumb, thumb_index_cdn_url)


def download_vid(url, s3_client, check_if_exists=False):
    ydl_opts = {'outtmpl': 'tmp/%(id)s.%(ext)s', 'quiet': False}
    ydl = youtube_dl.YoutubeDL(ydl_opts)
    cdn_url = None
    status = 'success'

    if check_if_exists:
        info = ydl.extract_info(url, download=False)

        if 'entries' in info:
            if len(info['entries']) > 1:
                raise Exception(
                    'ERROR: Cannot archive channels or pages with multiple videos')

            filename = ydl.prepare_filename(info['entries'][0])
        else:
            filename = ydl.prepare_filename(info)

        key = filename.split('/')[1]

        try:
            s3_client.head_object(Bucket=os.getenv('DO_BUCKET'), Key=key)

            # file exists
            cdn_url = 'https://{}.{}.cdn.digitaloceanspaces.com/{}'.format(
                os.getenv('DO_BUCKET'), os.getenv('DO_SPACES_REGION'), key)

            status = 'already archived'

        except ClientError:
            pass

    # sometimes this results in a different filename, so do this again
    info = ydl.extract_info(url, download=True)

    if 'entries' in info:
        if len(info['entries']) > 1:
            raise Exception(
                'ERROR: Cannot archive channels or pages with multiple videos')

        filename = ydl.prepare_filename(info['entries'][0])
    else:
        filename = ydl.prepare_filename(info)

    if not os.path.exists(filename):
        filename = filename.split('.')[0] + '.mkv'

    if status != 'already archived':
        key = filename.split('/')[1]
        cdn_url = 'https://{}.{}.cdn.digitaloceanspaces.com/{}'.format(
            os.getenv('DO_BUCKET'), os.getenv('DO_SPACES_REGION'), key)

        with open(filename, 'rb') as f:
            s3_client.upload_fileobj(f, Bucket=os.getenv(
                'DO_BUCKET'), Key=key, ExtraArgs={'ACL': 'public-read'})

    key_thumb, thumb_index = get_thumbnails(filename, s3_client)
    os.remove(filename)

    video_data = {
        'cdn_url': cdn_url,
        'thumbnail': key_thumb,
        'thumbnail_index': thumb_index,
        'duration': info['duration'] if 'duration' in info else None,
        'title': info['title'] if 'title' in info else None,
        'timestamp': info['timestamp'] if 'timestamp' in info  else datetime.datetime.strptime(info['upload_date'], '%Y%m%d').timestamp() if 'upload_date' in info else None,
    }

    return (video_data, status)


def update_sheet(wks, row, status, video_data, columns, v):
    update = []

    if columns['status'] is not None:
        update += [{
            'range': columns['status'] + str(row),
            'values': [[status]]
        }]

    if 'cdn_url' in video_data and video_data['cdn_url'] is not None and columns['archive'] is not None and v[col_to_index(columns['archive'])] == '':
        update += [{
            'range': columns['archive'] + str(row),
            'values': [[video_data['cdn_url']]]
        }]

    if 'date' in video_data and columns['date'] is not None and v[col_to_index(columns['date'])] == '':
        update += [{
            'range': columns['date'] + str(row),
            'values': [[datetime.datetime.now().isoformat()]]
        }]

    if 'thumbnail' in video_data and columns['thumbnail'] is not None and v[col_to_index(columns['thumbnail'])] == '':
        update += [{
            'range': columns['thumbnail'] + str(row),
            'values': [['=IMAGE("' + video_data['thumbnail'] + '")']]
        }]

    if 'thumbnail_index' in video_data and columns['thumbnail_index'] is not None and v[col_to_index(columns['thumbnail_index'])] == '':
        update += [{
            'range': columns['thumbnail_index'] + str(row),
            'values': [[video_data['thumbnail_index']]]
        }]

    if 'timestamp' in video_data and columns['timestamp'] is not None and video_data['timestamp'] is not None and v[col_to_index(columns['timestamp'])] == '':
        update += [{
            'range': columns['timestamp'] + str(row),
            'values': [[datetime.datetime.fromtimestamp(video_data['timestamp']).isoformat()]]
        }]

    if 'title' in video_data and columns['title'] is not None and video_data['title'] is not None and v[col_to_index(columns['title'])] == '':
        update += [{
            'range': columns['title'] + str(row),
            'values': [[video_data['title']]]
        }]

    if 'duration' in video_data and columns['duration'] is not None and video_data['duration'] is not None and v[col_to_index(columns['duration'])] == '':
        update += [{
            'range': columns['duration'] + str(row),
            'values': [[str(video_data['duration'])]]
        }]

    wks.batch_update(update, value_input_option='USER_ENTERED')


def main():
    parser = argparse.ArgumentParser(
        description="Automatically use youtube-dl to download media from a Google Sheet")
    parser.add_argument("--sheet", action="store", dest="sheet")
    parser.add_argument('--streaming', dest='streaming', action='store_true')

    args = parser.parse_args()

    print("Opening document " + args.sheet)

    gc = gspread.service_account()
    sh = gc.open(args.sheet)
    n_worksheets = len(sh.worksheets())

    s3_client = boto3.client('s3',
                             region_name=os.getenv('DO_SPACES_REGION'),
                             endpoint_url='https://{}.digitaloceanspaces.com'.format(
                                 os.getenv('DO_SPACES_REGION')),
                             aws_access_key_id=os.getenv('DO_SPACES_KEY'),
                             aws_secret_access_key=os.getenv('DO_SPACES_SECRET'))

    # loop through worksheets to check
    for ii in range(n_worksheets):
        print("Opening worksheet " + str(ii))
        wks = sh.get_worksheet(ii)
        values = wks.get_all_values()

        headers = values[0]
        columns = {}

        columns['url'] = index_to_col(headers.index(
            'Media URL')) if 'Media URL' in headers else None
        url_index = col_to_index(columns['url'])

        columns['archive'] = index_to_col(headers.index(
            'Archive location')) if 'Archive location' in headers else None
        columns['date'] = index_to_col(headers.index(
            'Archive date')) if 'Archive date' in headers else None
        columns['status'] = index_to_col(headers.index(
            'Archive status')) if 'Archive status' in headers else None
        columns['thumbnail'] = index_to_col(headers.index(
            'Thumbnail')) if 'Thumbnail' in headers else None
        columns['thumbnail_index'] = index_to_col(headers.index(
            'Thumbnail index')) if 'Thumbnail index' in headers else None
        columns['timestamp'] = index_to_col(headers.index(
            'Upload timestamp')) if 'Upload timestamp' in headers else None
        columns['title'] = index_to_col(headers.index(
            'Upload title')) if 'Upload title' in headers else None
        columns['duration'] = index_to_col(headers.index(
            'Duration')) if 'Duration' in headers else None

        if columns['url'] is None:
            print("No 'Media URL' column found, skipping")
            continue

        # loop through rows in worksheet
        for i in range(2, len(values)+1):
            v = values[i-1]

            if v[url_index] != "" and v[col_to_index(columns['status'])] == "":
                try:
                    ydl_opts = {
                        'outtmpl': 'tmp/%(id)s.%(ext)s', 'quiet': False}
                    ydl = youtube_dl.YoutubeDL(ydl_opts)
                    info = ydl.extract_info(v[url_index], download=False)

                    if args.streaming and 'is_live' in info and info['is_live']:
                        wks.update(columns['status'] + str(i), 'Recording stream')
                        video_data, status = download_vid(v[url_index], s3_client)
                        update_sheet(wks, i, status, video_data, columns, v)
                        sys.exit()
                    elif not args.streaming and ('is_live' not in info or not info['is_live']):
                        video_data, status = download_vid(
                            v[url_index], s3_client, check_if_exists=True)
                    update_sheet(wks, i, status, video_data, columns, v)

                except:
                    # if any unexpected errors occured, log these into the Google Sheet
                    t, value, traceback = sys.exc_info()
                    update_sheet(wks, i, str(value), {}, columns, v)


if __name__ == "__main__":
    main()
