#!/usr/bin/env bash
set -e

mkdir -p data/backups logs config

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

python init_db.py
python scripts/harden_env.py

echo "Deployment setup complete. Add a cron job (for example: */1 * * * * cd /path/to/project && ./venv/bin/python src/bot_runner.py)"
