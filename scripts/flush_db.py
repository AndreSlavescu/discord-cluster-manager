#!/usr/bin/env python3

import psycopg2
from psycopg2 import Error
from dotenv import load_dotenv
import os

def flush_database():
    # Load environment variables
    load_dotenv()
    
    DATABASE_URL = os.getenv('DATABASE_URL')
    
    if DATABASE_URL is None:
        print(f"❌ Missing DATABASE_URL environment variable")
        return

    try:
        # Connect to database
        print("📡 Connecting to database...")
        connection = psycopg2.connect(DATABASE_URL, sslmode='require')
        cursor = connection.cursor()

        # Drop existing tables
        print("🗑️  Dropping existing tables...")
        drop_tables_query = """
        DROP TABLE IF EXISTS submissions CASCADE;
        DROP TABLE IF EXISTS leaderboard CASCADE;
        DROP TABLE IF EXISTS runinfo CASCADE;
        DROP TABLE IF EXISTS _yoyo_log CASCADE;
        DROP TABLE IF EXISTS _yoyo_migration CASCADE;
        DROP TABLE IF EXISTS _yoyo_version CASCADE;
        DROP TABLE IF EXISTS yoyo_lock CASCADE;
        DROP SCHEMA IF EXISTS leaderboard CASCADE;
        """
        cursor.execute(drop_tables_query)
        # Commit changes
        connection.commit()
        print("✅ Database flushed and recreated successfully!")

    except Error as e:
        print(f"❌ Database error: {e}")
    finally:
        if 'connection' in locals():
            cursor.close()
            connection.close()
            print("🔌 Database connection closed")

if __name__ == "__main__":
    flush_database() 
