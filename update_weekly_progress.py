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
    'EDV': 'keno',   # Eugenio del Río
    'DFA': 'diego',  # Diego Ferrario
    'VPC': 'vale',   # Valentina Plaza
    'VGP': 'valen',  # Valentina Giacchino
    'JDT': 'javi',   # Javiera Donoso
    'BAV': 'basti',  # Bastián Araya
    'CJV': 'chris',  # Christian Jungmann
}


def get_client_map():
    """Obtiene el mapeo de clientes desde JSONBin."""
    r = requests.get(
        f"{JSONBIN_URL}/latest",
        headers={"X-Access-Key": JSONBIN_ACCESS, "X-Bin-Meta": "false"},
        timeout=15
    )
    r.raise_for_status()
    state = r.json()
    
    # Construir mapeo nombre SQL → client ID desde el JSONBin
    client_map = {}
    for client in state.get('clients', []):
        client_map[client['name']] = client['id']
    
    print(f"Cargados {len(client_map)} clientes desde JSONBin")
    return client_map


def query_sql_progress(client_map):
    """Consulta horas trabajadas del mes actual completo."""
    today = date.today()
    first_day = today.replace(day=1)
    last_day = date(today.year, today.month + 1, 1) if today.month < 12 else date(today.year + 1, 1, 1)
    last_day = (last_day - relativedelta(days=1))  # último día del mes
    
    fecha_desde = int(first_day.strftime('%Y%m%d'))
    fecha_hasta = int(last_day.strftime('%Y%m%d'))
    
    print(f"Consultando horas trabajadas de todo el mes: {first_day} hasta {last_day}")

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
        new_clients = set()
        
        for cliente_nombre, analista_inicial, horas in cursor.fetchall():
            client_id = client_map.get(cliente_nombre)
            analyst_id = ANALYST_MAP.get(analista_inicial)
            
            if not client_id:
                # Cliente nuevo descubierto
                new_clients.add(cliente_nombre)
                print(f"⚠ Cliente nuevo descubierto: {cliente_nombre}")
            elif not analyst_id:
                print(f"⚠ Analista desconocido: {analista_inicial}")
            
            if client_id and analyst_id:
                key = (client_id, analyst_id)
                progress[key] = progress.get(key, 0) + float(horas)
        
        return progress, new_clients
    finally:
        conn.close()


def update_jsonbin_progress(progress, new_clients_from_sql):
    """Actualiza el JSONBin con el progreso semanal y nuevos clientes."""
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
    
    # Agregar nuevos clientes descubiertos
    existing_clients = {c['name']: c['id'] for c in state.get('clients', [])}
    next_id = max([int(c['id'][1:]) for c in state.get('clients', [])], default=0) + 1
    
    new_count = 0
    for client_name in new_clients_from_sql:
        if client_name not in existing_clients:
            new_id = f"c{next_id:02d}"
            state['clients'].append({
                "id": new_id,
                "name": client_name,
                "avgHrs": 0,
                "autoAdded": True,
                "addedDate": date.today().isoformat()
            })
            print(f"  + Nuevo cliente agregado: {client_name} → {new_id}")
            next_id += 1
            new_count += 1
    
    if new_count > 0:
        print(f"✓ {new_count} clientes nuevos agregados al JSONBin")
    else:
        print("✓ No hay clientes nuevos para agregar")

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
    print("✓ Progreso semanal y clientes actualizados en JSONBin")


def main():
    try:
        client_map = get_client_map()
        progress, new_clients = query_sql_progress(client_map)
        print(f"Recibidos {len(progress)} registros de horas trabajadas")
        update_jsonbin_progress(progress, new_clients)
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
