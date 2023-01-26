import os
import time
import sys

# https://stackoverflow.com/questions/237079/how-do-i-get-file-creation-and-modification-date-times

path_to_file= f"{os.getcwd()}/logs/1trace.log"
stat = os.stat(path_to_file)
try:
	date_modified = stat.st_birthtime
except AttributeError:
	date_modified = stat.st_mtime


# eg 1674659559
# eg 1674716232
# 1674716362
# print(f"date is {date_modified}")

seconds_since_last_log_write = time.time() - date_modified
# print(f"seconds since last log write {seconds_since_last_log_write}")

if seconds_since_last_log_write > 3600:
	# print(f"time greater than 1 hour since last 1trace.log write")
	print("time greater")
	sys.exit(1)
else:
	print("time less")
	sys.exit(0)
