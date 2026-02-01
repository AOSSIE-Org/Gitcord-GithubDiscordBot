import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '../gitcord.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # User linking table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            discord_id INTEGER PRIMARY KEY,
            github_username TEXT UNIQUE,
            score INTEGER DEFAULT 0,
            last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Repositories linked to channels
    c.execute('''
        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_url TEXT,
            owner TEXT,
            name TEXT,
            channel_id INTEGER,
            last_event_etag TEXT,
            UNIQUE(repo_url, channel_id)
        )
    ''')
    
    # Maintainers for specific repos
    c.execute('''
        CREATE TABLE IF NOT EXISTS maintainers (
            discord_id INTEGER,
            repo_url TEXT,
            FOREIGN KEY(discord_id) REFERENCES users(discord_id),
            UNIQUE(discord_id, repo_url)
        )
    ''') 

    # Deduplication table for events (lightweight)
    c.execute('''
        CREATE TABLE IF NOT EXISTS processed_events (
            event_id TEXT PRIMARY KEY
        )
    ''')
    
    # Activity log to prevent double counting (points specific)
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id TEXT PRIMARY KEY,
            activity_type TEXT,
            discord_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(discord_id) REFERENCES users(discord_id)
        )
    ''')
    
    conn.commit()
    conn.close()

def add_user(discord_id, github_username):
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (discord_id, github_username) VALUES (?, ?)', (discord_id, github_username))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_user_by_discord(discord_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE discord_id = ?', (discord_id,))
    user = c.fetchone()
    conn.close()
    return user

def get_all_users():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM users')
    users = c.fetchall()
    conn.close()
    return users

def add_repo(repo_url, channel_id):
    # Parse owner/name from URL (simple assumption)
    # URL format: https://github.com/owner/name
    parts = repo_url.rstrip('/').split('/')
    if len(parts) < 2:
        return False
    name = parts[-1]
    owner = parts[-2]
    
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO repos (repo_url, owner, name, channel_id) VALUES (?, ?, ?, ?)', 
                  (repo_url, owner, name, channel_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_repo(repo_url, channel_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('DELETE FROM repos WHERE repo_url = ? AND channel_id = ?', (repo_url, channel_id))
    rows = c.rowcount
    conn.commit()
    conn.close()
    return rows > 0

def get_repos():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM repos')
    repos = c.fetchall()
    conn.close()
    return repos

def add_maintainer(discord_id, repo_url):
    conn = get_connection()
    c = conn.cursor()
    try:
        # Check if user exists first? Assuming yes or FK handles it if enforced (sqlite default off)
        # We should ensure user is in users table? Or just trust ID?
        # Ideally user should be linked first, but maintainer might not be linked?
        # Requirement: "make that user maintainer role for that project"
        c.execute('INSERT INTO maintainers (discord_id, repo_url) VALUES (?, ?)', (discord_id, repo_url))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_maintainer(discord_id, repo_url):
    conn = get_connection()
    c = conn.cursor()
    c.execute('DELETE FROM maintainers WHERE discord_id = ? AND repo_url = ?', (discord_id, repo_url))
    rows = c.rowcount
    conn.commit()
    conn.close()
    return rows > 0

def get_maintainers_for_repo(repo_url):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT discord_id FROM maintainers WHERE repo_url = ?', (repo_url,))
    maintainers = [row['discord_id'] for row in c.fetchall()]
    conn.close()
    return maintainers

def get_discord_from_github(github_username):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT discord_id, score FROM users WHERE github_username = ? COLLATE NOCASE', (github_username,))
    row = c.fetchone()
    conn.close()
    return row

def update_repo_etag(repo_id, etag):
    conn = get_connection()
    c = conn.cursor()
    c.execute('UPDATE repos SET last_event_etag = ? WHERE id = ?', (etag, repo_id))
    conn.commit()
    conn.close()

def mark_event_processed(event_id):
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO processed_events (event_id) VALUES (?)', (event_id,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def is_event_processed(event_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT 1 FROM processed_events WHERE event_id = ?', (event_id,))
    exists = c.fetchone()
    conn.close()
    return exists is not None

def update_score(discord_id, points):
    conn = get_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET score = score + ? WHERE discord_id = ?', (points, discord_id))
    conn.commit()
    conn.close()

def log_activity(activity_id, activity_type, discord_id):
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO activity_log (id, activity_type, discord_id) VALUES (?, ?, ?)', 
                  (activity_id, activity_type, discord_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Activity already logged
    finally:
        conn.close()
