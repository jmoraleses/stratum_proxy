import sqlite3

class DatabaseHandler:
    def __init__(self, db_file):
        self.connection = sqlite3.connect(db_file)
        self.cursor = self.connection.cursor()
        self.create_table()

    def create_table(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS merkle_roots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merkle_root TEXT UNIQUE
        )''')
        self.connection.commit()

    def insert_merkle_root(self, merkle_root):
        try:
            self.cursor.execute('INSERT INTO merkle_roots (merkle_root) VALUES (?)', (merkle_root,))
            self.connection.commit()
        except sqlite3.IntegrityError:
            pass  # Ignore if the merkle_root already exists

    def merkle_root_exists(self, merkle_root):
        self.cursor.execute('SELECT 1 FROM merkle_roots WHERE merkle_root = ?', (merkle_root,))
        return self.cursor.fetchone() is not None

    def close(self):
        self.connection.close()
