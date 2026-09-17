import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import db
from services.profiles import SqliteProfileService


class MigrationTests(unittest.TestCase):
    def test_old_profile_table_accepts_every_editable_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old-profile.db'
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE users (userID INTEGER PRIMARY KEY, name TEXT, email TEXT, password TEXT);
                    INSERT INTO users VALUES (1, 'Legacy', 'legacy@example.com', 'password');
                    CREATE TABLE profile (userID INTEGER PRIMARY KEY, first_name TEXT);
                    INSERT INTO profile VALUES (1, 'Legacy');
                """)
            with patch.object(db, 'DB_PATH', path):
                service = SqliteProfileService()
                old = service.get_profile(1)
                self.assertEqual(old.first_name, 'Legacy')
                service.save_profile(1, replace(
                    old, last_name='Example', full_name='Legacy Example',
                    phone='555-0107', city='Edmonton', degree_program='Biology',
                    desired_titles=['Analyst'], skills=['Python'],
                ))
                saved = service.get_profile(1)
                self.assertEqual(saved.last_name, 'Example')
                self.assertEqual(saved.phone, '555-0107')
                self.assertEqual(saved.city, 'Edmonton')
                self.assertEqual(saved.degree_program, 'Biology')
                self.assertEqual(saved.desired_titles, ['Analyst'])
                self.assertEqual(saved.skills, ['Python'])

    def test_legacy_rows_preserved_and_new_accounts_can_share_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.db'
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE applications (id INTEGER PRIMARY KEY, url TEXT UNIQUE);
                    INSERT INTO applications VALUES (7, 'https://example.com/job');
                    CREATE TABLE profile (id INTEGER PRIMARY KEY, first_name TEXT);
                    INSERT INTO profile VALUES (1, 'Demo');
                """)
            conn.close()
            with patch.object(db, 'DB_PATH', path):
                db.init_db()
                db.init_db()
                with db.connect() as conn:
                    self.assertEqual(conn.execute('SELECT id FROM legacy_applications').fetchone()[0], 7)
                    self.assertEqual(conn.execute('SELECT first_name FROM legacy_profile').fetchone()[0], 'Demo')
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 0)
                    for user_id in (1, 2):
                        conn.execute('INSERT INTO users (userID,name,email,password) VALUES (?,?,?,?)',
                                     (user_id, 'Demo', f'{user_id}@example.com', 'demo'))
                        conn.execute('INSERT INTO applications (userID,company,title,url) VALUES (?,?,?,?)',
                                     (user_id, 'Demo', 'Developer', 'https://example.com/job'))
