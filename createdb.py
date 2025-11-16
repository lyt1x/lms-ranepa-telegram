import sqlite3

conn = sqlite3.connect("sessions.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE sessions (
    user_id INTEGER PRIMARY KEY,
    sesskey TEXT,
    cookie_jar TEXT
);
""")
conn.commit()
conn.close()