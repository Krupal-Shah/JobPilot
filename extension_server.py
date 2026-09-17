#!/usr/bin/env python3
"""Run the shared application on the extension's port, with the same auth/routes.

Run app.py on port 5050 and this launcher on port 8421. Both use the same
SQLite database and SECRET_KEY. Log in using http://127.0.0.1:5050.
"""
from app import app

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8421, debug=True)
