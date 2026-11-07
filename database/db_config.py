import os
import psycopg2
from psycopg2.extras import RealDictCursor

# Récupération de l'URL unique fournie par Render (DATABASE_URL)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """
    Établit une connexion à la base de données PostgreSQL sur Render.
    """
    if not DATABASE_URL:
        raise RuntimeError("La variable d'environnement DATABASE_URL n'est pas définie.")
    
    # Connexion à PostgreSQL avec RealDictCursor pour conserver 
    # le même comportement de dictionnaires qu'avec PyMySQL
    connection = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )
    return connection