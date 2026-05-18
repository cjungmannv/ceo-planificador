# Actualizador automático de promedios

Script que lee horas trabajadas desde MS SQL y actualiza JSONBin con los promedios mensuales. Se ejecuta el día 1 de cada mes vía GitHub Actions.

## Setup inicial (una sola vez)

### 1. Crear un repo privado en GitHub
- Subir estos 3 archivos al repo:
  - `update_jsonbin.py`
  - `requirements.txt`
  - `.github/workflows/update-monthly.yml`

### 2. Configurar los GitHub Secrets
En el repo → **Settings → Secrets and variables → Actions → New repository secret**

Crear estos 7 secrets:

| Secret | Valor | Ejemplo |
|---|---|---|
| `SQL_SERVER` | dirección del servidor MySQL | `dw-masanalytics.mysql.database.azure.com` |
| `SQL_DATABASE` | nombre de la base | `dw_proyectos` |
| `SQL_USER` | usuario SQL (de solo lectura ideal) | `reportes_readonly` |
| `SQL_PASSWORD` | contraseña | `tu_password_seguro` |
| `JSONBIN_ID` | ID del bin | `69fcb701250b1311c3198e75` |
| `JSONBIN_MASTER_KEY` | tu master key de JSONBin | `$2a$10$...` |
| `JSONBIN_ACCESS_KEY` | tu access key de JSONBin | `$2a$10$...` |

### 3. Ajustar la query SQL
En `update_jsonbin.py`, función `query_sql()`. La query actual consulta:

```sql
SELECT 
    m.cliente,
    SUM(h.horasdedicadas) as total_horas
FROM thregistrohoras h
JOIN modelo m ON h.idmodelo = m.idmodelo
WHERE h.idfecha BETWEEN %s AND %s
  AND h.escenario = 'Registro Horas'
  AND m.estado IN ('En Ejecución', 'Cerrado')
GROUP BY m.cliente
```

**Estructura confirmada del MySQL en Azure**:
- Servidor: `dw-masanalytics.mysql.database.azure.com`
- Base de datos: `dw_proyectos`
- Tabla horas: `thregistrohoras` con columnas:
  - `idthregistrohoras`, `idcolaborador`, `idmodelo`, `idfecha` (int YYYYMMDD), `horasdedicadas` (decimal), `escenario`
- Tabla clientes: `modelo` con columnas:
  - `idmodelo`, `cliente` (nombre texto), `proyecto`, `estado`

Los estados válidos son: 'En Ejecución', 'Cerrado', 'Planificado', 'Detenido', 'Anteproyecto'.

Puedes ajustar el filtro de estados según cuáles quieras incluir. Por defecto incluye 'En Ejecución' y 'Cerrado' ya que ambos pueden tener horas trabajadas activas.

### 4. Ajustar el mapeo de nombres
En `update_jsonbin.py`, diccionario `CLIENT_MAP`. Verificar que los nombres en SQL coincidan exactamente con las claves. Si en SQL aparece "Constructora Rio Cochrane" (sin tilde), agregar ambas variantes o normalizar.

### 5. Probar manualmente
En el repo → **Actions → Update monthly averages from SQL → Run workflow**

Si funciona, deberías ver en los logs:
```
Consultando horas entre 2026-02-01 y 2026-04-30
Recibidos N clientes del SQL
Actualizando N clientes en JSONBin
✓ JSONBin actualizado correctamente
```

Y al recargar la app, los promedios estarán al día.

## Cuándo se ejecuta automáticamente
- **Día 1 de cada mes** a las 06:00 hora Chile (09:00 UTC)
- También se puede correr manualmente desde GitHub Actions cuando quieras

## Logs y errores
- Cada ejecución queda registrada en **Actions** del repo
- Si falla, GitHub te manda un email automáticamente
- Si el SQL no es accesible desde internet, hay que usar otra estrategia (ver siguiente sección)

## ¿Y si el SQL solo es accesible desde la red interna?

Tres opciones:

1. **Self-hosted runner**: ejecutar el GitHub Action en una máquina dentro de tu red. GitHub te da el ejecutable, lo instalas en un servidor interno.
2. **Azure Function**: la función corre dentro del mismo Vnet que el SQL.
3. **Cron en servidor interno**: ejecutar el script Python directamente con `cron` en una VM que ya tengas.

Las tres usan el mismo `update_jsonbin.py` — solo cambia dónde corre.
