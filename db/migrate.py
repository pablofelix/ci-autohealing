#!/usr/bin/env python3
"""
Database migration script using Python/psycopg2
Alternative to migrate.sh when psql is not available
"""

import os
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")

# Colors
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

def print_color(text, color):
    print(f"{color}{text}{Colors.NC}")

def main():
    print("=" * 50)
    print_color("CI Auto-Healing Database Migration (Python)", Colors.BLUE)
    print("=" * 50)
    print()

    # Database configuration
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "5432"))
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("DB_NAME", "konflux_monitoring")
    base_db = "postgres"  # Connect to postgres db first

    print_color(f"[1/5] Connecting to PostgreSQL...", Colors.YELLOW)

    try:
        # Connect to postgres database
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=base_db
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        print_color("✓ Connected successfully", Colors.GREEN)
        print()

    except Exception as e:
        print_color(f"✗ Connection failed: {e}", Colors.RED)
        print()
        print("Please check:")
        print("  - PostgreSQL is running")
        print("  - DB_HOST, DB_PORT, DB_USER, DB_PASSWORD in .env are correct")
        sys.exit(1)

    # Check if database exists
    print_color(f"[2/5] Checking if database '{db_name}' exists...", Colors.YELLOW)

    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    db_exists = cur.fetchone() is not None

    if db_exists:
        print_color(f"⚠ Database '{db_name}' already exists.", Colors.YELLOW)
        response = input("Do you want to drop and recreate it? (y/N): ").strip().lower()

        if response == 'y':
            print_color(f"Dropping database '{db_name}'...", Colors.YELLOW)
            cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
            print_color("✓ Database dropped", Colors.GREEN)

            print_color(f"Creating database '{db_name}'...", Colors.YELLOW)
            cur.execute(f"CREATE DATABASE {db_name}")
            print_color("✓ Database created", Colors.GREEN)
        else:
            print_color("Skipping database creation, will update schema only", Colors.YELLOW)
    else:
        print_color(f"Creating database '{db_name}'...", Colors.YELLOW)
        cur.execute(f"CREATE DATABASE {db_name}")
        print_color("✓ Database created", Colors.GREEN)

    print()

    # Close connection to postgres, connect to new database
    cur.close()
    conn.close()

    print_color(f"[3/5] Connecting to '{db_name}' database...", Colors.YELLOW)

    conn = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=db_name
    )
    cur = conn.cursor()
    print_color("✓ Connected", Colors.GREEN)
    print()

    # Apply schema
    print_color("[4/5] Applying schema...", Colors.YELLOW)

    schema_file = Path(__file__).parent / "schema.sql"

    if not schema_file.exists():
        print_color(f"✗ Schema file not found: {schema_file}", Colors.RED)
        sys.exit(1)

    try:
        with open(schema_file, 'r') as f:
            schema_sql = f.read()

        cur.execute(schema_sql)
        conn.commit()

        print_color("✓ Schema applied successfully", Colors.GREEN)
    except Exception as e:
        print_color(f"✗ Failed to apply schema: {e}", Colors.RED)
        conn.rollback()
        sys.exit(1)

    print()

    # Verify tables
    print_color("[5/5] Verifying tables...", Colors.YELLOW)

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)

    tables = cur.fetchall()
    print_color(f"✓ Created {len(tables)} tables", Colors.GREEN)
    print()

    print("Tables:")
    for table in tables:
        print(f"  - {table[0]}")

    print()

    # Show views
    cur.execute("""
        SELECT table_name
        FROM information_schema.views
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)

    views = cur.fetchall()
    if views:
        print("Views:")
        for view in views:
            print(f"  - {view[0]}")
        print()

    # Close
    cur.close()
    conn.close()

    print("=" * 50)
    print_color("Migration completed successfully!", Colors.GREEN)
    print("=" * 50)
    print()
    print("Connection details:")
    print(f"  Host: {db_host}")
    print(f"  Port: {db_port}")
    print(f"  Database: {db_name}")
    print(f"  User: {db_user}")
    print()
    print("Next steps:")
    print("  1. Test scanner: python3 collectors/scanner.py --mode trigger")
    print("  2. Use in Claude Code: /ci-build")
    print()

if __name__ == '__main__':
    main()
