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

app = FastAPI(title="Hikvision Attendance SaaS System")

# Route directe pour servir le logo ABS
@app.get("/static/logo.png")
async def get_logo():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_path = os.path.join(base_dir, "static", "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    return Response(status_code=404)

# Identifiants du Super-Admin global (Propriétaire du SaaS)
SUPERADMIN_USER = "superadmin"
SUPERADMIN_PASS = "masterpassword123"

# Fonction utilitaire pour initialiser les tables SaaS et multi-rôles
def init_database_tables():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 1. Table des entreprises clientes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(150) NOT NULL,
                    slug VARCHAR(100) UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Insérer l'entreprise de démo par défaut (ID 1)
            cursor.execute("""
                INSERT INTO companies (id, name, slug) 
                VALUES (1, 'Entreprise Principale (Démo)', 'demo') 
                ON CONFLICT (id) DO NOTHING
            """)

            # 2. Table des utilisateurs par entreprise (Admin Général vs Superviseur)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS company_users (
                    id SERIAL PRIMARY KEY,
                    company_id INT REFERENCES companies(id) ON DELETE CASCADE,
                    username VARCHAR(100) NOT NULL,
                    password VARCHAR(100) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'admin', -- 'admin' ou 'supervisor'
                    UNIQUE (company_id, username)
                )
            """)

            # Comptes par défaut pour l'entreprise 1
            cursor.execute("""
                INSERT INTO company_users (company_id, username, password, role)
                VALUES 
                (1, 'admin', 'admin123', 'admin'),
                (1, 'superviseur', 'super123', 'supervisor')
                ON CONFLICT (company_id, username) DO NOTHING
            """)

            # 3. Table de renommage des appareils + company_id
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS devices_mapping (
                    device_key VARCHAR(100) PRIMARY KEY,
                    friendly_name VARCHAR(100) NOT NULL,
                    company_id INT REFERENCES companies(id) ON DELETE CASCADE
                )
            """)

            # 4. Table des horaires de travail + company_id
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
            
            # 5. Table de liaison pour les horaires multiples par travailleur
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employee_schedules (
                    badge_or_card_id VARCHAR(100) NOT NULL,
                    schedule_id INT NOT NULL REFERENCES work_schedules(id) ON DELETE CASCADE,
                    PRIMARY KEY (badge_or_card_id, schedule_id)
                )
            """)

            # 6. Table employees + company_id
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    badge_or_card_id VARCHAR(100) PRIMARY KEY,
                    company_id INT REFERENCES companies(id) ON DELETE CASCADE,
                    first_name VARCHAR(100),
                    last_name VARCHAR(100),
                    department VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE
                )
            """)

            # 7. Table attendance_raw_logs + company_id
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS attendance_raw_logs (
                    id SERIAL PRIMARY KEY,
                    company_id INT REFERENCES companies(id) ON DELETE CASCADE,
                    badge_or_card_id VARCHAR(100),
                    punch_timestamp TIMESTAMP,
                    device_id VARCHAR(100),
                    punch_type VARCHAR(20) DEFAULT 'IN'
                )
            """)

            # Horaires par défaut pour l'entreprise 1
            cursor.execute("SELECT COUNT(*) as cnt FROM work_schedules WHERE company_id = 1")
            res = cursor.fetchone()
            if res and res['cnt'] == 0:
                cursor.execute("""
                    INSERT INTO work_schedules (company_id, name, start_time, end_time, late_tolerance_minutes)
                    VALUES 
                    (1, 'Standard (08:00 - 17:00)', '08:00:00', '17:00:00', 30),
                    (1, 'Vacation Matin (06:00 - 14:00)', '06:00:00', '14:00:00', 15),
                    (1, 'Vacation Soir (14:00 - 22:00)', '14:00:00', '22:00:00', 15),
                    (1, 'Shift Nuit (22:00 - 06:00)', '22:00:00', '06:00:00', 20)
                """)

            conn.commit()
        conn.close()
        print("Base de données SaaS initialisée avec succès !")
    except Exception as e:
        print(f"Erreur initialisation tables BDD : {e}")

@app.on_event("startup")
async def startup_event():
    init_database_tables()

def get_best_matching_schedule(check_in_dt, assigned_schedules, all_schedules):
    candidates = assigned_schedules if assigned_schedules else all_schedules
    if not candidates:
        return {
            "name": "Standard",
            "start_time": time(8, 0),
            "end_time": time(17, 0),
            "late_tolerance_minutes": 30
        }

    cin_time = check_in_dt.time() if isinstance(check_in_dt, datetime) else check_in_dt
    cin_minutes = cin_time.hour * 60 + cin_time.minute

    best_sched = candidates[0]
    min_diff = float('inf')

    for s in candidates:
        st = s['start_time']
        st_minutes = st.hour * 60 + st.minute
        diff = abs(cin_minutes - st_minutes)
        diff = min(diff, 1440 - diff)
        if diff < min_diff:
            min_diff = diff
            best_sched = s

    return best_sched


# --- PAGE DE CONNEXION COMMUNE ---
@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = None):
    error_html = f'<div class="alert alert-danger py-2 mb-3 text-center small">{error}</div>' if error else ''
    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Connexion SaaS - Hikvision</title>
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
                <img src="/static/logo.png" alt="Logo ABS" class="mb-3" onerror="this.style.display='none'">
                <h4 class="fw-bold text-dark">Portail SaaS</h4>
                <p class="text-muted small">Connexion Super-Admin / Entreprise</p>
            </div>
            {error_html}
            <form action="/login" method="POST">
                <div class="mb-3">
                    <label class="form-label text-secondary small fw-bold">Nom d'utilisateur</label>
                    <div class="input-group">
                        <span class="input-group-text bg-light"><i class="fa-solid fa-user text-muted"></i></span>
                        <input type="text" class="form-control" name="username" placeholder="ex: superadmin ou admin" required>
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
    if username == SUPERADMIN_USER and password == SUPERADMIN_PASS:
        response = RedirectResponse(url="/superadmin", status_code=303)
        response.set_cookie(key="saas_session", value="superadmin", httponly=True)
        return response

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM company_users WHERE username = %s AND password = %s", (username.strip(), password.strip()))
            user = cursor.fetchone()
        conn.close()

        if user:
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(key="saas_session", value="company_user", httponly=True)
            response.set_cookie(key="company_id", value=str(user['company_id']), httponly=True)
            response.set_cookie(key="user_role", value=user['role'], httponly=True)
            return response
    except Exception as e:
        print(f"Erreur login : {e}")

    return RedirectResponse(url="/login?error=Identifiants+incorrects", status_code=303)

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="saas_session")
    response.delete_cookie(key="company_id")
    response.delete_cookie(key="user_role")
    return response


# --- 1. PORTAIL SUPER-ADMIN ---
@app.get("/superadmin", response_class=HTMLResponse)
async def superadmin_dashboard(request: Request, error: str = None, success: str = None, saas_session: str = Cookie(None)):
    if saas_session != "superadmin":
        return RedirectResponse(url="/login", status_code=303)

    companies = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT c.*, 
                (SELECT COUNT(*) FROM employees e WHERE e.company_id = c.id) as emp_count
                FROM companies c ORDER BY c.id ASC
            """)
            companies = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Erreur superadmin : {e}")

    alert_html = ""
    if error:
        alert_html = f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="fa-solid fa-circle-exclamation me-2"></i>{error}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'
    elif success:
        alert_html = f'<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="fa-solid fa-circle-check me-2"></i>{success}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'

    comp_rows_html = ""
    for c in companies:
        status_badge = '<span class="badge bg-success">Actif</span>' if c['is_active'] else '<span class="badge bg-danger">Suspendu</span>'
        comp_rows_html += f"""
            <tr>
                <td class="ps-3"><code>{c['id']}</code></td>
                <td class="fw-semibold">{c['name']}</td>
                <td><code>?company={c['slug']}</code></td>
                <td><span class="badge bg-info text-dark">{c['emp_count']} employé(s)</span></td>
                <td>{status_badge}</td>
                <td class="text-end pe-3">
                    <a href="/superadmin/login-as/{c['id']}" class="btn btn-sm btn-outline-primary me-1" title="Gérer"><i class="fa-solid fa-right-to-bracket"></i> Gérer</a>
                    <a href="/superadmin/company/delete/{c['id']}" class="btn btn-sm btn-outline-danger" onclick="return confirm('Supprimer cette entreprise ?');"><i class="fa-solid fa-trash"></i></a>
                </td>
            </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Portail Super-Admin</title>
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
                <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-crown me-2 text-warning"></i> Portail Super-Admin (SaaS)</span>
                <a href="/logout" class="btn btn-danger btn-sm px-3"><i class="fa-solid fa-right-from-bracket me-1"></i> Déconnexion</a>
            </div>
        </nav>

        <div class="container my-4" style="max-width: 1050px;">
            {alert_html}
            <div class="row g-4">
                <div class="col-md-5">
                    <div class="card">
                        <div class="card-header bg-primary text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-building-circle-plus me-2"></i> Créer une Entreprise</h5>
                        </div>
                        <div class="card-body p-4">
                            <form action="/superadmin/company/add" method="POST">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Nom de l'entreprise</label>
                                    <input type="text" class="form-control" name="name" placeholder="ex: CIIG" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Slug Webhook</label>
                                    <input type="text" class="form-control" name="slug" placeholder="ex: ciig" required>
                                </div>
                                <hr class="my-3">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Admin Général (Username)</label>
                                    <input type="text" class="form-control" name="admin_user" placeholder="ex: admin" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Mot de passe Admin Général</label>
                                    <input type="password" class="form-control" name="admin_pass" placeholder="••••••••" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Mot de passe Superviseur</label>
                                    <input type="password" class="form-control" name="super_pass" placeholder="••••••••" required>
                                </div>
                                <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-check me-2"></i> Créer</button>
                            </form>
                        </div>
                    </div>
                </div>
                <div class="col-md-7">
                    <div class="card">
                        <div class="card-header bg-dark text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-list me-2"></i> Entreprises Clientes ({len(companies)})</h5>
                        </div>
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th class="ps-3">ID</th>
                                            <th>Nom</th>
                                            <th>Slug</th>
                                            <th>Employés</th>
                                            <th>Statut</th>
                                            <th class="text-end pe-3">Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {comp_rows_html}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/superadmin/company/add")
async def superadmin_add_company(name: str = Form(...), slug: str = Form(...), admin_user: str = Form(...), admin_pass: str = Form(...), super_pass: str = Form(...), saas_session: str = Cookie(None)):
    if saas_session != "superadmin":
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id", (name.strip(), slug.strip().lower()))
            comp_id = cursor.fetchone()['id']

            cursor.execute("INSERT INTO company_users (company_id, username, password, role) VALUES (%s, %s, %s, 'admin')", (comp_id, admin_user.strip(), admin_pass.strip()))
            cursor.execute("INSERT INTO company_users (company_id, username, password, role) VALUES (%s, %s, %s, 'supervisor')", (comp_id, "superviseur", super_pass.strip()))

            conn.commit()
        conn.close()
        return RedirectResponse(url="/superadmin?success=Entreprise+créée+avec+succès", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/superadmin?error=Erreur:+{str(e)}", status_code=303)

@app.get("/superadmin/login-as/{comp_id}")
async def superadmin_login_as(comp_id: int, saas_session: str = Cookie(None)):
    if saas_session != "superadmin":
        return RedirectResponse(url="/login", status_code=303)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="saas_session", value="company_user", httponly=True)
    response.set_cookie(key="company_id", value=str(comp_id), httponly=True)
    response.set_cookie(key="user_role", value="admin", httponly=True)
    return response

@app.get("/superadmin/company/delete/{comp_id}")
async def superadmin_delete_company(comp_id: int, saas_session: str = Cookie(None)):
    if saas_session != "superadmin":
        return RedirectResponse(url="/login", status_code=303)
    if comp_id == 1:
        return RedirectResponse(url="/superadmin?error=Impossible+de+supprimer+la+démo", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM companies WHERE id = %s", (comp_id,))
            conn.commit()
        conn.close()
        return RedirectResponse(url="/superadmin?success=Entreprise+supprimée", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/superadmin?error=Erreur:+{str(e)}", status_code=303)


# --- 2. TABLEAU DE BORD CLIENT (ADMIN vs SUPERVISEUR) ---
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, error: str = None, success: str = None, saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user":
        return RedirectResponse(url="/login", status_code=303)

    employees = []
    devices = []
    schedules = []
    emp_schedules_map = {}
    current_company_name = "Entreprise"
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM companies WHERE id = %s", (company_id,))
            comp_res = cursor.fetchone()
            if comp_res:
                current_company_name = comp_res['name']

            cursor.execute("""
                SELECT e.*
                FROM employees e 
                WHERE e.company_id = %s
                ORDER BY COALESCE(NULLIF(TRIM(e.department), ''), 'Non assigné') ASC, e.first_name ASC, e.last_name ASC
            """, (company_id,))
            employees = cursor.fetchall()
                
            cursor.execute("SELECT * FROM work_schedules WHERE company_id = %s ORDER BY id ASC", (company_id,))
            schedules = cursor.fetchall()
                
            cursor.execute("""
                SELECT es.badge_or_card_id, s.name, s.start_time, s.end_time
                FROM employee_schedules es
                JOIN work_schedules s ON es.schedule_id = s.id
                WHERE s.company_id = %s
            """, (company_id,))
            for row in cursor.fetchall():
                b_id = row['badge_or_card_id']
                if b_id not in emp_schedules_map:
                    emp_schedules_map[b_id] = []
                emp_schedules_map[b_id].append(f"{row['name']} ({str(row['start_time'])[:5]}-{str(row['end_time'])[:5]})")

            cursor.execute("SELECT DISTINCT device_id FROM attendance_raw_logs WHERE device_id IS NOT NULL AND company_id = %s", (company_id,))
            raw_devs = [row['device_id'] for row in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM devices_mapping WHERE company_id = %s", (company_id,))
            mappings = {row['device_key']: row['friendly_name'] for row in cursor.fetchall()}
            
            for dev in set(raw_devs).union(set(mappings.keys())):
                devices.append({"key": dev, "name": mappings.get(dev, dev)})
                
        conn.close()
    except Exception as e:
        print(f"Erreur dashboard : {e}")

    is_supervisor = (user_role == "supervisor")
    role_badge = '<span class="badge bg-warning text-dark px-3 py-2">Superviseur (Lecture seule)</span>' if is_supervisor else '<span class="badge bg-primary px-3 py-2">Admin Général</span>'
    alert_html = f'<div class="alert alert-danger">{error}</div>' if error else (f'<div class="alert alert-success">{success}</div>' if success else '')

    import_section = "" if is_supervisor else """
    <div class="card shadow-sm mb-4">
        <div class="card-header bg-dark text-white py-3">
            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-file-excel me-2 text-success"></i> Importation de la liste des Employés (.xlsx / .csv)</h5>
        </div>
        <div class="card-body p-4">
            <form action="/employees/import" method="POST" enctype="multipart/form-data" class="row g-3 align-items-center">
                <div class="col-md-8">
                    <input type="file" class="form-control" name="file" accept=".xlsx, .xls, .csv" required>
                </div>
                <div class="col-md-4">
                    <button type="submit" class="btn btn-success w-100 py-2 fw-semibold"><i class="fa-solid fa-file-import me-2"></i> Importer</button>
                </div>
            </form>
        </div>
    </div>
    """

    schedules_mgmt_html = "" if is_supervisor else """
    <div class="row g-4 mb-4">
        <div class="col-lg-5">
            <div class="card h-100">
                <div class="card-header bg-success text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-clock me-2"></i> Ajouter un Horaire / Shift</h5>
                </div>
                <div class="card-body p-4">
                    <form action="/schedules/add" method="POST">
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Nom</label>
                            <input type="text" class="form-control" name="name" placeholder="ex: Shift Matin" required>
                        </div>
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label text-secondary small fw-bold">Entrée</label>
                                <input type="time" class="form-control" name="start_time" value="08:00" required>
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label text-secondary small fw-bold">Sortie</label>
                                <input type="time" class="form-control" name="end_time" value="17:00" required>
                            </div>
                        </div>
                        <div class="mb-3">
                            <label class="form-label text-secondary small fw-bold">Tolérance retard (min)</label>
                            <input type="number" class="form-control" name="late_tolerance_minutes" value="30" required>
                        </div>
                        <button type="submit" class="btn btn-success w-100 py-2 fw-semibold">Ajouter</button>
                    </form>
                </div>
            </div>
        </div>
        <div class="col-lg-7">
            <div class="card h-100">
                <div class="card-header bg-dark text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-business-time me-2"></i> Catalogue des Horaires</h5>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle mb-0">
                            <thead><tr><th class="ps-3">Nom</th><th>Entrée</th><th>Sortie</th><th>Tolérance</th><th class="text-end pe-3">Action</th></tr></thead>
                            <tbody>{sched_rows_html}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    sched_rows_html = "".join([f"<tr><td class='ps-3 fw-semibold'>{s['name']}</td><td>{str(s['start_time'])[:5]}</td><td>{str(s['end_time'])[:5]}</td><td>{s['late_tolerance_minutes']}m</td><td class='text-end pe-3'><a href='/schedules/delete/{s['id']}' class='btn btn-sm btn-outline-danger' onclick='return confirm(\"Supprimer?\");'><i class='fa-solid fa-trash'></i></a></td></tr>" for s in schedules])
    sched_checkboxes_html = "".join([f"<div class='form-check mb-1'><input class='form-check-input' type='checkbox' name='schedule_ids' value='{s['id']}' id='sc_{s['id']}'><label class='form-check-label small' for='sc_{s['id']}'><strong>{s['name']}</strong> ({str(s['start_time'])[:5]}-{str(s['end_time'])[:5]})</label></div>" for s in schedules])

    dev_rows_html = ""
    for d in devices:
        rename_form = d['key'] if is_supervisor else f"<form action='/devices/rename' method='POST' class='d-flex gap-2'><input type='hidden' name='device_key' value='{d['key']}'><input type='text' class='form-control form-control-sm' name='friendly_name' value='{d['name']}' required><button type='submit' class='btn btn-sm btn-success text-nowrap'>Enregistrer</button></form>"
        del_btn = "" if is_supervisor else f"<td class='text-end pe-3'><a href='/devices/delete/{d['key']}' class='btn btn-sm btn-outline-danger'><i class='fa-solid fa-trash'></i></a></td>"
        dev_rows_html += f"<tr><td class='ps-3'><code>{d['key']}</code></td><td>{rename_form}</td>{del_btn}</tr>"

    devices_mgmt_html = f"""
    <div class="card shadow-sm mb-4">
        <div class="card-header bg-secondary text-white py-3">
            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-network-wired me-2"></i> Gestion des Terminaux de Pointage</h5>
        </div>
        {"<!-- Masqué superviseur -->" if is_supervisor else '''
        <div class="card-body bg-light border-bottom p-3">
            <form action="/devices/add" method="POST" class="row g-2 align-items-center">
                <div class="col-md-5"><input type="text" class="form-control form-control-sm" name="device_key" placeholder="ID Technique / N° de série" required></div>
                <div class="col-md-5"><input type="text" class="form-control form-control-sm" name="friendly_name" placeholder="Nom personnalisé (ex: Porte Entrée)" required></div>
                <div class="col-md-2"><button type="submit" class="btn btn-sm btn-primary w-100 fw-semibold">Ajouter</button></div>
            </form>
        </div>
        '''}
        <div class="card-body p-0">
            <div class="table-responsive">
                <table class="table table-hover align-middle mb-0">
                    <thead><tr><th class="ps-3">ID Technique</th><th>Nom Personnalisé</th><th class="text-end pe-3">Action</th></tr></thead>
                    <tbody>{dev_rows_html if dev_rows_html else '<tr><td colspan="3" class="text-center py-3 text-muted">Aucun terminal détecté.</td></tr>'}</tbody>
                </table>
            </div>
        </div>
    </div>
    """

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
                b_id = emp['badge_or_card_id']
                is_act = emp.get('is_active', True)
                status_badge = '<span class="badge bg-success bg-opacity-10 text-success">Actif</span>' if is_act else '<span class="badge bg-danger bg-opacity-10 text-danger">Inactif</span>'
                full_name = f"{emp['first_name']} {emp['last_name']}".strip()
                search_text = f"{full_name} {b_id} {dept_label}".lower()
                
                assigned_scheds = emp_schedules_map.get(b_id, [])
                if assigned_scheds:
                    sched_badges = "".join([f'<span class="badge bg-light text-dark border me-1 mb-1 d-inline-block">{s}</span>' for s in assigned_scheds])
                else:
                    sched_badges = '<span class="badge bg-secondary bg-opacity-10 text-secondary">Horaires par défaut</span>'

                actions_td = "" if is_supervisor else f"""
                    <td class="pe-3 text-end">
                        <a href="/employees/edit/{b_id}" class="btn btn-sm btn-outline-primary me-1" title="Modifier"><i class="fa-solid fa-pen-to-square"></i></a>
                        <a href="/employees/delete/{b_id}" class="btn btn-sm btn-outline-danger" title="Supprimer" onclick="return confirm('Voulez-vous vraiment supprimer cet employé ?');"><i class="fa-solid fa-trash"></i></a>
                    </td>
                """

                emp_rows_html += f"""
                    <tr class="emp-data-row" data-dept="{dept_label}" data-search="{search_text}">
                        <td class="ps-3"><code>{b_id}</code></td>
                        <td class="fw-semibold">{full_name}</td>
                        <td><span class="badge bg-secondary bg-opacity-10 text-secondary">{dept_label}</span></td>
                        <td>{sched_badges}</td>
                        <td>{status_badge}</td>
                        {actions_td}
                    </tr>
                """

    add_emp_card = "" if is_supervisor else f"""
    <div class="col-lg-4">
        <div class="card">
            <div class="card-header bg-primary text-white py-3"><h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-user-plus me-2"></i> Nouvel Employé</h5></div>
            <div class="card-body p-4">
                <form action="/employees/add" method="POST">
                    <div class="mb-3"><label class="form-label text-secondary small fw-bold">Matricule</label><input type="text" class="form-control" name="badge_or_card_id" required></div>
                    <div class="mb-3"><label class="form-label text-secondary small fw-bold">Prénom</label><input type="text" class="form-control" name="first_name" required></div>
                    <div class="mb-3"><label class="form-label text-secondary small fw-bold">Nom</label><input type="text" class="form-control" name="last_name" required></div>
                    <div class="mb-3"><label class="form-label text-secondary small fw-bold">Département</label><input type="text" class="form-control" name="department"></div>
                    <div class="mb-3"><label class="form-label text-secondary small fw-bold">Shifts</label><div class="schedule-box">{sched_checkboxes_html}</div></div>
                    <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold">Enregistrer</button>
                </form>
            </div>
        </div>
    </div>
    """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <title>Tableau de bord - {current_company_name}</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{ background-color: #f4f7f6; font-family: 'Segoe UI', sans-serif; }} 
            .navbar {{ background: linear-gradient(135deg, #1e293b, #0f172a) !important; }} 
            .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
            .schedule-box {{ max-height: 140px; overflow-y: auto; border: 1px solid #dee2e6; border-radius: 6px; padding: 8px; background-color: #fafafa; }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3">
            <div class="container-fluid px-4">
                <div class="d-flex align-items-center gap-3"><span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-building me-2"></i> {current_company_name}</span>{role_badge}</div>
                <div class="d-flex align-items-center gap-2">
                    <a href="/" class="btn btn-outline-light btn-sm px-3 active"><i class="fa-solid fa-house me-1"></i> Accueil</a>
                    <a href="/reports" class="btn btn-outline-light btn-sm px-3"><i class="fa-solid fa-calendar-days me-1"></i> Rapports</a>
                    <a href="/summary" class="btn btn-light btn-sm px-3 fw-semibold"><i class="fa-solid fa-chart-pie me-1"></i> Synthèse</a>
                    <a href="/logout" class="btn btn-danger btn-sm px-3 ms-2"><i class="fa-solid fa-right-from-bracket me-1"></i> Déconnexion</a>
                </div>
            </div>
        </nav>
        <div class="container-fluid px-4 my-4">
            {alert_html}
            {import_section}
            {schedules_mgmt_html.replace("{sched_rows_html}", sched_rows_html)}
            {devices_mgmt_html}
            <div class="row g-4">
                {add_emp_card}
                <div class="{'col-lg-8' if not is_supervisor else 'col-lg-12'}">
                    <div class="card">
                        <div class="card-header bg-primary text-white py-3 d-flex justify-content-between align-items-center">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-users me-2"></i> Liste des Employés ({len(employees)})</h5>
                        </div>
                        <div class="card-body bg-light border-bottom py-2 px-3">
                            <div class="row g-2 align-items-center">
                                <div class="col-md-6">
                                    <div class="input-group input-group-sm">
                                        <span class="input-group-text bg-white"><i class="fa-solid fa-magnifying-glass text-muted"></i></span>
                                        <input type="text" id="empSearchInput" class="form-control" placeholder="Filtrer par nom, prénom ou matricule...">
                                        <button class="btn btn-outline-secondary" type="button" id="clearEmpSearch"><i class="fa-solid fa-xmark"></i></button>
                                    </div>
                                </div>
                                <div class="col-md-4">
                                    <select id="empDeptFilter" class="form-select form-select-sm">{dept_options_html}</select>
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
                                            <th class="ps-3">Matricule</th>
                                            <th>Nom & Prénom</th>
                                            <th>Département</th>
                                            <th>Horaires</th>
                                            <th>Statut</th>
                                            {"" if is_supervisor else '<th class="text-end pe-3">Actions</th>'}
                                        </tr>
                                    </thead>
                                    <tbody id="employeesTableBody">
                                        {emp_rows_html if emp_rows_html else '<tr><td colspan="6" class="text-center py-4 text-muted">Aucun employé.</td></tr>'}
                                        <tr id="noEmpSearchResult" style="display: none;"><td colspan="6" class="text-center py-4 text-muted">Aucun employé ne correspond.</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <script>
            document.addEventListener("DOMContentLoaded", function() {{
                const searchInput = document.getElementById("empSearchInput");
                const clearSearchBtn = document.getElementById("clearEmpSearch");
                const deptFilter = document.getElementById("empDeptFilter");
                const allEmpRows = Array.from(document.querySelectorAll(".emp-data-row"));
                const allDeptHeaders = Array.from(document.querySelectorAll(".dept-group-header"));
                const noResultRow = document.getElementById("noEmpSearchResult");
                const filterCounter = document.getElementById("empFilterCounter");

                function filterTable() {{
                    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
                    const selectedDept = deptFilter ? deptFilter.value : "ALL";
                    let matchCount = 0;
                    const visibleDepts = new Set();

                    allEmpRows.forEach(row => {{
                        const rowDept = row.getAttribute("data-dept");
                        const rowSearch = row.getAttribute("data-search") || "";
                        const matchDept = (selectedDept === "ALL" || rowDept === selectedDept);
                        const matchQuery = (!query || rowSearch.includes(query));
                        if (matchDept && matchQuery) {{
                            row.style.display = "";
                            matchCount++;
                            visibleDepts.add(rowDept);
                        }} else {{
                            row.style.display = "none";
                        }}
                    }});

                    allDeptHeaders.forEach(header => {{
                        const hDept = header.getAttribute("data-dept");
                        header.style.display = visibleDepts.has(hDept) ? "" : "none";
                    }});

                    if (noResultRow) noResultRow.style.display = (allEmpRows.length > 0 && matchCount === 0) ? "" : "none";
                    if (filterCounter) filterCounter.textContent = matchCount + " employé(s)";
                }}

                if (searchInput) searchInput.addEventListener("input", filterTable);
                if (clearSearchBtn) clearSearchBtn.addEventListener("click", () => {{ searchInput.value = ""; filterTable(); }});
                if (deptFilter) deptFilter.addEventListener("change", filterTable);
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- ROUTES GESTION TERMINAUX ET HORAIRES ---
@app.post("/devices/add")
async def add_device_manual(device_key: str = Form(...), friendly_name: str = Form(...), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/?error=Accès+refusé", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO devices_mapping (device_key, friendly_name, company_id) VALUES (%s, %s, %s) ON CONFLICT (device_key) DO UPDATE SET friendly_name = EXCLUDED.friendly_name, company_id = EXCLUDED.company_id", (device_key.strip(), friendly_name.strip(), company_id))
            conn.commit()
        conn.close()
        return RedirectResponse(url="/?success=Terminal+ajouté", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error=Erreur:+{str(e)}", status_code=303)

@app.post("/devices/rename")
async def rename_device(device_key: str = Form(...), friendly_name: str = Form(...), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO devices_mapping (device_key, friendly_name, company_id) VALUES (%s, %s, %s) ON CONFLICT (device_key) DO UPDATE SET friendly_name = EXCLUDED.friendly_name, company_id = EXCLUDED.company_id", (device_key.strip(), friendly_name.strip(), company_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/devices/delete/{device_key}")
async def delete_device(device_key: str, saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM devices_mapping WHERE device_key = %s AND company_id = %s", (device_key, company_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.post("/schedules/add")
async def add_schedule(name: str = Form(...), start_time: str = Form(...), end_time: str = Form(...), late_tolerance_minutes: int = Form(30), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO work_schedules (company_id, name, start_time, end_time, late_tolerance_minutes) VALUES (%s, %s, %s, %s, %s)", (company_id, name.strip(), start_time, end_time, late_tolerance_minutes))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/schedules/delete/{sched_id}")
async def delete_schedule(sched_id: int, saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM employee_schedules WHERE schedule_id = %s", (sched_id,))
            cursor.execute("DELETE FROM work_schedules WHERE id = %s AND company_id = %s", (sched_id, company_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur : {e}")
    return RedirectResponse(url="/", status_code=303)


# --- GESTION DES EMPLOYÉS & IMPORT ---
@app.post("/employees/add")
async def add_employee(request: Request, badge_or_card_id: str = Form(...), first_name: str = Form(...), last_name: str = Form(...), department: str = Form(None), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    form_data = await request.form()
    schedule_ids = form_data.getlist("schedule_ids")
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO employees (badge_or_card_id, first_name, last_name, department, company_id, is_active) VALUES (%s, %s, %s, %s, %s, TRUE)", (badge_or_card_id.strip(), first_name.strip(), last_name.strip(), department.strip() if department else None, company_id))
            for s_id in schedule_ids:
                cursor.execute("INSERT INTO employee_schedules (badge_or_card_id, schedule_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (badge_or_card_id.strip(), int(s_id)))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/employees/delete/{badge_id}")
async def delete_employee(badge_id: str, saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM employee_schedules WHERE badge_or_card_id = %s", (badge_id,))
            cursor.execute("DELETE FROM employees WHERE badge_or_card_id = %s AND company_id = %s", (badge_id, company_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.post("/employees/import")
async def import_employees(file: UploadFile = File(...), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/", status_code=303)
    try:
        contents = await file.read()
        filename = file.filename.lower()
        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            return RedirectResponse(url="/?error=Format+non+supporté", status_code=303)

        df.columns = [str(col).strip().lower() for col in df.columns]
        col_badge = next((c for c in df.columns if c in ['badge', 'matricule', 'id', 'cardno']), None)
        col_prenom = next((c for c in df.columns if c in ['prenom', 'first_name']), None)
        col_nom = next((c for c in df.columns if c in ['nom', 'last_name']), None)
        col_dept = next((c for c in df.columns if c in ['departement', 'department']), None)

        if not col_badge or not col_nom:
            return RedirectResponse(url="/?error=Colonnes+manquantes+dans+le+fichier", status_code=303)

        imported = 0
        conn = get_db_connection()
        with conn.cursor() as cursor:
            for _, row in df.iterrows():
                b_val = str(row[col_badge]).strip() if pd.notna(row[col_badge]) else ""
                if not b_val or b_val.lower() == 'nan': continue
                cursor.execute("""
                    INSERT INTO employees (badge_or_card_id, first_name, last_name, department, company_id, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (badge_or_card_id) DO UPDATE SET first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name, department=EXCLUDED.department, company_id=EXCLUDED.company_id
                """, (b_val, str(row[col_prenom]).strip() if col_prenom and pd.notna(row[col_prenom]) else "", str(row[col_nom]).strip(), str(row[col_dept]).strip() if col_dept and pd.notna(row[col_dept]) else None, company_id))
                imported += 1
            conn.commit()
        conn.close()
        return RedirectResponse(url=f"/?success={imported}+employés+importés", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error=Erreur+import:+{str(e)}", status_code=303)


# --- RAPPORTS ET SYNTHÈSE DÉTAILLÉS ---
@app.get("/reports", response_class=HTMLResponse)
async def attendance_reports(request: Request, start_date: str = None, end_date: str = None, device_id: str = None, saas_session: str = Cookie(None), company_id: str = Cookie("1")):
    if saas_session != "company_user":
        return RedirectResponse(url="/login", status_code=303)

    reports = []
    devices = []
    today_str = date.today().isoformat()
    start_date = start_date or today_str
    end_date = end_date or today_str

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM devices_mapping WHERE company_id = %s", (company_id,))
            mappings = {row['device_key']: row['friendly_name'] for row in cursor.fetchall()}

            cursor.execute("SELECT DISTINCT device_id FROM attendance_raw_logs WHERE device_id IS NOT NULL AND company_id = %s", (company_id,))
            devices = [{"key": row['device_id'], "name": mappings.get(row['device_id'], row['device_id'])} for row in cursor.fetchall()]

            cursor.execute("SELECT * FROM work_schedules WHERE company_id = %s ORDER BY id ASC", (company_id,))
            all_schedules = cursor.fetchall()

            cursor.execute("SELECT es.badge_or_card_id, s.* FROM employee_schedules es JOIN work_schedules s ON es.schedule_id = s.id WHERE s.company_id = %s", (company_id,))
            emp_schedules_map = {}
            for row in cursor.fetchall():
                emp_schedules_map.setdefault(row['badge_or_card_id'], []).append(row)

            base_query = """
                SELECT l.id, l.badge_or_card_id, COALESCE(e.first_name, 'Inconnu') as first_name, COALESCE(e.last_name, '') as last_name, DATE(l.punch_timestamp) as punch_date, l.punch_timestamp as exact_punch, l.device_id
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id AND e.company_id = l.company_id
                WHERE l.company_id = %s AND DATE(l.punch_timestamp) BETWEEN %s AND %s
            """
            params = [company_id, start_date, end_date]
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
                grouped[key] = {"badge_or_card_id": r['badge_or_card_id'], "first_name": r['first_name'], "last_name": r['last_name'], "punch_date": r['punch_date'], "device_id": r['device_id'], "timestamps": []}
            grouped[key]["timestamps"].append((r['id'], r['exact_punch']))

        for key, data in grouped.items():
            sorted_ts = sorted(data["timestamps"], key=lambda x: x[1])
            cin_item, cout_item = sorted_ts[0], (sorted_ts[-1] if len(sorted_ts) > 1 else None)
            assigned_for_emp = emp_schedules_map.get(data["badge_or_card_id"], [])
            best_sched = get_best_matching_schedule(cin_item[1], assigned_for_emp, all_schedules)

            reports.append({
                "badge_or_card_id": data["badge_or_card_id"], "first_name": data["first_name"], "last_name": data["last_name"],
                "schedule_name": best_sched.get('name', 'Standard'), "schedule_start": best_sched['start_time'], "schedule_end": best_sched['end_time'], "late_tolerance_minutes": best_sched.get('late_tolerance_minutes', 30),
                "punch_date": data["punch_date"], "check_in_id": cin_item[0], "check_in": cin_item[1], "check_out_id": cout_item[0] if cout_item else None, "check_out": cout_item[1] if cout_item else None, "device_id": data["device_id"]
            })
    except Exception as e:
        print(f"Erreur rapports : {e}")

    report_rows_html = ""
    for row in reports:
        emp_name = f"{row['first_name']} {row['last_name']}".strip()
        cin, cout = row['check_in'], row['check_out']
        cin_str, cout_str = (str(cin).split(' ')[1] if cin else '-'), (str(cout).split(' ')[1] if cout else '-')
        dev_display = mappings.get(row['device_id'], row['device_id']) if row['device_id'] in mappings else row['device_id']
        del_id = row['check_out_id'] if row['check_out_id'] else row['check_in_id']

        if not cin or not cout or cin == cout:
            report_rows_html += f"<tr><td><strong>{row['punch_date']}</strong></td><td><code>{row['badge_or_card_id']}</code></td><td class='text-start ps-4 fw-semibold'>{emp_name}</td><td>{row['schedule_name']}</td><td><span class='badge bg-danger'>Incomplet ({cin_str})</span></td><td>-</td><td>-</td><td>{dev_display}</td><td><span class='badge bg-danger'>Absent</span></td><td><a href='/reports/delete/{del_id}' class='btn btn-sm btn-outline-danger'><i class='fa-solid fa-trash'></i></a></td></tr>"
        else:
            duration = cout - cin
            hrs, mins = int(duration.total_seconds()) // 3600, (int(duration.total_seconds()) % 3600) // 60
            max_arr = datetime.combine(date.today(), row['schedule_start']) + timedelta(minutes=row['late_tolerance_minutes'])
            is_arr_valid = cin.time() <= max_arr.time()
            is_dep_valid = cout.time() >= row['schedule_end']
            status = '<span class="badge bg-success">Conforme</span>' if (is_arr_valid and is_dep_valid) else '<span class="badge bg-warning text-dark">Non conforme</span>'
            report_rows_html += f"<tr><td><strong>{row['punch_date']}</strong></td><td><code>{row['badge_or_card_id']}</code></td><td class='text-start ps-4 fw-semibold'>{emp_name}</td><td>{row['schedule_name']}</td><td>{cin_str}</td><td>{cout_str}</td><td><strong>{hrs}h {mins:02d}m</strong></td><td>{dev_display}</td><td>{status}</td><td><a href='/reports/delete/{del_id}' class='btn btn-sm btn-outline-danger'><i class='fa-solid fa-trash'></i></a></td></tr>"

    device_options = '<option value="ALL">Tous les terminaux</option>' + "".join([f'<option value="{d["key"]}" {"selected" if device_id == d["key"] else ""}>{d["name"]}</option>' for d in devices])
    export_excel_url = f"/reports/export/excel?start_date={start_date}&end_date={end_date}" + (f"&device_id={device_id}" if device_id else "")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8"><title>Rapport Journalier</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>body {{ background-color: #f4f7f6; font-family: 'Segoe UI', sans-serif; }} .navbar {{ background: linear-gradient(135deg, #1e293b, #0f172a) !important; }} .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}</style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3">
            <div class="container-fluid px-4"><span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-calendar-days me-2"></i> Rapports Détaillés</span>
                <div class="d-flex gap-2"><a href="/" class="btn btn-outline-light btn-sm px-3">Accueil</a><a href="/logout" class="btn btn-danger btn-sm px-3">Déconnexion</a></div>
            </div>
        </nav>
        <div class="container-fluid px-4 my-4">
            <div class="card shadow-sm mb-4">
                <div class="card-body p-4">
                    <form method="GET" action="/reports" class="row g-3">
                        <div class="col-md-4"><label class="form-label small fw-bold">Date début</label><input type="date" class="form-control" name="start_date" value="{start_date}" required></div>
                        <div class="col-md-4"><label class="form-label small fw-bold">Date fin</label><input type="date" class="form-control" name="end_date" value="{end_date}" required></div>
                        <div class="col-md-2"><label class="form-label small fw-bold">Terminal</label><select class="form-select" name="device_id">{device_options}</select></div>
                        <div class="col-md-2 d-flex align-items-end"><button type="submit" class="btn btn-success w-100 fw-semibold">Filtrer</button></div>
                    </form>
                </div>
            </div>
            <div class="card shadow-sm">
                <div class="card-header bg-white d-flex justify-content-between align-items-center py-3">
                    <h5 class="mb-0 fs-6 fw-bold">Pointages ({len(reports)})</h5>
                    <div class="d-flex gap-2"><a href="{export_excel_url}" class="btn btn-success btn-sm"><i class="fa-solid fa-file-excel me-1"></i> Excel</a><button onclick="window.print();" class="btn btn-outline-dark btn-sm">Imprimer</button></div>
                </div>
                <div class="card-body p-0"><div class="table-responsive"><table class="table table-striped table-hover mb-0 align-middle text-center">
                    <thead><tr><th>Date</th><th>Matricule</th><th class="text-start ps-4">Employé</th><th>Shift</th><th>Arrivée</th><th>Départ</th><th>Heures</th><th>Terminal</th><th>Statut</th><th>Action</th></tr></thead>
                    <tbody>{report_rows_html if report_rows_html else '<tr><td colspan="10" class="text-center py-4 text-muted">Aucun pointage.</td></tr>'}</tbody>
                </table></div></div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/reports/delete/{log_id}")
async def delete_attendance_log(log_id: int, saas_session: str = Cookie(None), company_id: str = Cookie("1")):
    if saas_session != "company_user": return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM attendance_raw_logs WHERE id = %s AND company_id = %s", (log_id, company_id))
            conn.commit()
        conn.close()
    except Exception as e: print(e)
    return RedirectResponse(url="/reports", status_code=303)

@app.get("/reports/export/excel")
async def export_reports_excel(start_date: str = None, end_date: str = None, device_id: str = None, saas_session: str = Cookie(None), company_id: str = Cookie("1")):
    if saas_session != "company_user": return RedirectResponse(url="/login", status_code=303)
    today_str = date.today().isoformat()
    start_date, end_date = start_date or today_str, end_date or today_str
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM devices_mapping WHERE company_id = %s", (company_id,))
            mappings = {row['device_key']: row['friendly_name'] for row in cursor.fetchall()}
            cursor.execute("SELECT * FROM work_schedules WHERE company_id = %s", (company_id,))
            all_scheds = cursor.fetchall()
            cursor.execute("SELECT es.badge_or_card_id, s.* FROM employee_schedules es JOIN work_schedules s ON es.schedule_id = s.id WHERE s.company_id = %s", (company_id,))
            emp_sched_map = {}
            for row in cursor.fetchall(): emp_sched_map.setdefault(row['badge_or_card_id'], []).append(row)

            query = "SELECT l.badge_or_card_id, COALESCE(e.first_name, 'Inconnu') as first_name, COALESCE(e.last_name, '') as last_name, DATE(l.punch_timestamp) as p_date, l.punch_timestamp as exact_punch, l.device_id FROM attendance_raw_logs l LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id AND e.company_id = l.company_id WHERE l.company_id = %s AND DATE(l.punch_timestamp) BETWEEN %s AND %s"
            params = [company_id, start_date, end_date]
            if device_id and device_id != "ALL":
                query += " AND l.device_id = %s"
                params.append(device_id)
            cursor.execute(query, tuple(params))
            raw_logs = cursor.fetchall()
        conn.close()

        grouped = {}
        for r in raw_logs:
            grouped.setdefault((r['badge_or_card_id'], str(r['p_date']), r['device_id']), []).append(r['exact_punch'])

        export_data = []
        for (badge, p_date, dev), ts_list in grouped.items():
            sorted_ts = sorted(ts_list)
            cin, cout = sorted_ts[0], (sorted_ts[-1] if len(sorted_ts) > 1 else None)
            duration = (cout - cin) if (cin and cout and cin != cout) else timedelta(0)
            hrs, mins = int(duration.total_seconds()) // 3600, (int(duration.total_seconds()) % 3600) // 60
            export_data.append({"Date": p_date, "Matricule": badge, "Arrivée": str(cin).split(' ')[1] if cin else '-', "Départ": str(cout).split(' ')[1] if cout else '-', "Heures": f"{hrs}h {mins:02d}m", "Terminal": mappings.get(dev, dev)})

        df = pd.DataFrame(export_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Rapport')
        output.seek(0)
        return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=Rapport_{start_date}.xlsx"})
    except Exception as e:
        print(e)
        return RedirectResponse(url="/reports", status_code=303)


# --- SYNTHÈSE GLOBALE ---
@app.get("/summary", response_class=HTMLResponse)
async def attendance_summary(request: Request, start_date: str = None, end_date: str = None, saas_session: str = Cookie(None), company_id: str = Cookie("1")):
    if saas_session != "company_user": return RedirectResponse(url="/login", status_code=303)
    today_str = date.today().isoformat()
    start_date, end_date = start_date or today_str, end_date or today_str
    summary_rows = []
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM work_schedules WHERE company_id = %s", (company_id,))
            all_scheds = cursor.fetchall()
            cursor.execute("SELECT es.badge_or_card_id, s.* FROM employee_schedules es JOIN work_schedules s ON es.schedule_id = s.id WHERE s.company_id = %s", (company_id,))
            emp_sched_map = {}
            for row in cursor.fetchall(): emp_sched_map.setdefault(row['badge_or_card_id'], []).append(row)

            cursor.execute("SELECT COALESCE(e.first_name, 'Inconnu') as fn, COALESCE(e.last_name, '') as ln, l.badge_or_card_id, DATE(l.punch_timestamp) as pd, MIN(l.punch_timestamp) as cin, MAX(l.punch_timestamp) as cout FROM attendance_raw_logs l LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id AND e.company_id = l.company_id WHERE l.company_id = %s AND DATE(l.punch_timestamp) BETWEEN %s AND %s GROUP BY l.badge_or_card_id, DATE(l.punch_timestamp), e.first_name, e.last_name", (company_id, start_date, end_date))
            raw_logs = cursor.fetchall()
        conn.close()

        emp_stats = {}
        for r in raw_logs:
            badge = r['badge_or_card_id']
            stat = emp_stats.setdefault(badge, {"name": f"{r['fn']} {r['ln']}".strip(), "present": 0, "absent": 0, "late": 0, "secs": 0})
            cin, cout = r['cin'], r['cout']
            if not cin or not cout or cin == cout:
                stat["absent"] += 1
            else:
                best_sched = get_best_matching_schedule(cin, emp_sched_map.get(badge, []), all_scheds)
                max_arr = datetime.combine(r['pd'], best_sched['start_time']) + timedelta(minutes=best_sched.get('late_tolerance_minutes', 30))
                if cin <= max_arr: stat["present"] += 1
                else: 
                    stat["late"] += 1
                    stat["present"] += 1
                stat["secs"] += int((cout - cin).total_seconds())

        for badge, data in emp_stats.items():
            hrs, mins = data["secs"] // 3600, (data["secs"] % 3600) // 60
            summary_rows.append({"employee": data["name"], "present": data["present"], "absent": data["absent"], "late": data["late"], "hours": f"{hrs}h {mins:02d}m"})
    except Exception as e: print(e)

    summary_table_rows = "".join([f"<tr><td class='text-start ps-4 fw-semibold'>{r['employee']}</td><td><span class='badge bg-success'>{r['present']}</span></td><td><span class='badge bg-danger'>{r['absent']}</span></td><td><span class='badge bg-warning text-dark'>{r['late']}</span></td><td class='fw-bold text-primary'>{r['hours']}</td></tr>" for r in summary_rows]) if summary_rows else '<tr><td colspan="5" class="text-center py-4 text-muted">Aucune donnée.</td></tr>'

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head><meta charset="UTF-8"><title>Synthèse Globale</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>body {{ background-color: #f4f7f6; font-family: 'Segoe UI', sans-serif; }} .navbar {{ background: linear-gradient(135deg, #1e293b, #0f172a) !important; }} .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}</style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3"><div class="container-fluid px-4"><span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-chart-pie me-2"></i> Synthèse Globale</span><div class="d-flex gap-2"><a href="/" class="btn btn-outline-light btn-sm px-3">Accueil</a><a href="/logout" class="btn btn-danger btn-sm px-3">Déconnexion</a></div></div></nav>
        <div class="container-fluid px-4 my-4">
            <div class="card shadow-sm mb-4"><div class="card-body p-4">
                <form method="GET" action="/summary" class="row g-3">
                    <div class="col-md-5"><label class="form-label small fw-bold">Date début</label><input type="date" class="form-control" name="start_date" value="{start_date}" required></div>
                    <div class="col-md-5"><label class="form-label small fw-bold">Date fin</label><input type="date" class="form-control" name="end_date" value="{end_date}" required></div>
                    <div class="col-md-2 d-flex align-items-end"><button type="submit" class="btn btn-success w-100 fw-semibold">Filtrer</button></div>
                </form>
            </div></div>
            <div class="card shadow-sm">
                <div class="card-header bg-white d-flex justify-content-between align-items-center py-3"><h5 class="mb-0 fs-6 fw-bold">Récapitulatif ({len(summary_rows)})</h5><button onclick="window.print();" class="btn btn-outline-dark btn-sm">Imprimer</button></div>
                <div class="card-body p-0"><div class="table-responsive"><table class="table table-striped table-hover mb-0 align-middle text-center">
                    <thead><tr><th class="text-start ps-4">Employé</th><th>Présents</th><th>Absents</th><th>Retards</th><th class="pe-4">Total Heures</th></tr></thead>
                    <tbody>{summary_table_rows}</tbody>
                </table></div></div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- WEBHOOK HIKVISION ---
@app.post("/api/v1/attendance-webhook")
async def receive_attendance(request: Request):
    try:
        body = await request.body()
        raw_text = body.decode("utf-8", errors="ignore")
        company_slug = request.query_params.get("company")

        employee_no, device_id = "UNKNOWN", None
        m_dev = re.search(r'"(?:serialNo|serialNumber|deviceID)"\s*[:=]\s*["\']?([^"\'<>,\s]+)["\']?', raw_text)
        if m_dev: device_id = m_dev.group(1).strip()
        m_emp = re.search(r'"(?:employeeNoString|employeeNo|cardNo)"\s*[:=]\s*["\']?([^"\'<>,\s]+)["\']?', raw_text)
        if m_emp: employee_no = m_emp.group(1).strip()

        if employee_no == "UNKNOWN":
            return Response(content="<Response><statusString>IGNORED</statusString></Response>", media_type="application/xml", status_code=200)

        local_punch_time = datetime.now(ZoneInfo("Africa/Kinshasa")).replace(tzinfo=None)

        conn = get_db_connection()
        with conn.cursor() as cursor:
            company_id = 1
            if company_slug:
                cursor.execute("SELECT id FROM companies WHERE slug = %s", (company_slug.strip(),))
                comp = cursor.fetchone()
                if comp: company_id = comp['id']

            cursor.execute("INSERT INTO attendance_raw_logs (company_id, badge_or_card_id, punch_timestamp, device_id, punch_type) VALUES (%s, %s, %s, %s, 'IN')", (company_id, employee_no, local_punch_time, device_id or "Terminal_Inconnu"))
            conn.commit()
        conn.close()

        return Response(content="<Response><statusString>OK</statusString></Response>", media_type="application/xml", status_code=200)
    except Exception as e:
        print(f"Erreur webhook : {e}")
        return Response(content="<Response><statusString>ERROR</statusString></Response>", media_type="application/xml", status_code=500)