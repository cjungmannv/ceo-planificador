#!/usr/bin/env python3
"""
Actualiza promedios de horas en JSONBin consultando el SQL de Jira.
Se ejecuta el 1ro de cada mes via GitHub Actions.
"""

import os
import sys
import json
from datetime import date
from dateutil.relativedelta import relativedelta
import mysql.connector
import requests

# ── CONFIG (se leen de variables de entorno / GitHub Secrets) ──
SQL_SERVER   = os.environ['SQL_SERVER']      # dw-masanalytics.mysql.database.azure.com
SQL_DATABASE = os.environ['SQL_DATABASE']    # dw_proyectos
SQL_USER     = os.environ['SQL_USER']
SQL_PASSWORD = os.environ['SQL_PASSWORD']

JSONBIN_ID     = os.environ['JSONBIN_ID']
JSONBIN_MASTER = os.environ['JSONBIN_MASTER_KEY']
JSONBIN_ACCESS = os.environ['JSONBIN_ACCESS_KEY']

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

# ── MAPEO de IDs SQL → IDs app ──
# Ajustar según los identificadores reales en SQL.
# Si en SQL los clientes se identifican por nombre exacto, usar {nombre_sql: id_app}
CLIENT_MAP = {
    "Constructora Rio Cochrane": "c01",
    "Inmobiliaria PY":           "c02",
    "Inmobiliaria PY":           "c03",  # Family Office - mismo cliente en SQL
    "BBosch":                    "c04",
    "Head":                      "c05",
    "Volcan":                    "c06",   # Keno (parte de compartido)
    "Codelpa":                   "c07",   # Keno (parte de compartido)
    "FGMM":                      "c08",   # Keno (parte de compartido)
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
    "Tecnika":                   "c33",  # ⚠ No está en SQL, posible Proteknica?
    "Saval":                     "c34",  # ⚠ No está en SQL
    "Sigro":                     "c35",  # ⚠ No está en SQL
    "Amesti":                    "c36",
    "Agrosystem":                "c37",
    "Agricola Sutil":            "c38",
    "Kersting":                  "c39",
    "Emin":                      "c40",
    "Davita":                    "c41",
    "Agricola Maria Pinto":      "c42",
    "Santolaya":                 "c43",  # ⚠ No está en SQL
}

# Clientes compartidos: pares de IDs (el % se lee del JSONBin)
SHARED_CLIENTS = {
    "Volcan":  ["c06", "c06b"],     # Keno/Diego
    "Codelpa": ["c07", "c07b"],     # Keno/Valen
    "FGMM":    ["c08", "c08b"],     # Keno/Chris
}


def get_date_range():
    """Devuelve (desde, hasta) para los últimos 3 meses completos."""
    today = date.today()
    desde = (today - relativedelta(months=3)).replace(day=1)
    hasta = today.replace(day=1) - relativedelta(days=1)
    return desde, hasta


def query_sql():
    """Consulta el total de horas por cliente en los últimos 3 meses."""
    desde, hasta = get_date_range()
    print(f"Consultando horas entre {desde} y {hasta}")

    # Convertir fechas a formato YYYYMMDD (int)
    fecha_desde = int(desde.strftime('%Y%m%d'))
    fecha_hasta = int(hasta.strftime('%Y%m%d'))

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
                SUM(h.horasdedicadas) as total_horas
            FROM thregistrohoras h
            JOIN modelo m ON h.idmodelo = m.idmodelo
            WHERE h.idfecha BETWEEN %s AND %s
              AND h.escenario = 'Registro Horas'
              AND m.estado IN ('En Ejecución', 'Cerrado')
              AND (
                  m.cliente NOT IN ('Codelpa', 'Volcan', 'FGMM', 'Elecmetal', 'Sevilla', 'Stars Investment')
                  OR (m.cliente = 'Codelpa' AND m.proyecto LIKE '%Paquete 30 hrs mensuales CS%')
                  OR (m.cliente = 'Volcan' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'FGMM' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'Elecmetal' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'Sevilla' AND m.estado = 'En Ejecución')
                  OR (m.cliente = 'Stars Investment' AND m.estado = 'En Ejecución')
              )
            GROUP BY m.cliente
        """
        cursor.execute(query, (fecha_desde, fecha_hasta))
        result = {row[0]: float(row[1]) for row in cursor.fetchall()}
        return result
    finally:
        conn.close()


def calculate_averages(sql_data, state):
    """Convierte totales de 3 meses a promedios mensuales por cliente."""
    averages = {}
    
    for cliente_nombre, total in sql_data.items():
        promedio = round(total / 3, 1)
        
        # DEBUG
        if "Codelpa" in cliente_nombre:
            print(f"DEBUG Codelpa: total={total}, promedio={promedio}")

        if cliente_nombre in SHARED_CLIENTS:
            # Para clientes compartidos, guardar el TOTAL en ambos IDs
            # La app se encarga de aplicar el sharePercent al mostrar
            client_ids = SHARED_CLIENTS[cliente_nombre]
            for client_id in client_ids:
                averages[client_id] = promedio
                print(f"DEBUG {cliente_nombre} → {client_id}: {promedio}h (total sin repartir)")
        elif cliente_nombre in CLIENT_MAP:
            averages[CLIENT_MAP[cliente_nombre]] = promedio
        else:
            print(f"⚠ Cliente desconocido en SQL: {cliente_nombre}")

    return averages


def update_jsonbin(averages):
    """Lee el bin actual, actualiza promedios y guarda."""
    # Leer estado actual
    r = requests.get(
        f"{JSONBIN_URL}/latest",
        headers={"X-Access-Key": JSONBIN_ACCESS, "X-Bin-Meta": "false"},
        timeout=15
    )
    r.raise_for_status()
    state = r.json()

    if "clients" not in state:
        print("⚠ El bin no tiene 'clients' — abortando")
        return

    # Actualizar horas por cliente
    updated = 0
    for client in state["clients"]:
        if client["id"] in averages:
            new_hrs = averages[client["id"]]
            if client["type"] == "monthly":
                client["monthlyHrs"] = new_hrs
            else:
                client["avgHrs"] = new_hrs
            updated += 1

    print(f"Actualizando {updated} clientes en JSONBin")

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
    print("✓ JSONBin actualizado correctamente")


def main():
    try:
        # Primero leer el estado actual para obtener sharePercent
        r = requests.get(
            f"{JSONBIN_URL}/latest",
            headers={"X-Access-Key": JSONBIN_ACCESS, "X-Bin-Meta": "false"},
            timeout=15
        )
        r.raise_for_status()
        state = r.json()
        
        sql_data = query_sql()
        print(f"Recibidos {len(sql_data)} clientes del SQL")
        averages = calculate_averages(sql_data, state)
        update_jsonbin(averages)
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()