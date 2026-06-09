# Zanzer startup script for Windows VPS
# Run this from the project root to start the bot, supervisor, expiry notifier, and dashboard.

cd C:\xampp\htdocs\zanzer
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m backend.service
