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
        cursor.execute("SELECT * from hash WHERE HasBeenTweeted = 'False'") 
        rows = cursor.fetchall()
        logger.debug(f'Selecting hashes which need to be tweeted')
        for row in rows:
            # tweet here
            # message = f"hash is: {row.HashText} and time {time.time()}"
            message = f"{row.HashText}"
            logger.debug(message)
            tweet_success = False
            try:    
                client.create_tweet(text=message)
                logger.success(f'Tweet success: {message}')
                tweet_success = True
            except Exception as e:
                # eg 403 Forbidden You are not allowed to create a Tweet with duplicate content
                # 400 Bad Request Your Tweet text is too long. For more information on how Twitter determines text length see https://github.com/twitter/twitter-text.
                # 429 Too Many Requests Too Many Requests (after 50 tweets - 1226 on Sat)
                logger.error(f'unexpected error creating tweet: \n\n{e}\n')

            # if successful, update the row
            if (tweet_success):
              cursor.execute("UPDATE hash set HasBeenTweeted = 'True' WHERE HashId = ?", row.HashId)
              cursor.commit()

        retry_flag = False
    except Exception as e:
        logger.error(f"DB Retry after 30 secs as {e}")
        retry_count = retry_count + 1
        time.sleep(30)

logger.debug("finished tweet.py")

