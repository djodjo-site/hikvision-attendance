def generate_monthly_report(employee_id: int, year_month: str):
    """
    Agrège les données journalières pour un employé et un mois donné (ex: '2026-11')
    et met à jour la table monthly_reports.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 1. Calculer les totaux du mois à partir de daily_summaries
            cursor.execute("""
                SELECT 
                    SUM(worked_hours) AS total_hours,
                    SUM(late_minutes) AS total_lates,
                    SUM(CASE WHEN status = 'ABSENT' THEN 1 ELSE 0 END) AS total_absences,
                    SUM(CASE WHEN status IN ('PRESENT', 'RETARD') THEN 1 ELSE 0 END) AS total_presents
                FROM daily_summaries
                WHERE employee_id = %s 
                  AND DATE_FORMAT(attendance_date, '%%Y-%%m') = %s
            """, (employee_id, year_month))
            
            stats = cursor.fetchone()
            
            if stats and stats['total_hours'] is not None:
                total_hours = stats['total_hours'] or 0.00
                total_lates = stats['total_lates'] or 0
                total_absences = stats['total_absences'] or 0
                total_presents = stats['total_presents'] or 0
            else:
                total_hours = 0.00
                total_lates = 0
                total_absences = 0
                total_presents = 0

            # 2. Insérer ou mettre à jour le rapport mensuel
            cursor.execute("""
                INSERT INTO monthly_reports (employee_id, `year_month`, total_worked_hours, total_late_minutes, absent_days_count, present_days_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    total_worked_hours = VALUES(total_worked_hours),
                    total_late_minutes = VALUES(total_late_minutes),
                    absent_days_count = VALUES(absent_days_count),
                    present_days_count = VALUES(present_days_count)
            """, (employee_id, year_month, total_hours, total_lates, total_absences, total_presents))
            
            conn.commit()
            print(f"Rapport mensuel généré avec succès pour l'employé {employee_id} ({year_month})")
            
    except Exception as e:
        print(f"Erreur lors de la génération du rapport mensuel : {e}")
        conn.rollback()
    finally:
        conn.close()