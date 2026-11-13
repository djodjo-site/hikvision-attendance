import sys
import os

# Ajustez le chemin si nécessaire pour importer get_db_connection
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from database.db_config import get_db_connection

def run_migration():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 1. Création de la table des entreprises
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(150) NOT NULL,
                    slug VARCHAR(100) UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Insérer une entreprise par défaut (pour vos données actuelles)
            cursor.execute("""
                INSERT INTO companies (id, name, slug) 
                VALUES (1, 'Entreprise Principale (Démo)', 'demo') 
                ON CONFLICT (id) DO NOTHING
            """)

            # 2. Table des horaires de travail + company_id
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_schedules (
                    id SERIAL PRIMARY KEY,
                    company_id INT REFERENCES companies(id) ON DELETE CASCADE,
                    name VARCHAR(100) NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    late_tolerance_minutes INT DEFAULT 30
                )
            """)
            
            # 3. Ajout de company_id dans employees si absent
            cursor.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name='employees' and column_name='company_id'
            """)
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE employees ADD COLUMN company_id INT REFERENCES companies(id) ON DELETE CASCADE")
                cursor.execute("UPDATE employees SET company_id = 1 WHERE company_id IS NULL")

            # 4. Ajout de company_id dans devices_mapping si absent
            cursor.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name='devices_mapping' and column_name='company_id'
            """)
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE devices_mapping ADD COLUMN company_id INT REFERENCES companies(id) ON DELETE CASCADE")
                cursor.execute("UPDATE devices_mapping SET company_id = 1 WHERE company_id IS NULL")

            # 5. Ajout de company_id dans attendance_raw_logs si absent
            cursor.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name='attendance_raw_logs' and column_name='company_id'
            """)
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE attendance_raw_logs ADD COLUMN company_id INT REFERENCES companies(id) ON DELETE CASCADE")
                cursor.execute("UPDATE attendance_raw_logs SET company_id = 1 WHERE company_id IS NULL")

            conn.commit()
        conn.close()
        print("Migration multi-entreprises exécutée avec succès !")
    except Exception as e:
        print(f"Erreur lors de la migration BDD : {e}")

if __name__ == "__main__":
    run_migration()