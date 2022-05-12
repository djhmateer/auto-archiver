from fileinput import filename
from selenium import webdriver
import time
from selenium.webdriver.common.by import By
import hashlib


# filename='sDE-qZdi8p8.webm'
filename='sDE-qZdi8p8-XXX.webm'
f = open(filename, "rb")
bytes = f.read()  # read entire file as bytes

hash = hashlib.sha256(bytes)
# hash = hashlib.sha3_512(bytes)

f.close()
digest = hash.hexdigest()
print(f'is {digest=}')



