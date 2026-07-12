web: gunicorn app:app --workers 2 --timeout 120 --log-file -
release: flask db upgrade
