#!/usr/bin/env python3
"""
Actualiza promedios de horas Y horas por contrato en JSONBin consultando SQL.
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
SQL_SERVER   = os.environ['SQL_SERVER']
SQL_DATABASE = os.environ['SQL_DATABASE']
SQL_USER     = os.environ['SQL_USER']
SQL_PASSWORD = os.environ['SQL_PASSWORD']

JSONBIN_ID     = os.environ['JSONBIN_ID']
JSONBIN_MASTER = os.environ['JSONBIN_MASTER_KEY']
JSONBIN_ACCESS = os.environ['JSONBIN_ACCESS_KEY']

JSONBIN_URL = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"

# Analistas del equipo CE&O (para filtrar horas)
ANALYSTS_CEO = ['CJV', 'EDV', 'DFA', 'VPC', 'JDT', 'BAV']


def infer_type(tipo, modalidad):
    """
    Infiere el tipo de cliente basado en tipo y modalidad de SQL.
    
    Reglas:
    - tipo = "mantencion" → "monthly" (Mantención)
    - tipo = "proyecto" + modalidad contiene "paquete" → "package"
    - todo lo demás → "other"
    """
    tipo_lower = (tipo or '').lower().strip()
    modalidad_lower = (modalidad or '').lower().strip()
    
    if tipo_lower == 'mantencion':
        return 'monthly'
    elif tipo_lower == 'proyecto' and 'paquete' in modalidad_lower:
        return 'package'
    else:
        return 'other'


def get_date_range():
    """Devuelve (desde, hasta) para los últimos 3 meses completos."""
    today = date.today()
    desde = (today - relativedelta(months=3)).replace(day=1)
    hasta = today.replace(day=1) - relativedelta(days=1)
    return desde, hasta


def query_client_data():
    """
    Consulta SQL para obtener:
    - Lista completa de clientes
    - Total horas trabajadas últimos 3 meses
    - Horas por contrato (horas_originales)
    - Tipo y modalidad para inferir category
    
    Retorna: dict {cliente_nombre: {'total_horas': float, 'contract_hrs': float, 'tipo': str, 'modalidad': str}}
    """
    desde, hasta = get_date_range()
    print(f"Consultando horas entre {desde} y {hasta}")

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
                SUM(h.horasdedicadas) as total_horas,
                MAX(m.horas_originales) as horas_contrato,
                MAX(m.tipo) as tipo,
                MAX(m.modalidad) as modalidad
            FROM thregistrohoras h
            JOIN modelo m ON h.idmodelo = m.idmodelo
            JOIN mas_adminfinanzas.colaborador c ON h.idcolaborador = c.idcolaborador
            WHERE h.idfecha BETWEEN %s AND %s
              AND h.escenario = 'Registro Horas'
              AND c.abreviado IN ('CJV', 'EDV', 'DFA', 'VPC', 'JDT', 'BAV')
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
        
        result = {}
        for row in cursor.fetchall():
            cliente = row[0]
            total_horas = float(row[1]) if row[1] else 0
            contract_hrs = float(row[2]) if row[2] else None
            tipo = row[3] if row[3] else ''
            modalidad = row[4] if row[4] else ''
            
            result[cliente] = {
                'total_horas': total_horas,
                'contract_hrs': contract_hrs,
                'tipo': tipo,
                'modalidad': modalidad
            }
        
        return result
    finally:
        conn.close()


def find_or_create_client(state, cliente_nombre):
    """
    Busca un cliente en el state por nombre.
    Si no existe, lo crea con autoAdded=True.
    Retorna el objeto cliente.
    """
    # Normalizar nombre para búsqueda
    nombre_norm = cliente_nombre.strip()
    
    # Buscar existente
    for client in state['clients']:
        if client.get('name', '').strip() == nombre_norm:
            return client
    
    # No existe → crear nuevo
    # Generar ID: encontrar el máximo cXXX y sumar 1
    max_id = 0
    for client in state['clients']:
        cid = client.get('id', '')
        if cid.startswith('c'):
            try:
                num = int(cid[1:])
                max_id = max(max_id, num)
            except:
                pass
    
    new_id = f"c{max_id + 1}"
    new_client = {
        'id': new_id,
        'name': cliente_nombre,
        'avgHrs': 0,
        'autoAdded': True,
        'addedDate': date.today().strftime('%Y-%m-%d')
    }
    
    state['clients'].append(new_client)
    print(f"✨ Cliente nuevo detectado: {cliente_nombre} → {new_id}")
    return new_client


def update_jsonbin(sql_data):
    """
    Lee el bin actual, actualiza:
    - avgHrs/monthlyHrs (promedio 3 meses)
    - contractHrs (desde horas_originales)
    - type (inferido desde tipo/modalidad)
    - Crea clientes nuevos si no existen
    """
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

    updated = 0
    
    for cliente_nombre, data in sql_data.items():
        # Encontrar o crear cliente
        client = find_or_create_client(state, cliente_nombre)
        
        # Calcular promedio mensual
        promedio = round(data['total_horas'] / 3, 1)
        
        # Inferir tipo desde SQL
        inferred_type = infer_type(data['tipo'], data['modalidad'])
        
        # Actualizar horas trabajadas
        if inferred_type == 'monthly':
            client['monthlyHrs'] = promedio
        else:
            client['avgHrs'] = promedio
        
        # Actualizar tipo si no existe o si viene de SQL
        if not client.get('type') or client.get('autoAdded'):
            client['type'] = inferred_type
        
        # Actualizar horas por contrato
        if data['contract_hrs'] is not None:
            client['contractHrs'] = data['contract_hrs']
        
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
        sql_data = query_client_data()
        print(f"Recibidos {len(sql_data)} clientes del SQL")
        
        # Mostrar algunos ejemplos
        for cliente, data in list(sql_data.items())[:3]:
            print(f"  {cliente}: {data['total_horas']}h total, contrato: {data['contract_hrs']}h")
        
        update_jsonbin(sql_data)
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
