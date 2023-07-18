import datetime
import time
import cred_mssql
import cred_twitter
import pyodbc 
import tweepy
from loguru import logger

# does 2 hashes at a time
# which are combined into 1 tweet

# if HashTweetTime is in the future, then it wont try to tweet
# as maxtweets per day has been reached (50)
# so lets not hit the twitter API again

# if anything written to ErrorText column, then manual intervention required
# set ErrorText to NULL or empty to try again

# only on successful tweet will DateTimeTweetedUtc be filled in


logger.add("logs/t1trace.log", level="TRACE", rotation="00:00")
logger.add("logs/t2info.log", level="INFO", rotation="00:00")
logger.add("logs/t3success.log", level="SUCCESS", rotation="00:00")
logger.add("logs/t4warning.log", level="WARNING", rotation="00:00")
logger.add("logs/t5error.log", level="ERROR", rotation="00:00")

client = tweepy.Client(
    consumer_key=cred_twitter.consumer_key,
    consumer_secret=cred_twitter.consumer_secret,
    access_token=cred_twitter.access_token,
    access_token_secret=cred_twitter.access_token_secret
)

retry_flag = True
retry_count = 0
should_tweet = True
while retry_flag and retry_count < 5 and should_tweet:
    try:
        logger.debug(f'Trying to connect to db')
        conn_string = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER='+cred_mssql.server+';DATABASE='+cred_mssql.database+';ENCRYPT=yes;UID='+cred_mssql.username+';PWD='+ cred_mssql.password
        # logger.debug(conn_string)
        cnxn = pyodbc.connect(conn_string)
        cursor = cnxn.cursor()

        # is HashTweetTime okay ie are we waiting for +24 hours to tweet again?
        cursor.execute("SELECT TimeToTryTweetUtc from HashTweetTime") 
        rows = cursor.fetchall()
        # will only every be 1 row
        for row in rows:
            next_tweet_utc_naive = row.TimeToTryTweetUtc
            # naive datetime to timezone aware
            next_tweet_utc = next_tweet_utc_naive.replace(tzinfo=datetime.timezone.utc)

            dt_now = datetime.datetime.now(datetime.timezone.utc)

            # is dt_now greater than next_tweet_utc?
            if (dt_now > next_tweet_utc):
                pass
            else:
                logger.debug(f'wait until {next_tweet_utc} UTC as have got 429 error which means we have maxed out daily tweets')
                should_tweet = False

        if (should_tweet):
            pass
        else:
            break # out of while

        # fetchall() so don't get error with using 2 cursors
        # can reuse the cursor as have all in memory

        # using ErrorText is NULL so that if we do get an error, then can retry by blanking that cell
        # and don't retry until it is set to blank (as it could be a duplicate tweet which may never work)
        cursor.execute("SELECT top 2 * from hash WHERE HasBeenTweeted = 'False' and (ErrorText is NULL or ErrorText = '')") 
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
        message = f"trying to tweet {hashId1=} and {hashId2=}: {tweet_text}"
        logger.debug(message)
        tweet_success = None
        tweet_exception = None
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
            tweet_success = False

        # if successful, update the rows
        if (tweet_success):
            dt_now_utc = datetime.datetime.now(datetime.timezone.utc)
            cursor.execute("UPDATE hash set HasBeenTweeted = 'True', ErrorText=NULL, DateTimeTweetedUtc = ? WHERE HashId = ?", dt_now_utc, hashId1)
            cursor.execute("UPDATE hash set HasBeenTweeted = 'True', ErrorText=NULL, DateTimeTweetedUtc = ? WHERE HashId = ?", dt_now_utc, hashId2)
            cursor.commit()
        else:
            exc_text = str(tweet_exception)
            # if 429 too many requests, back off and try tomorrow
            if "429 Too Many Requests" in exc_text:
                logger.info("back off until + 24 hours as tweet limit reached")
                dt_now = datetime.datetime.now(datetime.timezone.utc)
                end_date = dt_now + datetime.timedelta(days=1)
                cursor.execute("UPDATE HashTweetTime set TimeToTryTweetUtc = ?", end_date)
                cursor.commit()
                # don't update Hash as we want to retry tomorrow when the window is open
            else:
                # need humnan to see what problem is so set ErrorText 
                cursor.execute("UPDATE hash set ErrorText = ? WHERE HashId = ?", str(tweet_exception), hashId1)
                cursor.execute("UPDATE hash set ErrorText = ? WHERE HashId = ?", str(tweet_exception), hashId2)
                cursor.commit()

        retry_flag = False
    except Exception as e:
        logger.error(f"DB Retry after 30 secs as: {e}")
        retry_count = retry_count + 1
        time.sleep(30)

logger.debug("finished tweet.py")

