import sys
import os
import json
import re
from zoneinfo import ZoneInfo

# Ajout dynamique du dossier parent dans le chemin Python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import FastAPI, Request, Response, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from datetime import datetime, date, time
from database.db_config import get_db_connection

app = FastAPI(title="Hikvision Attendance System")

# Route directe et infaillible pour servir le logo ABS
@app.get("/static/logo.png")
async def get_logo():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logo_path = os.path.join(base_dir, "static", "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    return Response(status_code=404)

DRH_USERNAME = "drh"
DRH_PASSWORD = "password123"

# Fonction utilitaire pour initialiser la table de renommage des appareils
def init_devices_table():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS devices_mapping (
                    device_key VARCHAR(100) PRIMARY KEY,
                    friendly_name VARCHAR(100) NOT NULL
                )
            """)
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur initialisation table devices : {e}")

@app.on_event("startup")
async def startup_event():
    init_devices_table()

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
async def dashboard(request: Request, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    employees = []
    devices = []
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            try:
                cursor.execute("SELECT e.*, s.name as schedule_name FROM employees e LEFT JOIN work_schedules s ON e.schedule_id = s.id")
                employees = cursor.fetchall()
            except Exception:
                employees = []
                
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

    emp_rows_html = ""
    if employees:
        for emp in employees:
            is_act = emp.get('is_active', True)
            status_badge = '<span class="badge bg-success bg-opacity-10 text-success">Actif</span>' if is_act else '<span class="badge bg-danger bg-opacity-10 text-danger">Inactif</span>'
            emp_rows_html += f"""
                <tr>
                    <td class="ps-3"><code>{emp['badge_or_card_id']}</code></td>
                    <td class="fw-semibold">{emp['first_name']} {emp['last_name']}</td>
                    <td>{emp.get('department') or '-'}</td>
                    <td>{emp.get('schedule_name') or 'Standard (8h-17h)'}</td>
                    <td>{status_badge}</td>
                    <td class="pe-3 text-end">
                        <a href="/employees/edit/{emp['badge_or_card_id']}" class="btn btn-sm btn-outline-primary me-1" title="Modifier"><i class="fa-solid fa-pen-to-square"></i></a>
                        <a href="/employees/delete/{emp['badge_or_card_id']}" class="btn btn-sm btn-outline-danger" title="Supprimer" onclick="return confirm('Voulez-vous vraiment supprimer cet employé ?');"><i class="fa-solid fa-trash"></i></a>
                    </td>
                </tr>
            """
    else:
        emp_rows_html = """
                <tr>
                    <td colspan="6" class="text-center py-4 text-muted">Aucun employé trouvé.</td>
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

            <div class="card shadow-sm mb-4">
                <div class="card-header bg-dark text-white py-3">
                    <h5 class="mb-0 fs-6 fw-bold"><i class="fa-solid fa-network-wired me-2"></i> Gestion & Renommage des Terminaux de Pointage</h5>
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
                                <button type="submit" class="btn btn-primary w-100 py-2 fw-semibold shadow-sm"><i class="fa-solid fa-check me-2"></i> Enregistrer l'employé</button>
                            </form>
                        </div>
                    </div>
                </div>

                <div class="col-lg-8">
                    <div class="card mt-0">
                        <div class="card-header py-3 d-flex justify-content-between align-items-center">
                            <h5 class="mb-0 fs-6 fw-bold text-dark"><i class="fa-solid fa-users-gear me-2 text-primary"></i> Liste des Employés</h5>
                            <a href="/summary" class="btn btn-sm btn-outline-primary fw-semibold"><i class="fa-solid fa-chart-pie me-1"></i> Voir la synthèse globale</a>
                        </div>
                        <div class="card-body p-0">
                            <div class="table-responsive">
                                <table class="table table-hover align-middle mb-0">
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
                                    <tbody>
                                        {emp_rows_html}
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


# --- ROUTE DE SUPPRESSION D'UN TERMINAL ---
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


# --- ROUTE DE SUPPRESSION D'UN POINTAGE (LOG) ---
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
                    DATE(l.punch_timestamp) as punch_date,
                    l.punch_timestamp as exact_punch,
                    l.device_id
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id
                WHERE DATE(l.punch_timestamp) BETWEEN %s AND %s
            """
            
            params = [start_date, end_date]
            
            if device_id and device_id != "ALL":
                base_query += " AND l.device_id = %s"
                params.append(device_id)

            base_query += " ORDER BY l.punch_timestamp DESC"
            
            cursor.execute(base_query, tuple(params))
            raw_logs = cursor.fetchall()

            # Regroupement par employé et par jour pour faire la ligne d'arrivée / départ
            grouped = {}
            for r in raw_logs:
                key = (r['badge_or_card_id'], str(r['punch_date']), r['device_id'])
                if key not in grouped:
                    grouped[key] = {
                        "badge_or_card_id": r['badge_or_card_id'],
                        "first_name": r['first_name'],
                        "last_name": r['last_name'],
                        "punch_date": r['punch_date'],
                        "device_id": r['device_id'],
                        "timestamps": []
                    }
                grouped[key]["timestamps"].append((r['id'], r['exact_punch']))

            for key, data in grouped.items():
                # Trier les timestamps chronologiquement pour identifier check_in et check_out
                sorted_ts = sorted(data["timestamps"], key=lambda x: x[1])
                check_in_item = sorted_ts[0]
                check_out_item = sorted_ts[-1] if len(sorted_ts) > 1 else None

                reports.append({
                    "badge_or_card_id": data["badge_or_card_id"],
                    "first_name": data["first_name"],
                    "last_name": data["last_name"],
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

            # ID de suppression prioritaire (sur check_out si présent, sinon check_in)
            del_id = row['check_out_id'] if row['check_out_id'] else row['check_in_id']

            if not check_in or not check_out or check_in == check_out:
                report_rows_html += f"""
                            <tr>
                                <td><strong>{row['punch_date']}</strong></td>
                                <td><code>{row['badge_or_card_id']}</code></td>
                                <td class="text-start ps-4 fw-semibold">{emp_name}</td>
                                <td><span class="badge bg-danger text-white">En retard ({check_in_str})</span></td>
                                <td><span class="text-muted fst-italic">Manquant</span></td>
                                <td>-</td>
                                <td><span class="badge bg-secondary">{dev_display}</span></td>
                                <td><span class="badge bg-danger text-white">Absent (Hors plage)</span></td>
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

            is_arrival_valid = time(8, 0) <= check_in_t <= time(8, 30)
            is_departure_valid = time(16, 30) <= check_out_t <= time(17, 0)

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
                global_status = '<span class="badge bg-danger text-white">Absent (Hors plage)</span>'

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
                    <button onclick="window.print();" class="btn btn-outline-dark btn-sm no-print fw-semibold"><i class="fa-solid fa-print me-1"></i> Imprimer / Exporter PDF</button>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-striped table-hover mb-0 align-middle text-center">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Matricule</th>
                                    <th class="text-start ps-4">Employé</th>
                                    <th>Arrivée (8h00 - 8h30)</th>
                                    <th>Départ (16h30 - 17h00)</th>
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
                    DATE(l.punch_timestamp) as p_date,
                    MIN(l.punch_timestamp) as check_in,
                    MAX(l.punch_timestamp) as check_out
                FROM attendance_raw_logs l
                LEFT JOIN employees e ON l.badge_or_card_id = e.badge_or_card_id
                WHERE DATE(l.punch_timestamp) BETWEEN %s AND %s
                GROUP BY l.badge_or_card_id, DATE(l.punch_timestamp), e.first_name, e.last_name
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
                        "days_records": set(),
                        "on_time": 0,
                        "late": 0,
                        "needs_review": 0,
                        "missing_time": 0,
                        "provisional_late_minutes": 0
                    }
                
                p_date = row['p_date']
                emp_stats[badge]["days_records"].add(p_date)
                
                cin = row['check_in']
                cout = row['check_out']
                
                if not cin or not cout or cin == cout:
                    emp_stats[badge]["missing_time"] += 1
                    emp_stats[badge]["needs_review"] += 1
                else:
                    cin_t = cin.time()
                    cout_t = cout.time()
                    
                    is_arr_valid = time(8, 0) <= cin_t <= time(8, 30)
                    is_dep_valid = time(16, 30) <= cout_t <= time(17, 0)
                    
                    if is_arr_valid and is_dep_valid:
                        emp_stats[badge]["on_time"] += 1
                    else:
                        emp_stats[badge]["late"] += 1
                        emp_stats[badge]["needs_review"] += 1
                        
                        if cin_t > time(8, 30):
                            standard_time = datetime.combine(p_date, time(8, 30))
                            late_delta = cin - standard_time
                            late_mins = int(late_delta.total_seconds() // 60)
                            if late_mins > 0:
                                emp_stats[badge]["provisional_late_minutes"] += late_mins

            for badge, data in emp_stats.items():
                summary_rows.append({
                    "badge": badge,
                    "employee": data["name"],
                    "days_with_records": len(data["days_records"]),
                    "on_time": data["on_time"],
                    "late": data["late"],
                    "needs_review": data["needs_review"],
                    "missing_time": data["missing_time"],
                    "provisional_late_minutes": data["provisional_late_minutes"]
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
                                    <td>{row['days_with_records']}</td>
                                    <td><span class="badge bg-success bg-opacity-10 text-success">{row['on_time']}</span></td>
                                    <td><span class="badge bg-warning bg-opacity-20 text-dark">{row['late']}</span></td>
                                    <td><span class="badge bg-info bg-opacity-10 text-info">{row['needs_review']}</span></td>
                                    <td><span class="badge bg-danger bg-opacity-10 text-danger">{row['missing_time']}</span></td>
                                    <td class="pe-4 fw-bold text-secondary">{row['provisional_late_minutes']} min</td>
                                </tr>
            """
    else:
        summary_table_rows = """
                                <tr>
                                    <td colspan="7" class="text-center py-4 text-muted">Aucune donnée trouvée pour cette période.</td>
                                </tr>
        """

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
                    <button onclick="window.print();" class="btn btn-outline-dark btn-sm no-print fw-semibold"><i class="fa-solid fa-print me-1"></i> Imprimer / Exporter PDF</button>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-striped table-hover mb-0 align-middle text-center">
                            <thead>
                                <tr>
                                    <th class="text-start ps-4">Employee</th>
                                    <th>Days with Records</th>
                                    <th>On Time*</th>
                                    <th>Late*</th>
                                    <th>Needs Review</th>
                                    <th>Missing Time</th>
                                    <th class="pe-4">Provisional Late Minutes</th>
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


@app.post("/employees/add")
async def add_employee(
    badge_or_card_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(None),
    drh_session: str = Cookie(None)
):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO employees (badge_or_card_id, first_name, last_name, department, is_active)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    badge_or_card_id.strip(), 
                    first_name.strip(), 
                    last_name.strip(), 
                    department.strip() if department else None,
                    True
                )
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de l'ajout de l'employé : {e}")

    return RedirectResponse(url="/", status_code=303)


# --- ROUTE DE SUPPRESSION D'UN EMPLOYÉ ---
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


# --- ROUTE D'AFFICHAGE DU FORMULAIRE DE MODIFICATION ---
@app.get("/employees/edit/{badge_id}", response_class=HTMLResponse)
async def edit_employee_page(badge_id: str, drh_session: str = Cookie(None)):
    if drh_session != "authenticated":
        return RedirectResponse(url="/login", status_code=303)

    employee = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM employees WHERE badge_or_card_id = %s", (badge_id,))
            employee = cursor.fetchone()
        conn.close()
    except Exception as e:
        print(f"Erreur lors de la récupération de l'employé : {e}")

    if not employee:
        return RedirectResponse(url="/", status_code=303)

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


# --- ROUTE DE TRAITEMENT DE LA MODIFICATION ---
@app.post("/employees/update/{old_badge_id}")
async def update_employee(
    old_badge_id: str,
    badge_or_card_id: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(None),
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
                SET badge_or_card_id = %s, first_name = %s, last_name = %s, department = %s, is_active = %s
                WHERE badge_or_card_id = %s
                """,
                (
                    badge_or_card_id.strip(),
                    first_name.strip(),
                    last_name.strip(),
                    department.strip() if department else None,
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

            for key_candidate in ["employeeNoString", "employeeNo", "cardNo", "employeeID", "userID"]:
                val = find_key(data, key_candidate)
                if val:
                    employee_no = val
                    break
                    
            ip_val = find_key(data, "ipAddress") or find_key(data, "deviceIpAddress") or find_key(data, "deviceID") or find_key(data, "serialNo") or find_key(data, "macAddress")
            if ip_val:
                device_id = ip_val
        except Exception:
            pass

        if not device_id:
            if "terme_unique_terminal_2" in raw_text:
                device_id = "HIK_TERMINAL_2"
            else:
                client_host = request.client.host if request.client else None
                if client_host and client_host not in ("127.0.0.1", "localhost", "::1"):
                    if client_host == "102.68.58.216":
                        device_id = "102.68.58.216"
                    else:
                        device_id = client_host
                else:
                    device_id = "TERMINAL_ABS_SHOP" 

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

        print(f"-> Pointage validé | Matricule extrait : {employee_no} | Appareil : {device_id}")

        local_punch_time = datetime.now(ZoneInfo("Africa/Kinshasa")).replace(tzinfo=None)

        conn = get_db_connection()
        with conn.cursor() as cursor:
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