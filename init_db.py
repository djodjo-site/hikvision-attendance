import os
import psycopg2

# Utilisez votre External Database URL de Render
DATABASE_URL = "postgresql://hikvision_db_user:RTPciWsEwJ6VcSSfkftCeZBDFLpo2xTn@dpg-dbngaae7bikc73cfomeg-a.oregon-postgres.render.com/hikvision_db"

def init_database():
    print("Connexion à la base de données PostgreSQL sur Render...")
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    # Lecture du fichier dans le sous-dossier database/
    with open("database/schema.sql", "r", encoding="utf-8") as f:
        schema_sql = f.read()
        
    print("Exécution du script schema.sql...")
    cursor.execute(schema_sql)
    conn.commit()
    
    cursor.close()
    conn.close()
    print("Succès : Les tables ont été créées avec succès !")

if __name__ == "__main__":
    init_database()