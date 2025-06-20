#!/usr/bin/env python3
import click
import psycopg2
from werkzeug.security import generate_password_hash
import tomllib

@click.command()
@click.argument('email')
@click.argument('password')
@click.option('--config', default='config.toml', help='Config file path')
def upsert_admin(email, password, config):
    """Upsert admin user with email and password."""
    
    try:
        with open(config, 'rb') as f:
            config_data = tomllib.load(f)
    except FileNotFoundError:
        click.echo(f"Error: Config file {config} not found", err=True)
        return
    except tomllib.TOMLDecodeError as e:
        click.echo(f"Error parsing TOML: {e}", err=True)
        return
    
    db_config = config_data.get('database', {})
    conn_params = {
        'host': db_config.get('host', 'localhost'),
        'port': db_config.get('port', 5432),
        'database': db_config.get('name'),
        'user': db_config.get('user'),
        'password': db_config.get('password')
    }
    
    password_hash = generate_password_hash(password)
    
    try:
        with psycopg2.connect(**conn_params) as conn:
            with conn.cursor() as cur:
                # Check if user exists
                cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
                
                if user:
                    # Update existing user
                    cur.execute(
                        "UPDATE users SET password_hash = %s WHERE email = %s",
                        (password_hash, email)
                    )
                    click.echo(f"Updated password for {email}")
                else:
                    # Create new user
                    cur.execute(
                        "INSERT INTO users (email, password_hash, is_admin) VALUES (%s, %s, %s)",
                        (email, password_hash, True)
                    )
                    click.echo(f"Created admin user {email}")
                
                conn.commit()
                
    except psycopg2.Error as e:
        click.echo(f"Database error: {e}", err=True)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)

if __name__ == '__main__':
    upsert_admin()
