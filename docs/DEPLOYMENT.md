# V0 en una VPS

La app tiene PostgreSQL propio. No necesita reutilizar las bases de otros servicios.
No publiques PostgreSQL en Internet. La web usa autenticación HTTP Basic: acceso por
túnel SSH o HTTPS mediante Caddy. No publiques la web por HTTP sin cifrar.

## Preparar e iniciar

Requiere Docker y Compose v2. Usa un directorio nuevo, propiedad del usuario SSH.

```bash
cp .env.example .env
chmod 600 .env
```

Genera dos contraseñas independientes (por ejemplo `openssl rand -hex 32`). Configura
POSTGRES_PASSWORD, DATABASE_URL, APP_USER y APP_PASSWORD en .env. DATABASE_URL usa
`db:5432` dentro de Compose. Nunca copies ese archivo al repositorio remoto.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8088/health
```

Las migraciones se ejecutan en un contenedor independiente antes de iniciar la app.
Los datos y modelos tienen volúmenes propios. No uses `down -v` para actualizar.
El healthcheck verifica conectividad y existencia de la tabla de migraciones.

## Acceso y administración

Desde el equipo del usuario:

```bash
ssh -i ~/.ssh/CLAVE -N -L 8088:127.0.0.1:8088 USUARIO@HOST
```

Abre http://127.0.0.1:8088 e introduce APP_USER / APP_PASSWORD. Para administrar la
base, ejecuta en la VPS, desde el directorio de la app:

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Si necesitas una herramienta SQL local, habilita el puerto de administración:

```bash
docker compose -f docker-compose.yml -f compose.admin.yml up -d
```

Y abre otro túnel desde el equipo local:

```bash
ssh -i ~/.ssh/CLAVE -N -L 55433:127.0.0.1:55433 USUARIO@HOST
```

Conecta la herramienta a 127.0.0.1:55433 usando las credenciales privadas. Un rol
administrativo puede crear después un usuario SQL con permisos de solo lectura.

## Datos privados y reglas existentes

No incluyas extractos, reglas ni modelos en el contexto de construcción. Para cargar
un archivo privado, cópialo temporalmente al contenedor, carga y retíralo. Guarda las
copias originales en un directorio privado fuera del checkout, con permisos 700/600.

```bash
docker compose cp /RUTA_PRIVADA/generated_rules_private.json app:/tmp/generated.json
docker compose exec app gzp-finance load-rules /tmp/generated.json --source learned
docker compose exec app rm /tmp/generated.json
docker compose cp /RUTA_PRIVADA/confirmed_rules_private.json app:/tmp/confirmed.json
docker compose exec app gzp-finance load-rules /tmp/confirmed.json --source user_confirmed
docker compose exec app rm /tmp/confirmed.json
docker compose exec app gzp-finance reclassify
```

Importa los extractos desde la web. Las transacciones y las confirmaciones residen
solo en PostgreSQL. Las confirmaciones manuales no se sobrescriben al cambiar reglas
o activar un modelo.

## Histórico original y validación por banco

El Excel etiquetado se conserva en `historical_records`, separado del libro bancario.
No crea movimientos ni confirmaciones ni ejemplos de entrenamiento automáticamente.
Los ejemplos para ML siguen pasando por `seed-history`, que acepta confianza Alta.

Extrae la hoja de movimientos a un JSON privado con `filename`, `sheet` y `records`.
Cada registro conserva `source_row` (fila original, contando la cabecera), `source_bank`,
`manual_date` ISO, `amount_eur` decimal como texto (null si falta) y `values` con
los campos `target_*`. Canonicaliza únicamente los alias de los tres bancos soportados;
conserva los demás bancos como referencia. No cambies las etiquetas originales.
Carga ese JSON y el CSV de cruce original fuera del contexto de Docker:

```bash
gzp-finance load-history-reference /RUTA_PRIVADA/history.json --dataset /RUTA_PRIVADA/dataset.csv
```

El vínculo usa identificadores bancarios/componentes cuando existen; en los demás
casos requiere un único movimiento con el mismo banco, fecha bancaria, importe,
concepto normalizado y cuenta si consta. Los cruces ambiguos quedan pendientes.
Las siguientes importaciones vuelven a buscar referencias pendientes.
La recarga del mismo JSON es idempotente y se rechaza mezclar otra versión del Excel.

En Revisar, filtra por nuevos/sin etiqueta, vinculados al histórico o todos.
Histórico Excel conserva las etiquetas y la confianza del cruce. Validación muestra
recuentos, entradas, salidas y neto por banco; compara importes solo para vínculos
uno a uno, con diferencias por fila, entradas, salidas y neto. Los fills agrupados,
filas sin importe y referencias sin vínculo quedan fuera de esa comparación y se
cuentan explícitamente. Una coincidencia parcial no valida el histórico completo.
Estas sumas son movimientos, no saldos de cuenta.

## Histórico y baseline ML (batch explícito)

```bash
docker compose cp /RUTA_PRIVADA/Dataset_entrenamiento_finanzas.csv app:/tmp/history.csv
docker compose exec app gzp-finance seed-history /tmp/history.csv
docker compose exec app rm /tmp/history.csv
docker compose exec app gzp-finance train
docker compose exec app gzp-finance models
docker compose exec app gzp-finance activate-model VERSION_REVISADA
docker compose exec app gzp-finance reclassify
```

El seed acepta solo confianza Alta. No usa manual_date ni model_text. Los ejemplos
confirmados por el usuario solo se habilitan si marca la casilla de entrenamiento.
Cada corrección conserva un snapshot inmutable; una corrección posterior deshabilita
el anterior para entrenamiento. Guardar no reentrena el modelo.

El baseline usa TF-IDF word/char, banco, tipo, categoría, dirección, importe y MCC,
y regresión logística por campo. Se conserva el split train/validation del histórico.
Mayo-septiembre de 2026 queda reservado, aunque un ejemplo esté marcado train.
Los datos posteriores a septiembre pueden entrar en siguientes entrenamientos.
Las reglas tienen prioridad por campo, incluidos valores explícitamente vacíos.
ML de baja confianza es una sugerencia; no rellena la clasificación.

Los umbrales se eligen en validación (mínimo 5 predicciones aceptadas y 95% de acierto,
umbral no inferior a 0.85). Si no existe evidencia, ese campo solo propone. Estas
métricas son del ML y sirven para ajustar el baseline; no son un test independiente
ni miden aceptación humana. El periodo futuro sin etiquetas no permite calcular
cuántos movimientos acepta una persona sin editar: hay que registrar su revisión.
No conviertas predicciones sin confirmar en ejemplos o reglas.

## Caddy

Una vez decidido el dominio, añade un bloque separado al Caddyfile existente:

```caddyfile
finanzas.example.com {
    reverse_proxy host.docker.internal:8088
}
```

Ese destino solo funciona si el Caddy del host puede alcanzar el loopback de la VPS
(por ejemplo con red host). Para Caddy en una red bridge, conecta Caddy a la red de
esta app y usa `reverse_proxy app:8000` con un alias exclusivo, por ejemplo
`finance-app:8000`. No uses el nombre genérico app en una red compartida.
Valida antes de recargar y conserva los bloques actuales. No se cambia Caddy en la V0
sin decidir el dominio y comprobar su red real.

## Backup y restauración

Guarda backups fuera del checkout y con permisos 600; contienen datos financieros.

```bash
umask 077
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /RUTA_PRIVADA/finance.dump
```

Prueba la restauración en una base vacía separada; nunca sobre otra app ni la base
en uso. Copia también el volumen de modelos si hay un modelo activo; la BD referencia
su artefacto. Restaura con pg_restore y valida recuentos, migraciones y `/health`.

## Actualizaciones

Haz backup, revisa las migraciones y ejecuta `docker compose up -d --build`.
Para recuperar la versión anterior conserva su imagen y un backup previo compatible.
No reviertas una migración destructiva sobre la BD en uso.
