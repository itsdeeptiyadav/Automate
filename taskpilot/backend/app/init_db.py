"""Create the SQLite tables:  python -m app.init_db"""
from app.config import get_settings
from app.database import init_db, make_engine

if __name__ == "__main__":
    s = get_settings()
    init_db(make_engine(s.database_url))
    print(f"Database ready: {s.database_url}")
