-- =========================================================================
-- 1. Table des horaires de travail (Configurations des shifts)
-- =========================================================================
CREATE TABLE work_schedules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,                  -- Ex: "Horaire Standard 08h-17h"
    start_time TIME NOT NULL,                    -- Heure d'arrivée théorique (ex: 08:00:00)
    end_time TIME NOT NULL,                      -- Heure de départ théorique (ex: 17:00:00)
    late_tolerance_minutes INT DEFAULT 15,       -- Tolérance avant de comptabiliser un retard (en min)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- 2. Table des employés
-- =========================================================================
CREATE TABLE employees (
    id SERIAL PRIMARY KEY,
    badge_or_card_id VARCHAR(100) UNIQUE NOT NULL, -- Identifiant unique de pointage ou numéro de carte/badge sur le terminal Hikvision
    hikvision_employee_no VARCHAR(100) UNIQUE,     -- Numéro d'employé configuré dans l'appareil Hikvision
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    department VARCHAR(100),                       -- Département / Service
    schedule_id INT REFERENCES work_schedules(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================================
-- 3. Table des logs bruts (Événements reçus du terminal Hikvision / ISUP)
-- =========================================================================
CREATE TABLE attendance_raw_logs (
    id SERIAL PRIMARY KEY,
    badge_or_card_id VARCHAR(100) NOT NULL,        -- Lien avec le badge ou l'employé
    punch_timestamp TIMESTAMP NOT NULL,            -- Date et heure exactes du pointage
    device_id VARCHAR(100),                        -- Identifiant du terminal Hikvision source
    punch_type VARCHAR(20),                        -- 'IN' (Entrée), 'OUT' (Sortie) ou détecté automatiquement
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index pour optimiser les recherches par date et par employé sur les logs bruts
CREATE INDEX idx_raw_logs_timestamp ON attendance_raw_logs(punch_timestamp);
CREATE INDEX idx_raw_logs_badge ON attendance_raw_logs(badge_or_card_id);

-- =========================================================================
-- 4. Table des synthèses journalières (Calculées par votre backend)
-- =========================================================================
CREATE TABLE daily_summaries (
    id SERIAL PRIMARY KEY,
    employee_id INT REFERENCES employees(id) ON DELETE CASCADE,
    attendance_date DATE NOT NULL,
    first_check_in TIME,                           -- Heure du premier pointage de la journée
    last_check_out TIME,                           -- Heure du dernier pointage de la journée
    worked_hours NUMERIC(5,2) DEFAULT 0.00,        -- Nombre d'heures effectives travaillées
    late_minutes INT DEFAULT 0,                    -- Minutes de retard enregistrées
    status VARCHAR(30) DEFAULT 'ABSENT',           -- 'PRESENT', 'ABSENT', 'RETARD', 'CONGE', etc.
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_employee_date UNIQUE (employee_id, attendance_date)
);

-- Index pour accélérer la génération des rapports mensuels
CREATE INDEX idx_daily_summaries_date ON daily_summaries(attendance_date);

-- =========================================================================
-- 5. Table des synthèses mensuelles (Pour la paie / RH)
-- =========================================================================
CREATE TABLE monthly_reports (
    id SERIAL PRIMARY KEY,
    employee_id INT REFERENCES employees(id) ON DELETE CASCADE,
    year_month VARCHAR(7) NOT NULL,                -- Format 'AAAA-MM' (Ex: '2026-11')
    total_worked_hours NUMERIC(7,2) DEFAULT 0.00,  -- Total des heures du mois
    total_late_minutes INT DEFAULT 0,              -- Cumul des retards en minutes
    absent_days_count INT DEFAULT 0,               -- Nombre de jours d'absence
    present_days_count INT DEFAULT 0,              -- Nombre de jours de présence
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unique_employee_month UNIQUE (employee_id, year_month)
);