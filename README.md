# REFIND — Lost and Found Matching System

REFIND stores lost and found item reports in a local SQLite database, compares reports using priority-based matching, ranks possible matches, and creates separate in-app notifications for the person who lost the item and the person who reported finding it.

## Features
- Register and log in; passwords are stored as secure hashes.
- Separate forms for **I Lost an Item** and **I Found an Item**.
- SQLite storage for users, item reports, match pairs, and notifications.
- Optional photo uploads (PNG/JPG/JPEG/WEBP, max 5 MB).
- Priority-based score using category, title, description, location, colour, and date.
- When both reports have photos, a lightweight average-colour image comparison contributes to the score. This is **not AI object recognition**; visually similar objects with different colours may score differently.
- Ranked match list and individual notifications for both users.
- Mark your own report as resolved.

## Run on Windows (VS Code)
1. Install Python 3.11 or newer from https://www.python.org/downloads/ and select **Add Python to PATH** during installation.
2. Open the `REFIND` folder in VS Code.
3. Open **Terminal → New Terminal**.
4. Create a virtual environment:
   ```powershell
   python -m venv .venv
   ```
5. Activate it:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again.
6. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
7. Start the app:
   ```powershell
   python app.py
   ```
8. Open http://127.0.0.1:5000 in your browser.
9. Register two accounts to test the two-user notification flow. Add a lost report with one account and a found report with the other, using similar details and (optionally) photos.

## Matching priorities
Default text/detail weights:
- Category: 25%
- Item title: 20%
- Description: 20%
- Location: 15%
- Colour: 10%
- Date proximity: 10%

When both photos are present, the details score is scaled to 85% and a simple image-colour similarity contributes 15%. Without two photos, the details score is used by itself. A score of 25% or more is stored as a possible match. Scores of 99.9% or higher trigger “perfect match” wording. Matching scores are suggestions, not proof that two reports refer to the same object.

## Important notes
- This is a beginner-friendly local academic project, not production-ready authentication.
- The Flask development server is for local testing only. Do not expose it publicly as-is.
- Before deployment, set a strong `REFIND_SECRET_KEY` environment variable and add CSRF protection, rate limits, email/SMS delivery, and stronger image analysis.
- Notifications currently appear inside the REFIND website. They are not sent as real email/SMS messages.
- The SQLite database (`refind.db`) is created automatically on first run. Keep it private because it contains report and account data.
