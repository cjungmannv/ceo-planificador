#!/usr/bin/env python3
"""
Actualiza el avance semanal de horas trabajadas.
Se ejecuta todos los lunes vía GitHub Actions.
"""

import os
import sys
from datetime import date
from dateutil.relativedelta import relativedelta
import mysql.connector
import requests

# ── CONFIG ──
SQL_SERVER   = os.environ['SQL_SERVER']
SQL_DATABASE = os.environ['SQL_DATABASE']
SQL_USER     = os.environ['SQL_USER']
SQL_PASSWORD = os.environ['SQL_PASSWORD']

JSONBIN_ID     = os.environ['JSONBIN_ID']
JSONBIN_MASTER = os.environ['JSONBIN_MASTER_KEY']
JSONBIN_ACCESS = os.environ['JSONBIN_ACCESS_KEY']

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

# Mapeo de iniciales SQL → IDs app
ANALYST_MAP = {
    'EDR': 'keno',   # Eugenio del Río
    'DFA': 'diego',  # Diego Ferrario
    'VPC': 'vale',   # Valentina Plaza
    'VGP': 'valen',  # Valentina Giacchino
    'JDT': 'javi',   # Javiera Donoso
    'BAV': 'basti',  # Bastián Araya
    'CJV': 'chris',  # Christian Jungmann
}

# Mapeo de nombres SQL → IDs app (mismo que el script mensual)
CLIENT_MAP = {
    "Constructora Rio Cochrane": "c01",
    "Inmobiliaria PY":           "c02",
    "Inmobiliaria PY":           "c03",
    "BBosch":                    "c04",
    "Head":                      "c05",
    "Volcan":                    "c06",
    "Codelpa":                   "c07",
    "FGMM":                      "c08",
    "Pesco":                     "c09",
    "Stars Investment":          "c10",
    "Republica Austral":         "c11",
    "Creado en Chile":           "c12",
    "Hemisur":                   "c13",
    "BBS":                       "c14",
    "Jedimar":                   "c15",
    "Almaviva":                  "c16",
    "Sevilla":                   "c17",
    "Elecmetal":                 "c18",
    "Viña Montes":               "c19",
    "Amicar":                    "c20",
    "LFE":                       "c21",
    "Bodenor Flexcenter":        "c22",
    "Cruceros Australis":        "c23",
    "Lounge":                    "c25",
    "Betterplan":                "c26",
    "Summit Agro":               "c27",
    "AFE":                       "c28",
    "Agricola Bulnes":           "c29",
    "MaxiK":                     "c30",
    "CASBRO":                    "c31",
    "Montgras":                  "c32",
    "Amesti":                    "c36",
    "Agrosystem":                "c37",
    "Agricola Sutil":            "c38",
    "Kersting":                  "c39",
    "Emin":                      "c40",
    "Davita":                    "c41",
    "Agricola Maria Pinto":      "c42",
}


def query_sql_progress():
    """Consulta horas trabajadas del mes actual hasta hoy."""
    today = date.today()
    first_day = today.replace(day=1)
    
    fecha_desde = int(first_day.strftime('%Y%m%d'))
    fecha_hasta = int(today.strftime('%Y%m%d'))
    
    print(f"Consultando horas trabajadas desde {first_day} hasta {today}")

    conn = mysql.connector.connect(
        host=SQL_SERVER,
        database=SQL_DATABASE,
        user=SQL_USER,
        password=SQL_PASSWORD,
        ssl_disabled=False
    )

    try:
        cursor = conn.cursor()
        query = """
            SELECT 
                m.cliente,
                c.abreviado as analista_inicial,
                SUM(h.horasdedicadas) as horas_trabajadas
            FROM thregistrohoras h
            JOIN modelo m ON h.idmodelo = m.idmodelo
            JOIN mas_adminfinanzas.colaborador c ON h.idcolaborador = c.idcolaborador
            WHERE h.idfecha BETWEEN %s AND %s
              AND h.escenario = 'Registro Horas'
              AND m.estado IN ('En Ejecución', 'Cerrado')
              AND (
                  m.cliente NOT IN ('Codelpa', 'Volcan', 'FGMM', 'Elecmetal', 'Sevilla', 'Stars Investment')
                  OR (m.cliente = 'Codelpa' AND m.proyecto LIKE '%%Paquete 30 hrs mensuales CS%%')
                  OR (m.cliente = 'Volcan' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'FGMM' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'Elecmetal' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'Sevilla' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'Stars Investment' AND m.estado = 'En Ejecución')
              )
            GROUP BY m.cliente, c.abreviado
        """
        cursor.execute(query, (fecha_desde, fecha_hasta))
        
        # Agrupar por cliente y analista
        progress = {}
        for cliente_nombre, analista_inicial, horas in cursor.fetchall():
            client_id = CLIENT_MAP.get(cliente_nombre)
            analyst_id = ANALYST_MAP.get(analista_inicial)
            
            if client_id and analyst_id:
                key = (client_id, analyst_id)
                progress[key] = progress.get(key, 0) + float(horas)
            elif not client_id:
                print(f"⚠ Cliente desconocido: {cliente_nombre}")
            elif not analyst_id:
                print(f"⚠ Analista desconocido: {analista_inicial}")
        
        return progress
    finally:
        conn.close()


def update_jsonbin_progress(progress):
    """Actualiza el JSONBin con el progreso semanal."""
    # Leer estado actual
    r = requests.get(
        f"{JSONBIN_URL}/latest",
        headers={"X-Access-Key": JSONBIN_ACCESS, "X-Bin-Meta": "false"},
        timeout=15
    )
    r.raise_for_status()
    state = r.json()

    # Obtener mes actual en formato YYYYMM
    current_month = date.today().strftime('%Y%m')
    
    # Crear estructura de progreso si no existe
    if "weeklyProgress" not in state:
        state["weeklyProgress"] = {}
    
    # Guardar progreso del mes actual
    state["weeklyProgress"][current_month] = {
        "date": date.today().isoformat(),
        "progress": {f"{cid}:{aid}": hrs for (cid, aid), hrs in progress.items()}
    }
    
    print(f"Actualizando progreso: {len(progress)} registros cliente-analista")

    # Guardar
    r = requests.put(
        JSONBIN_URL,
        headers={
            "Content-Type": "application/json",
            "X-Master-Key": JSONBIN_MASTER
        },
        json=state,
        timeout=15
    )
    r.raise_for_status()
    print("✓ Progreso semanal actualizado en JSONBin")


def main():
    try:
        progress = query_sql_progress()
        print(f"Recibidos {len(progress)} registros de horas trabajadas")
        update_jsonbin_progress(progress)
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
