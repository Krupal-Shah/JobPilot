-- Application tracker + master profile + user accounts. SQLite dialect.
--
-- Single `applications` table covers the whole pipeline (bookmarked ->
-- applied -> interview -> offer/rejected) via the `stage` column, rather
-- than splitting a separate kanban/bookmark table from a submitted-
-- applications table. Simpler for this prototype's scope, and
-- `date_applied` still records the moment a job moves past "bookmarked".
--
-- Every application and profile belongs to a user (userID foreign key),
-- so multiple people can use the app independently.

CREATE TABLE IF NOT EXISTS users (
    userID INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password TEXT,
    master_resume TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    userID INTEGER NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT,
    stage TEXT NOT NULL DEFAULT 'bookmarked'
        CHECK (stage IN ('bookmarked', 'applied', 'interview', 'offer', 'rejected')),
    resume_variant TEXT,
    resume_base TEXT,
    resume_state TEXT,
    tailoring_error TEXT,
    cover_letter TEXT,
    notes TEXT NOT NULL DEFAULT '',
    date_applied TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (userID) REFERENCES users(userID)
);

-- Two users can bookmark the same job posting, so url is no longer UNIQUE
-- globally; instead each user can only bookmark a given URL once.
CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_user_url ON applications(userID, url);
CREATE INDEX IF NOT EXISTS idx_applications_user_stage ON applications(userID, stage);

-- Per-user master profile that the automation service fills forms from.
-- Created with blank personal details on first read per user.
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    userID INTEGER NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    school TEXT NOT NULL,
    linkedin TEXT NOT NULL DEFAULT '',
    github TEXT NOT NULL DEFAULT '',
    address_line_1 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    province TEXT NOT NULL DEFAULT '',
    postal_code TEXT NOT NULL DEFAULT '',
    degree_program TEXT NOT NULL DEFAULT '',
    expected_graduation TEXT NOT NULL DEFAULT '',
    field_map TEXT NOT NULL DEFAULT '[]',
    screening_answers TEXT NOT NULL DEFAULT '[]',
    desired_titles TEXT NOT NULL DEFAULT '[]',
    skills TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (userID) REFERENCES users(userID)
);

CREATE INDEX IF NOT EXISTS idx_profile_user ON profile(userID);

-- Legacy edited masters remain readable during migration. New uploads and edits
-- are stored in users.master_resume; new users inherit assets/resumeTemplate.tex.
CREATE TABLE IF NOT EXISTS master_resumes (
    userID INTEGER PRIMARY KEY,
    latex TEXT NOT NULL,
    FOREIGN KEY (userID) REFERENCES users(userID) ON DELETE CASCADE
);
-- Discard obsolete, regenerable PDF caches. Compressed LaTeX remains in
-- applications.resume_variant; PDFs are now compiled only for delivery.
DROP TABLE IF EXISTS application_resumes;
DROP TABLE IF EXISTS master_resume_pdf;
