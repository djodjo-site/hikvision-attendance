import sys
import os
import json
import re
import io
import pandas as pd
from zoneinfo import ZoneInfo

# Ajout dynamique du dossier parent dans le chemin Python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import FastAPI, Request, Response, Form, Cookie, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from datetime import datetime, date, time, timedelta
from database.db_config import get_db_connection

app = FastAPI(title="Hikvision Attendance System")

# Route directe pour servir le logo ABS
@app.get("/static/logo.png")
async def get_logo():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_path = os.path.join(base_dir, "static", "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    return Response(status_code=404)

DRH_USERNAME = "drh"
DRH_PASSWORD = "password123"

# Fonction utilitaire pour initialiser les tables et horaires par défaut
def init_database_tables():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Table de renommage des appareils
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS devices_mapping (
                    device_key VARCHAR(100) PRIMARY KEY,
                    friendly_name VARCHAR(100) NOT NULL
                )
            """)
            # Table des horaires de travail
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_schedules (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    late_tolerance_minutes INT DEFAULT 30
                )
            """)
            
            # Insérer un horaire par défaut s'il n'en existe aucun
            cursor.execute("SELECT COUNT(*) as cnt FROM work_schedules")
            res = cursor.fetchone()
            if res and res['cnt'] == 0:
                cursor.execute("""
                    INSERT INTO work_schedules (name, start_time, end_time, late_tolerance_minutes)
                    VALUES ('Standard (08:00 - 17:00)', '08:00:00', '17:00:00', 30)
                """)
                
            # Vérifier si la colonne schedule_id existe dans employees, sinon l'ajouter
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='employees' and column_name='schedule_id'
            """)
            if not cursor.fetchone():
                cursor.execute("""
                    ALTER TABLE employees ADD COLUMN schedule_id INT REFERENCES work_schedules(id)
                """)

            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur initialisation tables BDD : {e}")

@app.on_event("startup")
async def startup_event():
    init_database_tables()

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = None):
    error_html = f'<div class="alert alert-danger py-2 mb-3 text-center small">{error}</div>' if error else ''
    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Connexion DRH - Hikvision</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{
                background: linear-gradient(135deg, #1e293b, #0f172a);
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }}
            .login-card {{
                width: 100%;
                max-width: 420px;
                border: none;
                border-radius: 16px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.3);
                background: #ffffff;
            }}
            .logo-container img {{
                max-height: 80px;
                width: auto;
                object-fit: contain;
            }}
            .developer-footer {{
                border-top: 1px solid #f1f5f9;
                padding-top: 15px;
                margin-top: 20px;
                font-size: 0.8rem;
                color: #64748b;
            }}
        </style>
    </head>
    <body>
        <div class="card login-card p-4">
            <div class="text-center mb-4 logo-container">
                <img src="/static/logo.png" alt="Logo ABS" class="mb-3">
                <h4 class="fw-bold text-dark">Espace DRH</h4>
                <p class="text-muted small">Veuillez vous connecter pour accéder au suivi</p>
            </div>
            {error_html}
            <form action="/login" method="POST">
                <div class="mb-3">
                    <label class="form-label text-secondary small fw-bold">Nom d'utilisateur</label>
                    <div class="input-group">
                        <span class="input-group-text bg-light"><i class="fa-solid fa-user text-muted"></i></span>
                        <input type="text" class="form-control" name="username" placeholder="ex: drh" required>
                    </div>
                </div>
                <div class="mb-4">
                    <label class="form-label text-secondary small fw-bold">Mot de passe</label>
                    <div class="input-group">
                        <span class="input-group-text bg-light"><i class="fa-solid fa-lock text-muted"></i></span>
                        <input type="password" class="form-control" name="password" placeholder="••••••••" required>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-right-to-bracket me-2"></i> Se connecter</button>
            </form>

            <div class="text-center developer-footer">
                <i class="fa-solid fa-code me-1 text-primary"></i> Application développée par <strong>Ir Jonathan LUKOKI</strong>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/login")
async def login_action(username: str = Form(...), password: str = Form(...)):
    if username == DRH_USERNAME and password == DRH_PASSWORD:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="drh_session", value="authenticated", httponly=True)
        return response
    else:
        return RedirectResponse(url="/login?error=Identifiants+incorrects", status_code=303)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="drh_session")
    return response

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, error: str = None, success: str = None, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    employees = []
    devices = []
    schedules = []
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            try:
                cursor.execute("""
                    SELECT e.*, s.name as schedule_name 
                    FROM employees e 
                    LEFT JOIN work_schedules s ON e.schedule_id = s.id
                    ORDER BY COALESCE(NULLIF(TRIM(e.department), ''), 'Non assigné') ASC, e.first_name ASC, e.last_name ASC
                """)
                employees = cursor.fetchall()
            except Exception:
                employees = []
                
            try:
                cursor.execute("SELECT * FROM work_schedules ORDER BY id ASC")
                schedules = cursor.fetchall()
            except Exception:
                schedules = []
                
            cursor.execute("SELECT DISTINCT device_id FROM attendance_raw_logs WHERE device_id IS NOT NULL")
            raw_devs = [row['device_id'] for row in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM devices_mapping")
            mappings = {row['device_key']: row['friendly_name'] for row in cursor.fetchall()}
            
            all_keys = set(raw_devs).union(set(mappings.keys()))
            
            for dev in all_keys:
                devices.append({
                    "key": dev,
                    "name": mappings.get(dev, dev)
                })
                
        conn.close()
    except Exception as e:
        print(f"Attention - Connexion BDD non établie : {e}")

    alert_html = ""
    if error:
        alert_html = f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="fa-solid fa-circle-exclamation me-2"></i>{error}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'
    elif success:
        alert_html = f'<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="fa-solid fa-circle-check me-2"></i>{success}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'

    emp_rows_html = ""
    dept_options_html = '<option value="ALL">Tous les départements</option>'
    if employees:
        from collections import OrderedDict
        grouped_emps = OrderedDict()
        for emp in employees:
            dept_label = (emp.get('department') or '').strip() or 'Non assigné'
            if dept_label not in grouped_emps:
                grouped_emps[dept_label] = []
            grouped_emps[dept_label].append(emp)

        for dept_label, dept_emps in grouped_emps.items():
            dept_options_html += f'<option value="{dept_label}">{dept_label} ({len(dept_emps)})</option>'
            emp_rows_html += f"""
                <tr class="table-light dept-group-header" data-dept="{dept_label}">
                    <td colspan="6" class="ps-3 py-2 fw-bold text-primary border-top border-bottom">
                        <i class="fa-solid fa-building me-2"></i> Département : {dept_label}
                        <span class="badge bg-primary bg-opacity-10 text-primary ms-2 dept-count-badge">{len(dept_emps)} employé(s)</span>
                    </td>
                </tr>
            """
            for emp in dept_emps:
                is_act = emp.get('is_active', True)
                status_badge = '<span class="badge bg-success bg-opacity-10 text-success">Actif</span>' if is_act else '<span class="badge bg-danger bg-opacity-10 text-danger">Inactif</span>'
                full_name = f"{emp['first_name']} {emp['last_name']}".strip()
                search_text = f"{full_name} {emp['badge_or_card_id']} {dept_label}".lower()
                emp_rows_html += f"""
                    <tr class="emp-data-row" data-dept="{dept_label}" data-search="{search_text}">
                        <td class="ps-3"><code>{emp['badge_or_card_id']}</code></td>
                        <td class="fw-semibold">{full_name}</td>
                        <td><span class="badge bg-secondary bg-opacity-10 text-secondary">{dept_label}</span></td>
                        <td><span class="badge bg-light text-dark border">{emp.get('schedule_name') or 'Standard'}</span></td>
                        <td>{status_badge}</td>
                        <td class="pe-3 text-end">
                            <a href="/employees/edit/{emp['badge_or_card_id']}" class="btn btn-sm btn-outline-primary me-1" title="Modifier"><i class="fa-solid fa-pen-to-square"></i></a>
                            <a href="/employees/delete/{emp['badge_or_card_id']}" class="btn btn-sm btn-outline-danger" title="Supprimer" onclick="return confirm('Voulez-vous vraiment supprimer cet employé ?');"><i class="fa-solid fa-trash"></i></a>
                        </td>
                    </tr>
                """
    else:
        emp_rows_html = """
                <tr id="no-emp-initial">
                    <td colspan="6" class="text-center py-4 text-muted">Aucun employé trouvé.</td>
                </tr>
        """

    sched_rows_html = ""
    sched_options_html = ""
    for s in schedules:
        sched_options_html += f'<option value="{s["id"]}">{s["name"]} ({str(s["start_time"])[:5]} - {str(s["end_time"])[:5]})</option>'
        sched_rows_html += f"""
            <tr>
                <td class="ps-3 fw-semibold">{s['name']}</td>
                <td><span class="badge bg-primary bg-opacity-10 text-primary">{str(s['start_time'])[:5]}</span></td>
                <td><span class="badge bg-secondary bg-opacity-10 text-secondary">{str(s['end_time'])[:5]}</span></td>
                <td>{s['late_tolerance_minutes']} min</td>
                <td class="text-end pe-3">
                    <a href="/schedules/delete/{s['id']}" class="btn btn-sm btn-outline-danger" title="Supprimer cet horaire" onclick="return confirm('Voulez-vous supprimer cet horaire ?');"><i class="fa-solid fa-trash"></i></a>
                </td>
            </tr>
        """

    dev_rows_html = ""
    if devices:
        for d in devices:
            dev_rows_html += f"""
                <tr>
                    <td class="ps-3"><code>{d['key']}</code></td>
                    <td>
                        <form action="/devices/rename" method="POST" class="d-flex gap-2">
                            <input type="hidden" name="device_key" value="{d['key']}">
                            <input type="text" class="form-control form-control-sm" name="friendly_name" value="{d['name']}" required>
                            <button type="submit" class="btn btn-sm btn-success text-nowrap"><i class="fa-solid fa-floppy-disk"></i> Enregistrer</button>
                        </form>
                    </td>
                    <td class="text-end pe-3">
                        <a href="/devices/delete/{d['key']}" class="btn btn-sm btn-outline-danger" title="Supprimer ce terminal" onclick="return confirm('Voulez-vous vraiment supprimer ce terminal ?');"><i class="fa-solid fa-trash"></i></a>
                    </td>
                </tr>
            """
    else:
        dev_rows_html = '<tr><td colspan="3" class="text-center py-3 text-muted">Aucun terminal détecté pour l\'instant. Effectuez un pointage.</td></tr>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Gestion de Présence - Hikvision</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{
                background-color: #f4f7f6;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }}
            .navbar {{
                background: linear-gradient(135deg, #1e293b, #0f172a) !important;
            }}
            .card {{
                border: none;
                border-radius: 12px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            }}
            .card-header {{
                background-color: #ffffff;
                border-bottom: 1px solid #f1f5f9;
                border-top-left-radius: 12px !important;
                border-top-right-radius: 12px !important;
                font-weight: 600;
            }}
            .table th {{
                font-weight: 600;
                color: #475569;
                background-color: #f8fafc !important;
            }}
            .badge {{
                padding: 0.5em 0.8em;
                font-weight: 500;
                border-radius: 6px;
            }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3">
            <div class="container-fluid px-4">
                <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-clock-rotate-left me-2"></i> Suivi de Présence & Pointage Hikvision</span>
                <div class="d-flex align-items-center gap-2">
                    <a href="/" class="btn btn-outline-light btn-sm px-3 active"><i class="fa-solid fa-house me-1"></i> Tableau de bord</a>
                    <a href="/reports" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-calendar-days me-1"></i> Rapport Journalier</a>
                    <a href="/summary" class="btn btn-light btn-sm px-3 fw-semibold"><i class="fa-solid fa-chart-pie me-1"></i> Synthèse Employés</a>
                    <a href="/logout" class="btn btn-danger btn-sm px-3 ms-2"><i class="fa-solid fa-right-from-bracket me-1"></i> Déconnexion</a>
                </div>
            </div>
        </nav>

        <div class="container-fluid px-4 my-4">
            {alert_html}

            <div class="row g-4 mb-4">
                <div class="col-md-4">
                    <div class="card border-start border-primary border-4">
                        <div class="card-body d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted fw-semibold mb-1">Employés Enregistrés</h6>
                                <h3 class="fw-bold text-dark mb-0">{len(employees)}</h3>
                            </div>
                            <div class="text-primary bg-primary bg-opacity-10 p-3 rounded-circle">
                                <i class="fa-solid fa-users fa-xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card border-start border-success border-4">
                        <div class="card-body d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted fw-semibold mb-1">Statut du Système</h6>
                                <h3 class="fw-bold text-success mb-0">En ligne 🟢</h3>
                            </div>
                            <div class="text-success bg-success bg-opacity-10 p-3 rounded-circle">
                                <i class="fa-solid fa-server fa-xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card border-start border-info border-4">
                        <div class="card-body">
                            <h6 class="text-muted fw-semibold mb-1">Terminaux Connectés</h6>
                            <h3 class="fw-bold text-info mb-0">{len(devices)} détecté(s)</h3>
                        </div>
                    </div>
                </div>
            </div>

            <!-- SECTION IMPORTATION MASSIVE EXCEL/CSV -->
            <div class="card shadow-sm mb-4">
                <div class="card-header bg-dark text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-file-excel me-2 text-success"></i> Importation de la liste des Employés (Fichier Excel / CSV)</h5>
                </div>
                <div class="card-body p-4">
                    <form action="/employees/import" method="POST" enctype="multipart/form-data" class="row g-3 align-items-center">
                        <div class="col-md-8">
                            <label class="form-label text-secondary small fw-bold">Sélectionner un fichier (.xlsx, .xls, .csv)</label>
                            <input type="file" class="form-control" name="file" accept=".xlsx, .xls, .csv" required>
                            <div class="form-text">
                                Colonnes attendues dans votre fichier : <code>badge</code> (ou matricule), <code>prenom</code>, <code>nom</code>, <code>departement</code>.
                            </div>
                        </div>
                        <div class="col-md-4 d-flex align-items-end">
                            <button type="submit" class="btn btn-success w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-file-import me-2"></i> Importer les Employés</button>
                        </div>
                    </form>
                </div>
            </div>

            <!-- SECTION GESTION DES HORAIRES DE TRAVAIL -->
            <div class="row g-4 mb-4">
                <div class="col-lg-5">
                    <div class="card h-100">
                        <div class="card-header bg-success text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-clock me-2"></i> Configurer un Horaire de Travail</h5>
                        </div>
                        <div class="card-body p-4">
                            <form action="/schedules/add" method="POST">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Nom de l'horaire</label>
                                    <input type="text" class="form-control" name="name" placeholder="ex: Horaire Normal, Équipe Nuit" required>
                                </div>
                                <div class="row">
                                    <div class="col-md-6 mb-3">
                                        <label class="form-label text-secondary small fw-bold">Heure d'arrivée</label>
                                        <input type="time" class="form-control" name="start_time" value="08:00" required>
                                    </div>
                                    <div class="col-md-6 mb-3">
                                        <label class="form-label text-secondary small fw-bold">Heure de départ</label>
                                        <input type="time" class="form-control" name="end_time" value="17:00" required>
                                    </div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Tolérance de retard (minutes)</label>
                                    <input type="number" class="form-control" name="late_tolerance_minutes" value="30" required>
                                </div>
                                <button type="submit" class="btn btn-success w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-plus me-2"></i> Ajouter l'horaire</button>
                            </form>
                        </div>
                    </div>
                </div>
                <div class="col-lg-7">
                    <div class="card h-100">
                        <div class="card-header bg-dark text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-business-time me-2"></i> Horaires configurés</h5>
                        </div>
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th class="ps-3">Nom</th>
                                            <th>Entrée</th>
                                            <th>Sortie</th>
                                            <th>Tolérance</th>
                                            <th class="text-end pe-3">Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {sched_rows_html}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- SECTION TERMINAUX -->
            <div class="card shadow-sm mb-4">
                <div class="card-header bg-secondary text-white py-3 d-flex justify-content-between align-items-center">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-network-wired me-2"></i> Gestion & Ajout Manuel des Terminaux de Pointage</h5>
                </div>
                <div class="card-body bg-light border-bottom p-3">
                    <form action="/devices/add" method="POST" class="row g-2 align-items-center">
                        <div class="col-md-5">
                            <input type="text" class="form-control form-control-sm" name="device_key" placeholder="ID Technique (ex: Terminal_Porte1 ou Serial No)" required>
                        </div>
                        <div class="col-md-5">
                            <input type="text" class="form-control form-control-sm" name="friendly_name" placeholder="Nom personnalisé (ex: Porte Principale)" required>
                        </div>
                        <div class="col-md-2">
                            <button type="submit" class="btn btn-sm btn-primary w-100 fw-semibold"><i class="fa-solid fa-plus me-1"></i> Ajouter</button>
                        </div>
                    </form>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle mb-0">
                            <thead>
                                <tr>
                                    <th class="ps-3">ID Technique / IP d'origine</th>
                                    <th>Nom Personnalisé (Ex: Porte Principale, Entrepôt...)</th>
                                    <th class="text-end pe-3">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {dev_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- SECTION EMPLOYÉS -->
            <div class="row g-4">
                <div class="col-lg-4">
                    <div class="card">
                        <div class="card-header bg-primary text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-user-plus me-2"></i> Enregistrer un Nouvel Employé</h5>
                        </div>
                        <div class="card-body p-4">
                            <form action="/employees/add" method="POST">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Matricule / ID Badge</label>
                                    <input type="text" class="form-control" name="badge_or_card_id" placeholder="ex: 02132" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Prénom</label>
                                    <input type="text" class="form-control" name="first_name" placeholder="ex: Jonathan" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Nom</label>
                                    <input type="text" class="form-control" name="last_name" placeholder="ex: Lukoki" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Département</label>
                                    <input type="text" class="form-control" name="department" placeholder="ex: Informatique">
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Horaire de travail assigné</label>
                                    <select class="form-select" name="schedule_id">
                                        {sched_options_html}
                                    </select>
                                </div>
                                <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-check me-2"></i> Enregistrer l'employé</button>
                            </form>
                        </div>
                    </div>
                </div>

                <div class="col-lg-8">
                    <div class="card mt-0">
                        <div class="card-header py-3 d-flex flex-wrap justify-content-between align-items-center gap-2">
                            <h5 class="mb-0 fs-6 fw-bold text-dark"><i class="fa-solid fa-users-gear me-2 text-primary"></i> Liste des Employés par Département</h5>
                            <a href="/summary" class="btn btn-sm btn-outline-primary fw-semibold"><i class="fa-solid fa-chart-pie me-1"></i> Voir la synthèse globale</a>
                        </div>
                        <div class="card-body bg-light border-bottom py-2 px-3">
                            <div class="row g-2 align-items-center">
                                <div class="col-md-6">
                                    <div class="input-group input-group-sm">
                                        <span class="input-group-text bg-white"><i class="fa-solid fa-magnifying-glass text-muted"></i></span>
                                        <input type="text" id="empSearchInput" class="form-control" placeholder="Filtrer par nom, prénom ou matricule...">
                                        <button class="btn btn-outline-secondary" type="button" id="clearEmpSearch" title="Effacer"><i class="fa-solid fa-xmark"></i></button>
                                    </div>
                                </div>
                                <div class="col-md-4">
                                    <select id="empDeptFilter" class="form-select form-select-sm">
                                        {dept_options_html}
                                    </select>
                                </div>
                                <div class="col-md-2 text-end">
                                    <span id="empFilterCounter" class="badge bg-primary bg-opacity-10 text-primary w-100 py-2">{len(employees)} au total</span>
                                </div>
                            </div>
                        </div>
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-hover align-middle mb-0" id="employeesTable">
                                    <thead>
                                        <tr>
                                            <th class="ps-3">Matricule / Badge</th>
                                            <th>Nom & Prénom</th>
                                            <th>Département</th>
                                            <th>Horaire assigné</th>
                                            <th>Statut</th>
                                            <th class="pe-3 text-end">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody id="employeesTableBody">
                                        {emp_rows_html}
                                        <tr id="noEmpSearchResult" style="display: none;">
                                            <td colspan="6" class="text-center py-4 text-muted">Aucun employé ne correspond à votre recherche.</td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                        <div class="card-footer bg-white d-flex flex-wrap justify-content-between align-items-center py-2 px-3 gap-2">
                            <small class="text-muted fw-semibold" id="empPaginationInfo">Affichage 0 - 0 sur 0</small>
                            <nav aria-label="Pagination des employés">
                                <ul class="pagination pagination-sm mb-0" id="empPaginationControls"></ul>
                            </nav>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            document.addEventListener("DOMContentLoaded", function() {{
                const ITEMS_PER_PAGE = 10;
                let currentPage = 1;

                const searchInput = document.getElementById("empSearchInput");
                const clearSearchBtn = document.getElementById("clearEmpSearch");
                const deptFilter = document.getElementById("empDeptFilter");
                const allEmpRows = Array.from(document.querySelectorAll(".emp-data-row"));
                const allDeptHeaders = Array.from(document.querySelectorAll(".dept-group-header"));
                const noResultRow = document.getElementById("noEmpSearchResult");
                const paginationInfo = document.getElementById("empPaginationInfo");
                const paginationControls = document.getElementById("empPaginationControls");
                const filterCounter = document.getElementById("empFilterCounter");

                function updateEmployeesTable() {{
                    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
                    const selectedDept = deptFilter ? deptFilter.value : "ALL";

                    // 1. Filtrer les employés correspondants
                    const matchingRows = allEmpRows.filter(row => {{
                        const rowDept = row.getAttribute("data-dept");
                        const rowSearch = row.getAttribute("data-search") || "";
                        const matchDept = (selectedDept === "ALL" || rowDept === selectedDept);
                        const matchQuery = (!query || rowSearch.includes(query));
                        return matchDept && matchQuery;
                    }});

                    const totalItems = matchingRows.length;
                    const totalPages = Math.max(1, Math.ceil(totalItems / ITEMS_PER_PAGE));
                    if (currentPage > totalPages) currentPage = totalPages;

                    const startIndex = (currentPage - 1) * ITEMS_PER_PAGE;
                    const endIndex = startIndex + ITEMS_PER_PAGE;
                    const visiblePageRows = new Set(matchingRows.slice(startIndex, endIndex));

                    // 2. Afficher uniquement les lignes de la page courante
                    const visibleDepts = new Set();
                    allEmpRows.forEach(row => {{
                        if (visiblePageRows.has(row)) {{
                            row.style.display = "";
                            visibleDepts.add(row.getAttribute("data-dept"));
                        }} else {{
                            row.style.display = "none";
                        }}
                    }});

                    // 3. Afficher uniquement les en-têtes des départements ayant au moins un employé sur la page
                    allDeptHeaders.forEach(header => {{
                        const hDept = header.getAttribute("data-dept");
                        header.style.display = visibleDepts.has(hDept) ? "" : "none";
                    }});

                    // 4. Gestion message vide et compteurs
                    if (noResultRow) {{
                        noResultRow.style.display = (allEmpRows.length > 0 && totalItems === 0) ? "" : "none";
                    }}
                    if (filterCounter) {{
                        filterCounter.textContent = totalItems + " employé(s)";
                    }}
                    if (paginationInfo) {{
                        if (totalItems === 0) {{
                            paginationInfo.textContent = "Affichage 0 - 0 sur 0";
                        }} else {{
                            const showFrom = startIndex + 1;
                            const showTo = Math.min(endIndex, totalItems);
                            paginationInfo.textContent = `Affichage ${{showFrom}} - ${{showTo}} sur ${{totalItems}}`;
                        }}
                    }}

                    // 5. Générer les boutons de pagination
                    if (paginationControls) {{
                        paginationControls.innerHTML = "";
                        if (totalItems > ITEMS_PER_PAGE) {{
                            const createPageItem = (label, pageNum, disabled = false, active = false) => {{
                                const li = document.createElement("li");
                                li.className = "page-item" + (disabled ? " disabled" : "") + (active ? " active" : "");
                                const a = document.createElement("a");
                                a.className = "page-link";
                                a.href = "#";
                                a.innerHTML = label;
                                a.addEventListener("click", function(e) {{
                                    e.preventDefault();
                                    if (!disabled && !active) {{
                                        currentPage = pageNum;
                                        updateEmployeesTable();
                                    }}
                                }});
                                li.appendChild(a);
                                return li;
                            }};

                            paginationControls.appendChild(createPageItem("&laquo;", currentPage - 1, currentPage === 1));

                            for (let p = 1; p <= totalPages; p++) {{
                                paginationControls.appendChild(createPageItem(String(p), p, false, p === currentPage));
                            }}

                            paginationControls.appendChild(createPageItem("&raquo;", currentPage + 1, currentPage === totalPages));
                        }}
                    }}
                }}

                if (searchInput) {{
                    searchInput.addEventListener("input", function() {{
                        currentPage = 1;
                        updateEmployeesTable();
                    }});
                }}
                if (clearSearchBtn) {{
                    clearSearchBtn.addEventListener("click", function() {{
                        searchInput.value = "";
                        currentPage = 1;
                        updateEmployeesTable();
                    }});
                }}
                if (deptFilter) {{
                    deptFilter.addEventListener("change", function() {{
                        currentPage = 1;
                        updateEmployeesTable();
                    }});
                }}

                updateEmployeesTable();
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# --- ROUTE IMPORTATION D'EMPLOYÉS (EXCEL / CSV) ---
@app.post("/employees/import")
async def import_employees(file: UploadFile = File(...), drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    try:
        contents = await file.read()
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        elif filename.endswith('.xlsx') or filename.endswith('.xls'):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            return RedirectResponse(url="/?error=Format+de+fichier+non+supporté.+Veuillez+utiliser+.xlsx,+xls+ou+.csv", status_code=303)

        df.columns = [str(col).strip().lower() for col in df.columns]

        col_badge = next((c for c in df.columns if c in ['badge', 'badge_id', 'matricule', 'id', 'cardno', 'card_no', 'employee_id']), None)
        col_prenom = next((c for c in df.columns if c in ['prenom', 'prénom', 'first_name', 'firstname', 'prenoms']), None)
        col_nom = next((c for c in df.columns if c in ['nom', 'last_name', 'lastname', 'name']), None)
        col_dept = next((c for c in df.columns if c in ['departement', 'département', 'department', 'service', 'dept']), None)

        if not col_badge or not col_nom:
            return RedirectResponse(
                url="/?error=Le+fichier+doit+contenir+au+moins+les+colonnes+matricule+(ou+badge)+et+nom", 
                status_code=303
            )

        imported_count = 0
        conn = get_db_connection()
        with conn.cursor() as cursor:
            for _, row in df.iterrows():
                badge_val = str(row[col_badge]).strip() if pd.notna(row[col_badge]) else ""
                if not badge_val or badge_val.lower() == 'nan':
                    continue

                prenom_val = str(row[col_prenom]).strip() if col_prenom and pd.notna(row[col_prenom]) else ""
                nom_val = str(row[col_nom]).strip() if col_nom and pd.notna(row[col_nom]) else ""
                dept_val = str(row[col_dept]).strip() if col_dept and pd.notna(row[col_dept]) else None

                cursor.execute(
                    """
                    INSERT INTO employees (badge_or_card_id, first_name, last_name, department, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (badge_or_card_id) DO UPDATE 
                    SET first_name = EXCLUDED.first_name, 
                        last_name = EXCLUDED.last_name, 
                        department = EXCLUDED.department
                    """,
                    (badge_val, prenom_val, nom_val, dept_val)
                )
                imported_count += 1
            conn.commit()
        conn.close()

        return RedirectResponse(url=f"/?success={imported_count}+employé(s)+importé(s)+avec+succès", status_code=303)

    except Exception as e:
        print(f"Erreur lors de l'importation Excel : {e}")
        return RedirectResponse(url=f"/?error=Erreur+de+lecture+du+fichier:+{str(e)}", status_code=303)


# --- ROUTES GESTION DES HORAIRES ---
@app.post("/schedules/add")
async def add_schedule(
    name: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    late_tolerance_minutes: int = Form(30),
    drh_session: str = Cookie(None)
):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO work_schedules (name, start_time, end_time, late_tolerance_minutes)
                VALUES (%s, %s, %s, %s)
            """, (name.strip(), start_time, end_time, late_tolerance_minutes))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur ajout horaire : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/schedules/delete/{sched_id}")
