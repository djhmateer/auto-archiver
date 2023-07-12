import pyodbc 
import time
from datetime import datetime
import cred_mssql
import cred_twitter
import pyodbc 
import tweepy
from loguru import logger

logger.add("logs/t1trace.log", level="TRACE", rotation="00:00")
logger.add("logs/t2info.log", level="INFO", rotation="00:00")
logger.add("logs/t3success.log", level="SUCCESS", rotation="00:00")
logger.add("logs/t4warning.log", level="WARNING", rotation="00:00")
logger.add("logs/t5error.log", level="ERROR", rotation="00:00")

logger.debug(f'tweet.py')

client = tweepy.Client(
    consumer_key=cred_twitter.consumer_key,
    consumer_secret=cred_twitter.consumer_secret,
    access_token=cred_twitter.access_token,
    access_token_secret=cred_twitter.access_token_secret
)

retry_flag = True
retry_count = 0
while retry_flag and retry_count < 5:
    try:
        logger.debug(f'Trying to connect to db')
        cnxn = pyodbc.connect('DRIVER={ODBC Driver 18 for SQL Server};SERVER='+cred_mssql.server+';DATABASE='+cred_mssql.database+';ENCRYPT=yes;UID='+cred_mssql.username+';PWD='+ cred_mssql.password)
        cursor = cnxn.cursor()

        # fetchall() so don't get error with using 2 cursors
        # can reuse the cursor as have all in memory
        cursor.execute("SELECT top 2 * from hash WHERE HasBeenTweeted = 'False'") 
        rows = cursor.fetchall()
        num_rows = len(rows)
        if (num_rows== 2):
            logger.debug("found 2 hashes which need tweeted")
        elif (num_rows == 1):
            logger.debug(f"only found {num_rows}, so wait as want to tweet 2 together")
            break # out of while
        else:
            logger.debug(f"no new hashes found")
            break # out of while

        logger.debug(f'Selecting 2 hashes which need to be tweeted')
        tweet_text = ""
        hashId1 = ""
        hashId2 = ""
        for row in rows:
            if (tweet_text == ""):
                tweet_text = row.HashText
                hashId1 = row.HashId
            else:
                tweet_text = tweet_text + " " + row.HashText
                hashId2 = row.HashId

        # tweet here
        message = f"trying to tweet {tweet_text}"
        logger.debug(message)
        tweet_success = False
        tweet_exception = ""
        try:    
            client.create_tweet(text=tweet_text)
            logger.success(f'Tweet success')
            tweet_success = True
        except Exception as e:
            # eg 403 Forbidden You are not allowed to create a Tweet with duplicate content
            # 400 Bad Request Your Tweet text is too long. For more information on how Twitter determines text length see https://github.com/twitter/twitter-text.
            # 429 Too Many Requests Too Many Requests (after 50 tweets - 1226 on Sat)
            logger.error(f'Error tweeting: {e}')
            tweet_exception = e

        # if successful, update the rows
        if (tweet_success):
            cursor.execute("UPDATE hash set HasBeenTweeted = 'True' WHERE HashId = ?", hashId1)
            cursor.execute("UPDATE hash set HasBeenTweeted = 'True' WHERE HashId = ?", hashId2)
            cursor.commit()
        else:
            cursor.execute("UPDATE hash set HasBeenTweeted = 'True', ErrorText = ? WHERE HashId = ?", str(tweet_exception), hashId1)
            cursor.execute("UPDATE hash set HasBeenTweeted = 'True', ErrorText = ? WHERE HashId = ?", str(tweet_exception), hashId2)
            cursor.commit()

        retry_flag = False
    except Exception as e:
        logger.error(f"DB Retry after 30 secs as {e}")
        retry_count = retry_count + 1
        time.sleep(30)

logger.debug("finished tweet.py")

