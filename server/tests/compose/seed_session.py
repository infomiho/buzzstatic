"""Creates a user and a session in the Buzz database and prints the session token.

Runs inside the server container with the standard library only."""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

connection = sqlite3.connect("/data/data.db")
connection.execute(
    "INSERT OR IGNORE INTO users (github_id, github_login) VALUES (1, 'compose-test')"
)
user_id = connection.execute("SELECT id FROM users WHERE github_id = 1").fetchone()[0]
token = "buzz_sess_" + secrets.token_urlsafe(32)
expires_at = (datetime.now() + timedelta(hours=1)).isoformat()
connection.execute(
    "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
    (hashlib.sha256(token.encode()).hexdigest(), user_id, expires_at),
)
connection.commit()
print(token)