async def delete_schedule(sched_id: int, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("UPDATE employees SET schedule_id = NULL WHERE schedule_id = %s", (sched_id,))
            cursor.execute("DELETE FROM work_schedules WHERE id = %s", (sched_id,))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur suppression horaire : {e}")
    return RedirectResponse(url="/", status_code=303)


@app.post("/devices/add")
async def add_device_manual(device_key: str = Form(...), friendly_name: str = Form(...), drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO devices_mapping (device_key, friendly_name)
                VALUES (%s, %s)
                ON CONFLICT (device_key) DO UPDATE SET friendly_name = EXCLUDED.friendly_name
            """, (device_key.strip(), friendly_name.strip()))
            conn.commit()
        conn.close()
        return RedirectResponse(url="/?success=Terminal+ajouté+avec+succès", status_code=303)
    except Exception as e:
        print(f"Erreur ajout manuel terminal : {e}")
        return RedirectResponse(url=f"/?error=Erreur+lors+de+l'ajout+du+terminal:+{str(e)}", status_code=303)

@app.post("/devices/rename")
async def rename_device(device_key: str = Form(...), friendly_name: str = Form(...), drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO devices_mapping (device_key, friendly_name)
                VALUES (%s, %s)
                ON CONFLICT (device_key) DO UPDATE SET friendly_name = EXCLUDED.friendly_name
            """, (device_key.strip(), friendly_name.strip()))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors du renommage du terminal : {e}")
    return RedirectResponse(url="/", status_code=303)


@app.get("/devices/delete/{device_key}")
async def delete_device(device_key: str, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM devices_mapping WHERE device_key = %s", (device_key,))
            cursor.execute("DELETE FROM attendance_raw_logs WHERE device_id = %s", (device_key,))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la suppression du terminal : {e}")

    return RedirectResponse(url="/", status_code=303)


@app.get("/reports/delete/{log_id}")
async def delete_attendance_log(log_id: int, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM attendance_raw_logs WHERE id = %s", (log_id,))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la suppression du pointage : {e}")

    return RedirectResponse(url="/reports", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
async def attendance_reports(request: Request, start_date: str = None, end_date: str = None, device_id: str = None, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    reports = []
    devices = []
    today_str = date.today().isoformat()
    
    if not start_date:
        start_date = today_str
    if not end_date:
        end_date = today_str

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM devices_mapping")
            mappings = {row['device_key']: row['friendly_name'] for row in cursor.fetchall()}

            cursor.execute("SELECT DISTINCT device_id FROM attendance_raw_logs WHERE device_id IS NOT NULL ORDER BY device_id")
            devices = [{"key": row['device_id'], "name": mappings.get(row['device_id'], row['device_id'])} for row in cursor.fetchall()]

            base_query = """
                SELECT 
                    l.id,
                    l.badge_or_card_id,
                    COALESCE(e.first_name, 'Inconnu') as first_name,
                    COALESCE(e.last_name, '') as last_name,
                    COALESCE(e.department, '-') as department,
                    s.start_time as schedule_start,
                    s.end_time as schedule_end,
                    s.late_tolerance_minutes,
                    DATE(l.punch_timestamp) as punch_date,
                    l.punch_timestamp as exact_punch,
                    l.device_id
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id
                LEFT JOIN work_schedules s ON e.schedule_id = s.id
                WHERE DATE(l.punch_timestamp) BETWEEN %s AND %s
            """
            
            params = [start_date, end_date]
            
            if device_id and device_id != "ALL":
                base_query += " AND l.device_id = %s"
                params.append(device_id)

            base_query += " ORDER BY l.punch_timestamp DESC"
            
            cursor.execute(base_query, tuple(params))
            raw_logs = cursor.fetchall()

            grouped = {}
            for r in raw_logs:
                key = (r['badge_or_card_id'], str(r['punch_date']), r['device_id'])
                if key not in grouped:
                    grouped[key] = {
                        "badge_or_card_id": r['badge_or_card_id'],
                        "first_name": r['first_name'],
                        "last_name": r['last_name'],
                        "schedule_start": r['schedule_start'] or time(8, 0),
                        "schedule_end": r['schedule_end'] or time(17, 0),
                        "late_tolerance_minutes": r['late_tolerance_minutes'] if r['late_tolerance_minutes'] is not None else 30,
                        "punch_date": r['punch_date'],
                        "device_id": r['device_id'],
                        "timestamps": []
                    }
                grouped[key]["timestamps"].append((r['id'], r['exact_punch']))

            for key, data in grouped.items():
                sorted_ts = sorted(data["timestamps"], key=lambda x: x[1])
                check_in_item = sorted_ts[0]
                check_out_item = sorted_ts[-1] if len(sorted_ts) > 1 else None

                reports.append({
                    "badge_or_card_id": data["badge_or_card_id"],
                    "first_name": data["first_name"],
                    "last_name": data["last_name"],
                    "schedule_start": data["schedule_start"],
                    "schedule_end": data["schedule_end"],
                    "late_tolerance_minutes": data["late_tolerance_minutes"],
                    "punch_date": data["punch_date"],
                    "check_in_id": check_in_item[0],
                    "check_in": check_in_item[1],
                    "check_out_id": check_out_item[0] if check_out_item else None,
                    "check_out": check_out_item[1] if check_out_item else None,
                    "device_id": data["device_id"]
                })

        conn.close()
    except Exception as e:
        print(f"Erreur lors de la génération du rapport : {e}")

    report_rows_html = ""
    if reports:
        for row in reports:
            emp_name = f"{row['first_name']} {row['last_name']}".strip()
            check_in = row['check_in']
            check_out = row['check_out']
            raw_dev = row.get('device_id', '-')
            dev_display = mappings.get(raw_dev, raw_dev) if raw_dev in mappings else raw_dev
            
            check_in_str = str(check_in).split(' ')[1] if check_in else '-'
            check_out_str = str(check_out).split(' ')[1] if check_out else '-'

            del_id = row['check_out_id'] if row['check_out_id'] else row['check_in_id']

            sched_start = row['schedule_start']
            sched_end = row['schedule_end']
            tolerance = row['late_tolerance_minutes']

            if not check_in or not check_out or check_in == check_out:
                report_rows_html += f"""
                            <tr>
                                <td><strong>{row['punch_date']}</strong></td>
                                <td><code>{row['badge_or_card_id']}</code></td>
                                <td class="text-start ps-4 fw-semibold">{emp_name}</td>
                                <td><span class="badge bg-danger text-white">Retard/Manquant ({check_in_str})</span></td>
                                <td><span class="text-muted fst-italic">Manquant</span></td>
                                <td>-</td>
                                <td><span class="badge bg-secondary">{dev_display}</span></td>
                                <td><span class="badge bg-danger text-white">Absent (Incomplet)</span></td>
                                <td class="no-print">
                                    <a href="/reports/delete/{del_id}" class="btn btn-sm btn-outline-danger" title="Supprimer ce pointage" onclick="return confirm('Voulez-vous vraiment supprimer ce pointage ?');"><i class="fa-solid fa-trash"></i></a>
                                </td>
                            </tr>
                """
                continue

            check_in_t = check_in.time()
            check_out_t = check_out.time()

            duration = check_out - check_in
            total_seconds = int(duration.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            work_duration = f"{hours}h {minutes:02d}m"

            max_arrival_dt = datetime.combine(date.today(), sched_start) + timedelta(minutes=tolerance)
            is_arrival_valid = check_in.time() <= max_arrival_dt.time()
            is_departure_valid = check_out_t >= sched_end

            if is_arrival_valid:
                arr_badge = f'<span class="badge bg-success text-white">{check_in_str}</span>'
            else:
                arr_badge = f'<span class="badge bg-danger text-white">En retard ({check_in_str})</span>'

            if is_departure_valid:
                dep_badge = f'<span class="badge bg-success text-white">{check_out_str}</span>'
            else:
                dep_badge = f'<span class="badge bg-warning text-dark">Sortie anticipée ({check_out_str})</span>'

            if is_arrival_valid and is_departure_valid:
                global_status = '<span class="badge bg-success text-white">Présent (Conforme)</span>'
            else:
                global_status = '<span class="badge bg-danger text-white">Non conforme</span>'

            report_rows_html += f"""
                            <tr>
                                <td><strong>{row['punch_date']}</strong></td>
                                <td><code>{row['badge_or_card_id']}</code></td>
                                <td class="text-start ps-4 fw-semibold">{emp_name}</td>
                                <td>{arr_badge}</td>
                                <td>{dep_badge}</td>
                                <td><strong>{work_duration}</strong></td>
                                <td><span class="badge bg-light text-dark border">{dev_display}</span></td>
                                <td>{global_status}</td>
                                <td class="no-print">
                                    <a href="/reports/delete/{del_id}" class="btn btn-sm btn-outline-danger" title="Supprimer ce pointage" onclick="return confirm('Voulez-vous vraiment supprimer ce pointage ?');"><i class="fa-solid fa-trash"></i></a>
                                </td>
                            </tr>
            """
    else:
        report_rows_html = """
                            <tr>
                                <td colspan="9" class="text-center py-4 text-muted">Aucun pointage trouvé pour cette période.</td>
                            </tr>
        """

    device_options = '<option value="ALL">Tous les terminaux</option>'
    for d in devices:
        selected = 'selected' if device_id == d['key'] else ''
        device_options += f'<option value="{d["key"]}" {selected}>{d["name"]}</option>'

    dev_param = f"&device_id={device_id}" if device_id else ""
    export_excel_url = f"/reports/export/excel?start_date={start_date}&end_date={end_date}{dev_param}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Rapport Journalier - Hikvision</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{
                background-color: #f4f7f6;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }}
            .navbar {{
                background: linear-gradient(135deg, #1e293b, #0f172a) !important;
            }}
            .card {{
                border: none;
                border-radius: 12px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            }}
            .card-header {{
                background-color: #ffffff;
                border-bottom: 1px solid #f1f5f9;
                border-top-left-radius: 12px !important;
                border-top-right-radius: 12px !important;
                font-weight: 600;
            }}
            .table th {{
                font-weight: 600;
                color: #475569;
                background-color: #f8fafc !important;
            }}
            @media print {{
                @page {{
                    size: landscape;
                    margin: 5mm;
                }}
                .no-print {{
                    display: none !important;
                }}
                body {{
                    background-color: white !important;
                    font-size: 11pt;
                    width: 100% !important;
                    margin: 0 !important;
                }}
                .container-fluid {{
                    max-width: 100% !important;
                    width: 100% !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }}
                .card {{
                    border: none !important;
                    box-shadow: none !important;
                    width: 100% !important;
                }}
                .card-body {{
                    padding: 0 !important;
                }}
                .table-responsive {{
                    overflow: visible !important;
                    width: 100% !important;
                }}
                .table {{
                    width: 100% !important;
                    max-width: 100% !important;
                }}
            }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3 no-print">
            <div class="container-fluid px-4">
                <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-calendar-days me-2"></i> Rapport Journalier Détaillé</span>
                <div class="d-flex align-items-center gap-2">
                    <a href="/" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-house me-1"></i> Tableau de bord</a>
                    <a href="/reports" class="btn btn-light btn-sm px-3 fw-semibold"><i class="fa-solid fa-calendar-days me-1"></i> Rapport Journalier</a>
                    <a href="/summary" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-chart-pie me-1"></i> Synthèse Employés</a>
                    <a href="/logout" class="btn btn-danger btn-sm px-3 ms-2"><i class="fa-solid fa-right-from-bracket me-1"></i> Déconnexion</a>
                </div>
            </div>
        </nav>

        <div class="container-fluid px-4 my-4">
            <div class="card shadow-sm mb-4 no-print">
                <div class="card-header bg-primary text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-filter me-2"></i> Filtrer le rapport journalier</h5>
                </div>
                <div class="card-body p-4">
                    <form method="GET" action="/reports" class="row g-3">
                        <div class="col-md-4">
                            <label class="form-label text-secondary small fw-bold">Date de début</label>
                            <input type="date" class="form-control" name="start_date" value="{start_date}" required>
                        </div>
                        <div class="col-md-4">
                            <label class="form-label text-secondary small fw-bold">Date de fin</label>
                            <input type="date" class="form-control" name="end_date" value="{end_date}" required>
                        </div>
                        <div class="col-md-2">
                            <label class="form-label text-secondary small fw-bold">Terminal</label>
                            <select class="form-select" name="device_id">
                                {device_options}
                            </select>
                        </div>
                        <div class="col-md-2 d-flex align-items-end">
                            <button type="submit" class="btn btn-success w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-magnifying-glass me-1"></i> Filtrer</button>
                        </div>
                    </form>
                </div>
            </div>

            <div class="card shadow-sm">
                <div class="card-header d-flex justify-content-between align-items-center py-3">
                    <h5 class="mb-0 fs-6 fw-bold text-dark"><i class="fa-solid fa-clipboard-user me-2 text-primary"></i> Détail des pointages ({len(reports)} enregistrements)</h5>
                    <div class="d-flex gap-2 no-print">
                        <a href="{export_excel_url}" class="btn btn-success btn-sm fw-semibold"><i class="fa-solid fa-file-excel me-1"></i> Exporter Excel (.xlsx)</a>
                        <button onclick="window.print();" class="btn btn-outline-dark btn-sm fw-semibold"><i class="fa-solid fa-print me-1"></i> Imprimer / PDF</button>
                    </div>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-striped table-hover mb-0 align-middle text-center">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Matricule</th>
                                    <th class="text-start ps-4">Employé</th>
                                    <th>Arrivée</th>
                                    <th>Départ</th>
                                    <th>Heures</th>
                                    <th>Terminal</th>
                                    <th>Statut de Conformité</th>
                                    <th class="no-print">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {report_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- ROUTE D'EXPORTATION EXCEL DU RAPPORT JOURNALIER ---
@app.get("/reports/export/excel")
async def export_reports_excel(start_date: str = None, end_date: str = None, device_id: str = None, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    today_str = date.today().isoformat()
    start_date = start_date or today_str
    end_date = end_date or today_str

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM devices_mapping")
            mappings = {row['device_key']: row['friendly_name'] for row in cursor.fetchall()}

            base_query = """
                SELECT 
                    l.badge_or_card_id,
                    COALESCE(e.first_name, 'Inconnu') as first_name,
                    COALESCE(e.last_name, '') as last_name,
                    COALESCE(e.department, '-') as department,
                    s.start_time as schedule_start,
                    s.end_time as schedule_end,
                    s.late_tolerance_minutes,
                    DATE(l.punch_timestamp) as punch_date,
                    l.punch_timestamp as exact_punch,
                    l.device_id
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id
                LEFT JOIN work_schedules s ON e.schedule_id = s.id
                WHERE DATE(l.punch_timestamp) BETWEEN %s AND %s
            """
            params = [start_date, end_date]
            if device_id and device_id != "ALL":
                base_query += " AND l.device_id = %s"
                params.append(device_id)

            base_query += " ORDER BY l.punch_timestamp DESC"
            cursor.execute(base_query, tuple(params))
            raw_logs = cursor.fetchall()
        conn.close()

        grouped = {}
        for r in raw_logs:
            key = (r['badge_or_card_id'], str(r['punch_date']), r['device_id'])
            if key not in grouped:
                grouped[key] = {"timestamps": [], "row": r}
            grouped[key]["timestamps"].append(r['exact_punch'])

        export_data = []
        for key, info in grouped.items():
            r = info["row"]
            sorted_ts = sorted(info["timestamps"])
            cin = sorted_ts[0]
            cout = sorted_ts[-1] if len(sorted_ts) > 1 else None

            cin_str = str(cin).split(' ')[1] if cin else '-'
            cout_str = str(cout).split(' ')[1] if cout else '-'
            emp_name = f"{r['first_name']} {r['last_name']}".strip()
            dev_display = mappings.get(r['device_id'], r['device_id'])

            if not cin or not cout or cin == cout:
                work_duration = "-"
                status = "Absent (Incomplet)"
            else:
                duration = cout - cin
                total_seconds = int(duration.total_seconds())
                hrs = total_seconds // 3600
                mins = (total_seconds % 3600) // 60
                work_duration = f"{hrs}h {mins:02d}m"
                status = "Présent (Conforme)"

            export_data.append({
                "Date": str(r['punch_date']),
                "Matricule": r['badge_or_card_id'],
                "Employé": emp_name,
                "Département": r['department'],
                "Arrivée": cin_str,
                "Départ": cout_str,
                "Heures Travaillées": work_duration,
                "Terminal": dev_display,
                "Statut": status
            })

        df = pd.DataFrame(export_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Rapport Pointage')
        output.seek(0)

        filename = f"Rapport_Pointage_{start_date}_au_{end_date}.xlsx"
        return StreamingResponse(
            output, 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        print(f"Erreur export Excel : {e}")
        return RedirectResponse(url="/reports", status_code=303)


@app.get("/summary", response_class=HTMLResponse)
async def attendance_summary(request: Request, start_date: str = None, end_date: str = None, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    summary_rows = []
    today_str = date.today().isoformat()
    
    if not start_date:
        start_date = today_str
    if not end_date:
        end_date = today_str

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            query = """
                SELECT 
                    COALESCE(e.first_name, 'Inconnu') as first_name,
                    COALESCE(e.last_name, '') as last_name,
                    l.badge_or_card_id,
                    s.start_time as schedule_start,
                    s.end_time as schedule_end,
                    s.late_tolerance_minutes,
                    DATE(l.punch_timestamp) as p_date,
                    MIN(l.punch_timestamp) as check_in,
                    MAX(l.punch_timestamp) as check_out
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id
                LEFT JOIN work_schedules s ON e.schedule_id = s.id
                WHERE DATE(l.punch_timestamp) BETWEEN %s AND %s
                GROUP BY l.badge_or_card_id, DATE(l.punch_timestamp), e.first_name, e.last_name, s.start_time, s.end_time, s.late_tolerance_minutes
            """
            cursor.execute(query, (start_date, end_date))
            raw_logs = cursor.fetchall()
            
            emp_stats = {}
            for row in raw_logs:
                badge = row['badge_or_card_id']
                name = f"{row['first_name']} {row['last_name']}".strip()
                
                if badge not in emp_stats:
                    emp_stats[badge] = {
                        "name": name,
                        "present_days": 0,
                        "late_days": 0,
                        "absent_days": 0,
                        "total_seconds": 0
                    }
                
                cin = row['check_in']
                cout = row['check_out']
                sched_start = row['schedule_start'] or time(8, 0)
                sched_end = row['schedule_end'] or time(17, 0)
                tolerance = row['late_tolerance_minutes'] if row['late_tolerance_minutes'] is not None else 30
                
                if not cin or not cout or cin == cout:
                    emp_stats[badge]["absent_days"] += 1
                else:
                    cin_t = cin.time()
                    cout_t = cout.time()
                    
                    max_arrival_dt = datetime.combine(row['p_date'], sched_start) + timedelta(minutes=tolerance)
                    is_arr_valid = cin <= max_arrival_dt
                    
                    if is_arr_valid:
                        emp_stats[badge]["present_days"] += 1
                    else:
                        emp_stats[badge]["late_days"] += 1
                        emp_stats[badge]["present_days"] += 1
                        
                    duration = cout - cin
                    if duration.total_seconds() > 0:
                        emp_stats[badge]["total_seconds"] += int(duration.total_seconds())

            for badge, data in emp_stats.items():
                total_secs = data["total_seconds"]
                hrs = total_secs // 3600
                mins = (total_secs % 3600) // 60
                formatted_hours = f"{hrs}h {mins:02d}m"

                summary_rows.append({
                    "badge": badge,
                    "employee": data["name"],
                    "present_days": data["present_days"],
                    "late_days": data["late_days"],
                    "absent_days": data["absent_days"],
                    "worked_hours": formatted_hours
                })
                
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la génération du rapport de synthèse : {e}")

    summary_table_rows = ""
    if summary_rows:
        for row in summary_rows:
            summary_table_rows += f"""
                                <tr>
                                    <td class="text-start ps-4 fw-semibold">{row['employee']}</td>
                                    <td><span class="badge bg-success bg-opacity-10 text-success">{row['present_days']}</span></td>
                                    <td><span class="badge bg-danger bg-opacity-10 text-danger">{row['absent_days']}</span></td>
                                    <td><span class="badge bg-warning bg-opacity-20 text-dark">{row['late_days']}</span></td>
                                    <td class="pe-4 fw-bold text-primary">{row['worked_hours']}</td>
                                </tr>
            """
    else:
        summary_table_rows = """
                                <tr>
                                    <td colspan="5" class="text-center py-4 text-muted">Aucune donnée trouvée pour cette période.</td>
                                </tr>
        """

    export_summary_url = f"/summary/export/excel?start_date={start_date}&end_date={end_date}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Synthèse des Présences - Hikvision</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{
                background-color: #f4f7f6;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }}
            .navbar {{
                background: linear-gradient(135deg, #1e293b, #0f172a) !important;
            }}
            .card {{
                border: none;
                border-radius: 12px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            }}
            .card-header {{
                background-color: #ffffff;
                border-bottom: 1px solid #f1f5f9;
                border-top-left-radius: 12px !important;
                border-top-right-radius: 12px !important;
                font-weight: 600;
            }}
            .table th {{
                font-weight: 600;
                color: #475569;
                background-color: #f8fafc !important;
            }}
            @media print {{
                @page {{
                    size: landscape;
                    margin: 5mm;
                }}
                .no-print {{
                    display: none !important;
                }}
                body {{
                    background-color: white !important;
                    font-size: 10pt;
                    width: 100% !important;
                    margin: 0 !important;
                }}
                .container-fluid {{
                    max-width: 100% !important;
                    width: 100% !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }}
                .card {{
                    border: none !important;
                    box-shadow: none !important;
                    width: 100% !important;
                }}
                .card-body {{
                    padding: 0 !important;
                }}
                .table-responsive {{
                    overflow: visible !important;
                    width: 100% !important;
                }}
                .table {{
                    width: 100% !important;
                    max-width: 100% !important;
                }}
            }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3 no-print">
            <div class="container-fluid px-4">
                <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-chart-pie me-2"></i> Synthèse Globale des Présences</span>
                <div class="d-flex align-items-center gap-2">
                    <a href="/" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-house me-1"></i> Tableau de bord</a>
                    <a href="/reports" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-calendar-days me-1"></i> Rapport Journalier</a>
                    <a href="/summary" class="btn btn-light btn-sm px-3 fw-semibold"><i class="fa-solid fa-chart-pie me-1"></i> Synthèse Employés</a>
                    <a href="/logout" class="btn btn-danger btn-sm px-3 ms-2"><i class="fa-solid fa-right-from-bracket me-1"></i> Déconnexion</a>
                </div>
            </div>
        </nav>

        <div class="container-fluid px-4 my-4">
            <div class="card shadow-sm mb-4 no-print">
                <div class="card-header bg-primary text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-filter me-2"></i> Filtrer la période de synthèse</h5>
                </div>
                <div class="card-body p-4">
                    <form method="GET" action="/summary" class="row g-3">
                        <div class="col-md-5">
                            <label class="form-label text-secondary small fw-bold">Date de début</label>
                            <input type="date" class="form-control" name="start_date" value="{start_date}" required>
                        </div>
                        <div class="col-md-5">
                            <label class="form-label text-secondary small fw-bold">Date de fin</label>
                            <input type="date" class="form-control" name="end_date" value="{end_date}" required>
                        </div>
                        <div class="col-md-2 d-flex align-items-end">
                            <button type="submit" class="btn btn-success w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-magnifying-glass me-1"></i> Filtrer</button>
                        </div>
                    </form>
                </div>
            </div>

            <div class="card shadow-sm">
                <div class="card-header d-flex justify-content-between align-items-center py-3">
                    <h5 class="mb-0 fs-6 fw-bold text-dark"><i class="fa-solid fa-clipboard-user me-2 text-primary"></i> Rapport Récapitulatif par Employé ({len(summary_rows)} collaborateurs)</h5>
                    <div class="d-flex gap-2 no-print">
                        <a href="{export_summary_url}" class="btn btn-success btn-sm fw-semibold"><i class="fa-solid fa-file-excel me-1"></i> Exporter Synthèse (.xlsx)</a>
                        <button onclick="window.print();" class="btn btn-outline-dark btn-sm fw-semibold"><i class="fa-solid fa-print me-1"></i> Imprimer / PDF</button>
                    </div>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-striped table-hover mb-0 align-middle text-center">
                            <thead>
                                <tr>
                                    <th class="text-start ps-4">Employé</th>
                                    <th>Jours Présents</th>
                                    <th>Jours Absents</th>
                                    <th>Jours en Retard</th>
                                    <th class="pe-4">Heures Travaillées</th>
                                </tr>
                            </thead>
                            <tbody>
                                {summary_table_rows}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- ROUTE D'EXPORTATION EXCEL DE LA SYNTHÈSE ---
@app.get("/summary/export/excel")
async def export_summary_excel(start_date: str = None, end_date: str = None, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    today_str = date.today().isoformat()
    start_date = start_date or today_str
    end_date = end_date or today_str

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            query = """
                SELECT 
                    COALESCE(e.first_name, 'Inconnu') as first_name,
                    COALESCE(e.last_name, '') as last_name,
                    l.badge_or_card_id,
                    s.start_time as schedule_start,
                    s.end_time as schedule_end,
                    s.late_tolerance_minutes,
                    DATE(l.punch_timestamp) as p_date,
                    MIN(l.punch_timestamp) as check_in,
                    MAX(l.punch_timestamp) as check_out
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id
                LEFT JOIN work_schedules s ON e.schedule_id = s.id
                WHERE DATE(l.punch_timestamp) BETWEEN %s AND %s
                GROUP BY l.badge_or_card_id, DATE(l.punch_timestamp), e.first_name, e.last_name, s.start_time, s.end_time, s.late_tolerance_minutes
            """
            cursor.execute(query, (start_date, end_date))
            raw_logs = cursor.fetchall()
        conn.close()

        emp_stats = {}
        for row in raw_logs:
            badge = row['badge_or_card_id']
            name = f"{row['first_name']} {row['last_name']}".strip()

            if badge not in emp_stats:
                emp_stats[badge] = {
                    "name": name,
                    "present_days": 0,
                    "late_days": 0,
                    "absent_days": 0,
                    "total_seconds": 0
                }

            cin = row['check_in']
            cout = row['check_out']
            sched_start = row['schedule_start'] or time(8, 0)
            tolerance = row['late_tolerance_minutes'] if row['late_tolerance_minutes'] is not None else 30

            if not cin or not cout or cin == cout:
                emp_stats[badge]["absent_days"] += 1
            else:
                max_arr = datetime.combine(row['p_date'], sched_start) + timedelta(minutes=tolerance)
                if cin <= max_arr:
                    emp_stats[badge]["present_days"] += 1
                else:
                    emp_stats[badge]["late_days"] += 1
                    emp_stats[badge]["present_days"] += 1

                duration = cout - cin
                if duration.total_seconds() > 0:
                    emp_stats[badge]["total_seconds"] += int(duration.total_seconds())

        export_summary = []
        for badge, data in emp_stats.items():
            hrs = data["total_seconds"] // 3600
            mins = (data["total_seconds"] % 3600) // 60
            export_summary.append({
                "Matricule": badge,
                "Employé": data["name"],
                "Jours Présents": data["present_days"],
                "Jours Absents": data["absent_days"],
                "Jours en Retard": data["late_days"],
                "Total Heures Travaillées": f"{hrs}h {mins:02d}m"
            })

        df = pd.DataFrame(export_summary)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Synthèse Présences')
        output.seek(0)

        filename = f"Synthese_Presence_{start_date}_au_{end_date}.xlsx"
        return StreamingResponse(
            output, 
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        print(f"Erreur export Synthèse Excel : {e}")
        return RedirectResponse(url="/summary", status_code=303)


@app.post("/employees/add")
async def add_employee(
    badge_or_card_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(None),
    schedule_id: int = Form(None),
    drh_session: str = Cookie(None)
):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO employees (badge_or_card_id, first_name, last_name, department, schedule_id, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    badge_or_card_id.strip(), 
                    first_name.strip(), 
                    last_name.strip(), 
                    department.strip() if department else None,
                    schedule_id,
                    True
                )
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de l'ajout de l'employé : {e}")

    return RedirectResponse(url="/", status_code=303)


@app.get("/employees/delete/{badge_id}")
async def delete_employee(badge_id: str, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM employees WHERE badge_or_card_id = %s", (badge_id,))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la suppression de l'employé : {e}")

    return RedirectResponse(url="/", status_code=303)


@app.get("/employees/edit/{badge_id}", response_class=HTMLResponse)
async def edit_employee_page(badge_id: str, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    employee = None
    schedules = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM employees WHERE badge_or_card_id = %s", (badge_id,))
            employee = cursor.fetchone()
            
            cursor.execute("SELECT * FROM work_schedules ORDER BY id ASC")
            schedules = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la récupération de l'employé : {e}")

    if not employee:
        return RedirectResponse(url="/", status_code=303)

    sched_options_html = ""
    for s in schedules:
        sel = 'selected' if employee.get('schedule_id') == s['id'] else ''
        sched_options_html += f'<option value="{s["id"]}" {sel}>{s["name"]} ({str(s["start_time"])[:5]} - {str(s["end_time"])[:5]})</option>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Modifier un Employé - Hikvision</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{ background-color: #f4f7f6; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
            .navbar {{ background: linear-gradient(135deg, #1e293b, #0f172a) !important; }}
            .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3">
            <div class="container-fluid px-4">
                <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-user-pen me-2"></i> Modification d'un Employé</span>
                <a href="/" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-house me-1"></i> Tableau de bord</a>
            </div>
        </nav>

        <div class="container my-5" style="max-width: 600px;">
            <div class="card shadow-sm">
                <div class="card-header bg-primary text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-user-gear me-2"></i> Modifier les informations de {employee['first_name']} {employee['last_name']}</h5>
                </div>
                <div class="card-body p-4">
                    <form action="/employees/update/{badge_id}" method="POST">
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Matricule / ID Badge</label>
                            <input type="text" class="form-control" name="badge_or_card_id" value="{employee['badge_or_card_id']}" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Prénom</label>
                            <input type="text" class="form-control" name="first_name" value="{employee['first_name']}" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Nom</label>
                            <input type="text" class="form-control" name="last_name" value="{employee['last_name']}" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Département</label>
                            <input type="text" class="form-control" name="department" value="{employee.get('department') or ''}">
                        </div>
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Horaire de travail</label>
                            <select class="form-select" name="schedule_id">
                                {sched_options_html}
                            </select>
                        </div>
                        <div class="mb-3 form-check">
                            <input type="checkbox" class="form-check-input" id="is_active" name="is_active" value="true" {'checked' if employee.get('is_active') else ''}>
                            <label class="form-check-label small fw-bold text-secondary" for="is_active">Employé actif</label>
                        </div>
                        <div class="d-flex gap-2">
                            <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-check me-2"></i> Enregistrer</button>
                            <a href="/" class="btn btn-secondary w-100 py-2 fw-semibold shadow-sm text-center text-decoration-none pt-2">Annuler</a>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/employees/update/{old_badge_id}")
async def update_employee(
    old_badge_id: str,
    badge_or_card_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(None),
    schedule_id: int = Form(None),
    is_active: str = Form(None),
    drh_session: str = Cookie(None)
):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    is_active_bool = True if is_active == "true" else False

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE employees 
                SET badge_or_card_id = %s, first_name = %s, last_name = %s, department = %s, schedule_id = %s, is_active = %s
                WHERE badge_or_card_id = %s
                """,
                (
                    badge_or_card_id.strip(),
                    first_name.strip(),
                    last_name.strip(),
                    department.strip() if department else None,
                    schedule_id,
                    is_active_bool,
                    old_badge_id
                )
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la mise à jour de l'employé : {e}")

    return RedirectResponse(url="/", status_code=303)


@app.post("/api/v1/attendance-webhook")
async def receive_attendance(request: Request):
    try:
        body = await request.body()
        raw_text = body.decode("utf-8", errors="ignore")
        
        print("=== REQUÊTE HIKVISION REÇUE ===")
        print(raw_text)
        print("================================")

        employee_no = "UNKNOWN"
        device_id = None
        punch_type = "IN"

        # 1. Priorité absolue au paramètre d'URL ?dev=... (ex: /api/v1/attendance-webhook?dev=Porte1)
        query_dev = request.query_params.get("dev")
        if query_dev:
            device_id = f"Terminal_{query_dev.strip()}"

        try:
            data = json.loads(raw_text)
            def find_key(obj, key):
                if isinstance(obj, dict):
                    if key in obj and obj[key]:
                        return str(obj[key]).strip()
                    for k, v in obj.items():
                        res = find_key(v, key)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_key(item, key)
                        if res:
                            return res
                return None

            # 2. Extraction dynamique du serialNo ou de l'identifiant du boîtier si ?dev absent
            if not device_id:
                for key_candidate in ["serialNo", "serialNumber", "deviceID", "deviceSerialNo", "macAddress", "ipAddress", "deviceIpAddress"]:
                    val = find_key(data, key_candidate)
                    if val:
                        device_id = val
                        break

            for key_candidate in ["employeeNoString", "employeeNo", "cardNo", "employeeID", "userID"]:
                val = find_key(data, key_candidate)
                if val:
                    employee_no = val
                    break
        except Exception:
            pass

        # Extraction fallback par Regex (JSON malformé ou XML Hikvision)
        if not device_id:
            for dev_pattern in [
                r'"(?:serialNo|serialNumber|deviceID|deviceSerialNo|macAddress)"\s*[:=]\s*["\']?([^"\'<>,\s]+)["\']?',
                r'<(?:serialNo|serialNumber|deviceID|macAddress)>([^<]+)</(?:serialNo|serialNumber|deviceID|macAddress)>'
            ]:
                m_dev = re.search(dev_pattern, raw_text)
                if m_dev:
                    device_id = m_dev.group(1).strip()
                    break

        if employee_no == "UNKNOWN":
            for pattern in [
                r'"(?:employeeNoString|employeeNo|cardNo|employeeID)"\s*[:=]\s*["\']?([^"\'<>,\s]+)["\']?',
                r'<employeeNoString>([^<]+)</employeeNoString>',
                r'<employeeNo>([^<]+)</employeeNo>',
                r'<cardNo>([^<]+)</cardNo>'
            ]:
                match = re.search(pattern, raw_text)
                if match:
                    employee_no = match.group(1).strip()
                    break

        # Filtre strict anti-spam / bots externes
        if employee_no == "UNKNOWN" or not employee_no or len(employee_no) > 20:
            return Response(content="<Response><statusString>IGNORED</statusString></Response>", media_type="application/xml", status_code=200)

        # 3. Fallback ultime sur l'IP source réelle (via proxy Render X-Forwarded-For)
        if not device_id:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                client_host = forwarded.split(",")[0].strip()
            else:
                client_host = request.client.host if request.client else "UNKNOWN_IP"
            device_id = f"IP_{client_host}"

        print(f"-> Pointage validé | Matricule : {employee_no} | Appareil : {device_id}")

        local_punch_time = datetime.now(ZoneInfo("Africa/Kinshasa")).replace(tzinfo=None)

        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Enregistrement automatique du terminal dans devices_mapping s'il est nouveau
            cursor.execute(
                """
                INSERT INTO devices_mapping (device_key, friendly_name)
                VALUES (%s, %s)
                ON CONFLICT (device_key) DO NOTHING
                """,
                (device_id, device_id)
            )
            cursor.execute(
                """
                INSERT INTO attendance_raw_logs (badge_or_card_id, punch_timestamp, device_id, punch_type)
                VALUES (%s, %s, %s, %s)
                """,
                (employee_no, local_punch_time, device_id, punch_type)
            )
            conn.commit()
        conn.close()

        return Response(content="<Response><statusString>OK</statusString></Response>", media_type="application/xml", status_code=200)

    except Exception as e:
        print(f"Erreur critique webhook : {e}")
        return Response(content=f"<Response><statusString>ERROR: {str(e)}</statusString></Response>", media_type="application/xml", status_code=500)