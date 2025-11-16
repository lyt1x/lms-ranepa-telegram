import sqlite3

conn = sqlite3.connect("sessions.db")
cursor = conn.cursor()
cursor.execute("SELECT * FROM sessions")
print(cursor.fetchall())