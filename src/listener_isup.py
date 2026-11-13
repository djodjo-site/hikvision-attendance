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

# Identifiants du Super-Admin (Vous, propriétaire du SaaS)
SUPERADMIN_USER = "superadmin"
SUPERADMIN_PASS = "masterpassword123"

# Fonction utilitaire pour initialiser les tables SaaS et multi-rôles
def init_database_tables():
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

            # Comptes par défaut pour l'entreprise 1 (admin / superviseur)
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


# --- PAGE DE CONNEXION COMMUNE (SUPER-ADMIN & CLIENTS) ---
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
    # 1. Vérification Super-Admin (Vous)
    if username == SUPERADMIN_USER and password == SUPERADMIN_PASS:
        response = RedirectResponse(url="/superadmin", status_code=303)
        response.set_cookie(key="saas_session", value="superadmin", httponly=True)
        return response

    # 2. Vérification Utilisateur d'entreprise (Admin général ou Superviseur)
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
            response.set_cookie(key="user_role", value=user['role'], httponly=True) # 'admin' ou 'supervisor'
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


# --- 1. PORTAIL PRINCIPAL DU SUPER-ADMIN (POUR VOUS) ---
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
                    <a href="/superadmin/login-as/{c['id']}" class="btn btn-sm btn-outline-primary me-1" title="Accéder à l'espace client"><i class="fa-solid fa-right-to-bracket"></i> Gérer</a>
                    <a href="/superadmin/company/delete/{c['id']}" class="btn btn-sm btn-outline-danger" onclick="return confirm('Attention : Supprimer cette entreprise supprimera aussi tous ses employés et pointages associés. Continuer ?');"><i class="fa-solid fa-trash"></i></a>
                </td>
            </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Portail Super-Admin - SaaS</title>
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
                <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-crown me-2 text-warning"></i> Portail Commercial Super-Admin (SaaS)</span>
                <div class="d-flex align-items-center gap-2">
                    <a href="/logout" class="btn btn-danger btn-sm px-3"><i class="fa-solid fa-right-from-bracket me-1"></i> Déconnexion</a>
                </div>
            </div>
        </nav>

        <div class="container my-4" style="max-width: 1050px;">
            {alert_html}
            <div class="row g-4">
                <div class="col-md-5">
                    <div class="card">
                        <div class="card-header bg-primary text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-building-circle-plus me-2"></i> Créer une Entreprise Cliente</h5>
                        </div>
                        <div class="card-body p-4">
                            <form action="/superadmin/company/add" method="POST">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Nom de l'entreprise</label>
                                    <input type="text" class="form-control" name="name" placeholder="ex: Entreprise B" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Slug Webhook</label>
                                    <input type="text" class="form-control" name="slug" placeholder="ex: entreprise-b" required>
                                    <div class="form-text small">Utilisé dans l'URL : <code>?company=slug</code></div>
                                </div>
                                <hr class="my-3">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Nom utilisateur Admin Général</label>
                                    <input type="text" class="form-control" name="admin_user" placeholder="ex: admin_b" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Mot de passe Admin Général</label>
                                    <input type="password" class="form-control" name="admin_pass" placeholder="••••••••" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Mot de passe Superviseur (Lecture seule)</label>
                                    <input type="password" class="form-control" name="super_pass" placeholder="••••••••" required>
                                    <div class="form-text small">Nom d'utilisateur par défaut : <code>superviseur</code></div>
                                </div>
                                <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-check me-2"></i> Créer le compte client</button>
                            </form>
                        </div>
                    </div>
                </div>
                <div class="col-md-7">
                    <div class="card">
                        <div class="card-header bg-dark text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-list me-2"></i> Entreprises Clientes Abonnées ({len(companies)})</h5>
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
async def superadmin_add_company(
    name: str = Form(...), 
    slug: str = Form(...), 
    admin_user: str = Form(...), 
    admin_pass: str = Form(...), 
    super_pass: str = Form(...), 
    saas_session: str = Cookie(None)
):
    if saas_session != "superadmin":
        return RedirectResponse(url="/login", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id", (name.strip(), slug.strip().lower()))
            comp_id = cursor.fetchone()['id']

            # Création de l'admin général de l'entreprise
            cursor.execute(
                "INSERT INTO company_users (company_id, username, password, role) VALUES (%s, %s, %s, 'admin')", 
                (comp_id, admin_user.strip(), admin_pass.strip())
            )
            # Création du superviseur de l'entreprise (lecture seule)
            cursor.execute(
                "INSERT INTO company_users (company_id, username, password, role) VALUES (%s, %s, %s, 'supervisor')", 
                (comp_id, "superviseur", super_pass.strip())
            )

            conn.commit()
        conn.close()
        return RedirectResponse(url="/superadmin?success=Entreprise+et+comptes+créés+avec+succès", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/superadmin?error=Erreur+lors+de+l'ajout:+{str(e)}", status_code=303)

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
        return RedirectResponse(url="/superadmin?error=Impossible+de+supprimer+l'entreprise+de+démo", status_code=303)
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

            try:
                cursor.execute("""
                    SELECT e.*
                    FROM employees e 
                    WHERE e.company_id = %s
                    ORDER BY COALESCE(NULLIF(TRIM(e.department), ''), 'Non assigné') ASC, e.first_name ASC, e.last_name ASC
                """, (company_id,))
                employees = cursor.fetchall()
            except Exception:
                employees = []
                
            try:
                cursor.execute("SELECT * FROM work_schedules WHERE company_id = %s ORDER BY id ASC", (company_id,))
                schedules = cursor.fetchall()
            except Exception:
                schedules = []
                
            try:
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
            except Exception as e:
                print(f"Erreur chargement horaires employés : {e}")

            cursor.execute("SELECT DISTINCT device_id FROM attendance_raw_logs WHERE device_id IS NOT NULL AND company_id = %s", (company_id,))
            raw_devs = [row['device_id'] for row in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM devices_mapping WHERE company_id = %s", (company_id,))
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

    is_supervisor = (user_role == "supervisor")
    role_badge = '<span class="badge bg-warning text-dark px-3 py-2">Superviseur (Lecture seule)</span>' if is_supervisor else '<span class="badge bg-primary px-3 py-2">Admin Général</span>'

    alert_html = ""
    if error:
        alert_html = f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="fa-solid fa-circle-exclamation me-2"></i>{error}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'
    elif success:
        alert_html = f'<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="fa-solid fa-circle-check me-2"></i>{success}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>'

    # Si superviseur, on masque ou restreint les blocs de modification
    import_section_html = "" if is_supervisor else """
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
                        </div>
                        <div class="col-md-4 d-flex align-items-end">
                            <button type="submit" class="btn btn-success w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-file-import me-2"></i> Importer les Employés</button>
                        </div>
                    </form>
                </div>
            </div>
    """

    schedules_mgmt_html = "" if is_supervisor else """
            <!-- SECTION GESTION DES HORAIRES DE TRAVAIL -->
            <div class="row g-4 mb-4">
                <div class="col-lg-5">
                    <div class="card h-100">
                        <div class="card-header bg-success text-white py-3">
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-clock me-2"></i> Ajouter un Horaire / Shift de Travail</h5>
                        </div>
                        <div class="card-body p-4">
                            <form action="/schedules/add" method="POST">
                                <div class="mb-3">
                                    <label class="form-label text-secondary small fw-bold">Nom de l'horaire / Vacation</label>
                                    <input type="text" class="form-control" name="name" placeholder="ex: Shift Matin" required>
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
                            <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-business-time me-2"></i> Catalogue des Horaires Disponibles</h5>
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
    else:
        emp_rows_html = """
                <tr id="no-emp-initial">
                    <td colspan="6" class="text-center py-4 text-muted">Aucun employé trouvé.</td>
                </tr>
        """

    sched_rows_html = ""
    sched_checkboxes_html = ""
    for s in schedules:
        sched_checkboxes_html += f"""
            <div class="form-check mb-1">
                <input class="form-check-input" type="checkbox" name="schedule_ids" value="{s['id']}" id="sched_check_{s['id']}">
                <label class="form-check-label small" for="sched_check_{s['id']}">
                    <strong>{s['name']}</strong> ({str(s['start_time'])[:5]} - {str(s['end_time'])[:5]})
                </label>
            </div>
        """
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
            rename_form = d['key'] if is_supervisor else f"""
                        <form action="/devices/rename" method="POST" class="d-flex gap-2">
                            <input type="hidden" name="device_key" value="{d['key']}">
                            <input type="text" class="form-control form-control-sm" name="friendly_name" value="{d['name']}" required>
                            <button type="submit" class="btn btn-sm btn-success text-nowrap"><i class="fa-solid fa-floppy-disk"></i> Enregistrer</button>
                        </form>
            """
            del_btn = "" if is_supervisor else f"""
                    <td class="text-end pe-3">
                        <a href="/devices/delete/{d['key']}" class="btn btn-sm btn-outline-danger" title="Supprimer" onclick="return confirm('Supprimer ce terminal ?');"><i class="fa-solid fa-trash"></i></a>
                    </td>
            """
            dev_rows_html += f"""
                <tr>
                    <td class="ps-3"><code>{d['key']}</code></td>
                    <td>{rename_form}</td>
                    {del_btn}
                </tr>
            """
    else:
        dev_rows_html = '<tr><td colspan="3" class="text-center py-3 text-muted">Aucun terminal détecté.</td></tr>'

    add_employee_card = "" if is_supervisor else f"""
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
                                    <label class="form-label text-secondary small fw-bold">Horaires & Shifts assignés</label>
                                    <div class="schedule-box">
                                        {sched_checkboxes_html if sched_checkboxes_html else '<span class="text-muted small">Aucun horaire créé.</span>'}
                                    </div>
                                </div>
                                <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-check me-2"></i> Enregistrer l'employé</button>
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
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Tableau de bord - {current_company_name}</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            body {{ background-color: #f4f7f6; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
            .navbar {{ background: linear-gradient(135deg, #1e293b, #0f172a) !important; }}
            .card {{ border: none; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
            .card-header {{ background-color: #ffffff; border-bottom: 1px solid #f1f5f9; border-top-left-radius: 12px !important; border-top-right-radius: 12px !important; font-weight: 600; }}
            .table th {{ font-weight: 600; color: #475569; background-color: #f8fafc !important; }}
            .badge {{ padding: 0.5em 0.8em; font-weight: 500; border-radius: 6px; }}
            .schedule-box {{ max-height: 140px; overflow-y: auto; border: 1px solid #dee2e6; border-radius: 6px; padding: 8px; background-color: #fafafa; }}
        </style>
    </head>
    <body>
        <nav class="navbar navbar-dark shadow-sm py-3">
            <div class="container-fluid px-4">
                <div class="d-flex align-items-center gap-3">
                    <span class="navbar-brand mb-0 h5 fw-bold"><i class="fa-solid fa-building me-2"></i> {current_company_name}</span>
                    {role_badge}
                </div>
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
                        <div class="card-body d-flex justify-content-between align-items-center">
                            <div>
                                <h6 class="text-muted fw-semibold mb-1">Terminaux Connectés</h6>
                                <h3 class="fw-bold text-info mb-0">{len(devices)} détecté(s)</h3>
                            </div>
                            <div class="text-info bg-info bg-opacity-10 p-3 rounded-circle">
                                <i class="fa-solid fa-network-wired fa-xl"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {import_section_html}
            {schedules_mgmt_html.replace("{sched_rows_html}", sched_rows_html)}

            <!-- SECTION EMPLOYÉS -->
            <div class="row g-4">
                {add_employee_card}
                <div class="{'col-lg-8' if not is_supervisor else 'col-lg-12'}">
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
                                            <th>Horaires assignés</th>
                                            <th>Statut</th>
                                            {"" if is_supervisor else '<th class="pe-3 text-end">Actions</th>'}
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
                    </div>
                </div>
            </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- ROUTE IMPORTATION D'EMPLOYÉS (EXCEL / CSV) ---
@app.post("/employees/import")
async def import_employees(file: UploadFile = File(...), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/?error=Accès+refusé:+Superviseur+en+lecture+seule", status_code=303)

    try:
        contents = await file.read()
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents))
        elif filename.endswith('.xlsx') or filename.endswith('.xls'):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            return RedirectResponse(url="/?error=Format+de+fichier+non+supporté", status_code=303)

        df.columns = [str(col).strip().lower() for col in df.columns]

        col_badge = next((c for c in df.columns if c in ['badge', 'badge_id', 'matricule', 'id', 'cardno', 'card_no', 'employee_id']), None)
        col_prenom = next((c for c in df.columns if c in ['prenom', 'prénom', 'first_name', 'firstname', 'prenoms']), None)
        col_nom = next((c for c in df.columns if c in ['nom', 'last_name', 'lastname', 'name']), None)
        col_dept = next((c for c in df.columns if c in ['departement', 'département', 'department', 'service', 'dept']), None)

        if not col_badge or not col_nom:
            return RedirectResponse(url="/?error=Colonnes+matricule+et+nom+requises", status_code=303)

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
                    INSERT INTO employees (badge_or_card_id, first_name, last_name, department, company_id, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (badge_or_card_id) DO UPDATE 
                    SET first_name = EXCLUDED.first_name, 
                        last_name = EXCLUDED.last_name, 
                        department = EXCLUDED.department,
                        company_id = EXCLUDED.company_id
                    """,
                    (badge_val, prenom_val, nom_val, dept_val, company_id)
                )
                imported_count += 1
            conn.commit()
        conn.close()

        return RedirectResponse(url=f"/?success={imported_count}+employé(s)+importé(s)+avec+succès", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error=Erreur+de+lecture:+{str(e)}", status_code=303)


# --- GESTION HORAIRES & EMPLOYÉS ---
@app.post("/schedules/add")
async def add_schedule(name: str = Form(...), start_time: str = Form(...), end_time: str = Form(...), late_tolerance_minutes: int = Form(30), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/?error=Accès+refusé", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO work_schedules (company_id, name, start_time, end_time, late_tolerance_minutes)
                VALUES (%s, %s, %s, %s, %s)
            """, (company_id, name.strip(), start_time, end_time, late_tolerance_minutes))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur ajout horaire : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/schedules/delete/{sched_id}")
async def delete_schedule(sched_id: int, saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/?error=Accès+refusé", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM employee_schedules WHERE schedule_id = %s", (sched_id,))
            cursor.execute("DELETE FROM work_schedules WHERE id = %s AND company_id = %s", (sched_id, company_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur suppression horaire : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.post("/employees/add")
async def add_employee(request: Request, badge_or_card_id: str = Form(...), first_name: str = Form(...), last_name: str = Form(...), department: str = Form(None), saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/?error=Accès+refusé", status_code=303)

    form_data = await request.form()
    schedule_ids = form_data.getlist("schedule_ids")

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO employees (badge_or_card_id, first_name, last_name, department, company_id, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (badge_or_card_id.strip(), first_name.strip(), last_name.strip(), department.strip() if department else None, company_id, True)
            )
            for s_id in schedule_ids:
                cursor.execute(
                    "INSERT INTO employee_schedules (badge_or_card_id, schedule_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (badge_or_card_id.strip(), int(s_id))
                )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur ajout employé : {e}")
    return RedirectResponse(url="/", status_code=303)

@app.get("/employees/delete/{badge_id}")
async def delete_employee(badge_id: str, saas_session: str = Cookie(None), company_id: str = Cookie("1"), user_role: str = Cookie("admin")):
    if saas_session != "company_user" or user_role == "supervisor":
        return RedirectResponse(url="/?error=Accès+refusé", status_code=303)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM employee_schedules WHERE badge_or_card_id = %s", (badge_id,))
            cursor.execute("DELETE FROM employees WHERE badge_or_card_id = %s AND company_id = %s", (badge_id, company_id))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur suppression employé : {e}")
    return RedirectResponse(url="/", status_code=303)


# --- RAPPORTS & SYNTHÈSE (ACCESSIBLES ADMIN ET SUPERVISEUR) ---
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

            cursor.execute("SELECT * FROM work_schedules WHERE company_id = %s", (company_id,))
            all_schedules = cursor.fetchall()

            cursor.execute("""
                SELECT es.badge_or_card_id, s.* FROM employee_schedules es
                JOIN work_schedules s ON es.schedule_id = s.id WHERE s.company_id = %s
            """, (company_id,))
            emp_schedules_map = {row['badge_or_card_id']: [] for row in cursor.fetchall()}
            for row in cursor.fetchall():
                emp_schedules_map[row['badge_or_card_id']].append(row)

            base_query = """
                SELECT l.id, l.badge_or_card_id, COALESCE(e.first_name, 'Inconnu') as first_name,
                       COALESCE(e.last_name, '') as last_name, DATE(l.punch_timestamp) as punch_date,
                       l.punch_timestamp as exact_punch, l.device_id
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

            grouped = {}
            for r in raw_logs:
                key = (r['badge_or_card_id'], str(r['punch_date']), r['device_id'])
                if key not in grouped:
                    grouped[key] = {
                        "badge": r['badge_or_card_id'], "first_name": r['first_name'],
                        "last_name": r['last_name'], "date": r['punch_date'],
                        "device_id": r['device_id'], "timestamps": []
                    }
                grouped[key]["timestamps"].append((r['id'], r['exact_punch']))

            for key, data in grouped.items():
                sorted_ts = sorted(data["timestamps"], key=lambda x: x[1])
                cin_item = sorted_ts[0]
                cout_item = sorted_ts[-1] if len(sorted_ts) > 1 else None
                best_sched = get_best_matching_schedule(cin_item[1], emp_schedules_map.get(data["badge"], []), all_schedules)

                reports.append({
                    "badge": data["badge"], "name": f"{data['first_name']} {data['last_name']}",
                    "schedule": best_sched['name'], "date": data["date"],
                    "cin": str(cin_item[1]).split(' ')[1], "cout": str(cout_item[1]).split(' ')[1] if cout_item else '-'
                })
        conn.close()
    except Exception as e:
        print(f"Erreur rapport : {e}")

    report_rows_html = "".join([f"<tr><td>{r['date']}</td><td><code>{r['badge']}</code></td><td class='text-start ps-3'>{r['name']}</td><td>{r['schedule']}</td><td>{r['cin']}</td><td>{r['cout']}</td></tr>" for r in reports]) or '<tr><td colspan="6" class="text-center py-4 text-muted">Aucun pointage trouvé.</td></tr>'

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <title>Rapport - SaaS</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body class="bg-light">
        <nav class="navbar navbar-dark bg-dark py-3">
            <div class="container-fluid px-4">
                <span class="navbar-brand fw-bold">Rapport Journalier</span>
                <a href="/" class="btn btn-outline-light btn-sm">Tableau de bord</a>
            </div>
        </nav>
        <div class="container my-4">
            <div class="card shadow-sm p-4">
                <table class="table table-hover align-middle text-center">
                    <thead><tr><th>Date</th><th>Matricule</th><th class="text-start ps-3">Employé</th><th>Shift</th><th>Arrivée</th><th>Départ</th></tr></thead>
                    <tbody>{report_rows_html}</tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """)

@app.get("/summary", response_class=HTMLResponse)
async def attendance_summary(request: Request, saas_session: str = Cookie(None), company_id: str = Cookie("1")):
    if saas_session != "company_user":
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse('<!DOCTYPE html><html><head><title>Synthèse</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head><body class="bg-light"><div class="container my-4"><a href="/" class="btn btn-secondary mb-3">← Retour</a><div class="card p-4"><h4>Synthèse des Présences</h4><p class="text-muted">Module de synthèse global actif.</p></div></div></body></html>')


# --- WEBHOOK HIKVISION MULTI-TENANT ---
@app.post("/api/v1/attendance-webhook")
async def receive_attendance(request: Request):
    try:
        body = await request.body()
        raw_text = body.decode("utf-8", errors="ignore")
        company_slug = request.query_params.get("company")

        employee_no = "UNKNOWN"
        device_id = None
        
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

            cursor.execute(
                """
                INSERT INTO attendance_raw_logs (company_id, badge_or_card_id, punch_timestamp, device_id, punch_type)
                VALUES (%s, %s, %s, %s, 'IN')
                """,
                (company_id, employee_no, local_punch_time, device_id or "Terminal_Inconnu")
            )
            conn.commit()
        conn.close()

        return Response(content="<Response><statusString>OK</statusString></Response>", media_type="application/xml", status_code=200)
    except Exception as e:
        print(f"Erreur webhook : {e}")
        return Response(content="<Response><statusString>ERROR</statusString></Response>", media_type="application/xml", status_code=500)