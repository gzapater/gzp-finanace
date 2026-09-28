# HANDOVER — gzp-finanace

## Objetivo de la V0

Construir una aplicación web sencilla para registrar y clasificar finanzas personales a partir de extractos bancarios.

Scope de esta V0:
- Importar extractos de Trade Republic, CaixaBank y MyInvestor.
- Normalizar transacciones a un esquema común.
- Aplicar reglas deterministas primero.
- Aplicar ML solo a campos no resueltos por reglas.
- Mostrar una pantalla de revisión para transacciones no resueltas o de baja confianza.
- Permitir al usuario corregir/confirmar clasificaciones.
- Guardar correcciones como nuevos ejemplos de entrenamiento.
- Permitir crear reglas deterministas confirmadas explícitamente por el usuario.
- Proponer nuevas reglas cuando el histórico muestre suficiente repetición/consistencia.
- Persistencia en PostgreSQL.
- Docker/Compose para despliegue en la VPS del usuario.

No ampliar todavía a dashboards complejos, presupuestos, patrimonio, forecasting ni reporting avanzado.

## Seguridad y datos privados

El repositorio es público.

NUNCA subir:
- extractos bancarios;
- dataset histórico;
- reglas privadas generadas;
- credenciales;
- DATABASE_URL;
- claves SSH;
- backups de PostgreSQL;
- archivos con IBAN, nombres, conceptos o transacciones reales.

Los datos reales deben vivir en data/private/ o en PostgreSQL y estar ignorados por Git.

## Estado actual del repo

Ya existe:
- src/gzp_finance/rules.py
- scripts/generate_rules.py
- tests/test_rules.py
- .gitignore
- pyproject.toml

El motor de reglas soporta:
- concept_key normalizado;
- banco;
- tipo bancario;
- categoría bancaria;
- dirección;
- importe exacto en céntimos;
- rango de importe;
- counterparty_iban;
- prioridades;
- reglas que rellenan solo algunos campos;
- reglas confirmadas con prioridad superior al histórico.

## Pipeline deseado

bank file
  -> importer
  -> normalized transaction
  -> deterministic rules
  -> ML for unresolved fields
  -> confidence thresholds
  -> review UI
  -> confirmed classification
  -> training history
  -> candidate rules / confirmed rules

Las reglas siempre se ejecutan antes que ML.

Una regla puede resolver solo algunos campos. ML completa únicamente los restantes.

## Campos objetivo

La clasificación histórica usa, como mínimo:

- target_tipo_transaccion
- target_tipo_gasto
- target_fiscalidad
- target_categoria_general
- target_subtipo
- target_activo
- target_detalle
- target_detalle2

No asumir que todos deben rellenarse siempre. La app debe permitir clasificaciones parciales cuando el histórico no soporta más detalle.

## Tipos de reglas

### 1. Reglas estructurales

Definidas por código o configuración.

Ejemplo:
- transferencias entre cuentas propias -> movimiento entre cuentas.

### 2. Reglas aprendidas automáticamente

Se generan a partir del histórico.

Política conservadora actual:
- mínimo 3 apariciones;
- 100% de consistencia histórica para el campo que se quiere rellenar;
- por defecto solo usar ejemplos históricos de match_confidence=Alta.

Una regla puede ser determinista para categoria_general y subtipo, pero no para activo/detalle.

### 3. Reglas confirmadas por usuario

El usuario puede convertir una clasificación en regla directamente.

Estas tienen prioridad sobre reglas aprendidas del histórico.

Casos ya definidos por el usuario:
- CaixaBank: PAG NOMINAS + -400 EUR -> transferencia propia Caixa -> MyInvestor.
- CaixaBank: PAG NOMINAS + -825 EUR -> gasto fijo / casa / alquiler.
- Trade Republic: Bitcoin + BUY -> inversión / cripto / Bitcoin.
- Trade Republic: compra recurrente Bitcoin alrededor de 200 EUR -> Plan de ahorro bitcoin.
- Trade Republic: Physical Gold USD (Acc) + BUY -> inversión / ETF oro / Plan de ahorro Oro.
- Trade Republic: transferencia entrante desde IBAN propio de Caixa -> movimiento de cuentas / Caixa -> Trade Republic.
- Trade Republic: REPSOL WAYLET -> Transporte / Coche / Gasolina.
- Trade Republic: TPV FUENCARRAL -> Transporte / Coche / Gasolina.
- MyInvestor: ingreso recurrente de 400 EUR desde Caixa -> movimiento de cuentas / Caixa -> MyInvestor.
- MyInvestor: compras de fondos conocidos -> inversión + activo correspondiente.

Importante: el usuario ha identificado errores puntuales en el histórico que hacían parecer ambiguos Bitcoin/Oro. Las reglas confirmadas deben prevalecer sobre esos errores históricos.

## Reglas vs ML

Reglas:
- alta precisión;
- explicables;
- tienen prioridad;
- se usan para conceptos/activos/transferencias recurrentes.

ML:
- resuelve conceptos nuevos o variables;
- nunca debe sobrescribir un campo fijado por una regla;
- debe devolver predicción + confianza por campo;
- sus predicciones no se incorporan al entrenamiento sin confirmación del usuario.

## Aprendizaje desde la app

Cuando el usuario corrige o confirma una transacción, guardar:
- input bancario original;
- transacción normalizada;
- reglas aplicadas;
- predicción ML y confianza;
- clasificación final confirmada;
- timestamp;
- versión del modelo;
- si el ejemplo es apto para entrenamiento.

El modelo NO se reentrena tras cada corrección.

Acumular ejemplos confirmados y reentrenar en batch/manual durante esta V0.

La app debe permitir:
- guardar solo esta clasificación;
- crear regla para concepto;
- crear regla concepto + importe;
- crear regla personalizada;
- marcar que una clasificación se use para entrenamiento.

También debe poder proponer:
'Este concepto se ha clasificado igual N veces. ¿Convertir en regla automática?'

## Evaluación ML

El histórico original sirve para entrenamiento.

El periodo mayo-septiembre 2026 debe reservarse como test realista cuando sea posible.

Métrica principal de producto:
'De 100 transacciones nuevas, ¿cuántas puede aceptar el usuario sin tocar nada?'

No optimizar solo accuracy académica.

## Dataset existente

Existe un dataset consolidado generado previamente con:
- 1.087 ejemplos utilizables;
- 1.015 de confianza alta;
- 72 de confianza media;
- 6 excluidos;
- bancos: Trade Republic, CaixaBank, MyInvestor.

El dataset separa inputs bancarios de targets manuales.

El fichero privado se entregará aparte y NO debe versionarse.

## Resultados de reglas observados

Antes de reglas confirmadas adicionales:
- Trade Republic mayo-septiembre: 198 movimientos; 85 con alguna regla; 41 con Tipo+Categoria+Subtipo.
- CaixaBank mayo-septiembre: 48 movimientos; 38 con regla completa principal.
- MyInvestor mayo-septiembre: 20 movimientos; 10 con regla completa principal.

Tras añadir reglas confirmadas para Trade Republic, el número de movimientos con Tipo+Categoria+Subtipo completos subió aproximadamente de 41 a 81 en ese periodo.

## Importadores

### Trade Republic

Formato CSV.

Debe soportar:
- fecha;
- datetime;
- type;
- category;
- asset_class;
- name;
- symbol;
- amount;
- fee;
- tax;
- description;
- counterparty_name;
- counterparty_iban;
- payment_reference;
- mcc_code;
- transaction_id.

Para BUY/SELL, Trade Republic puede dividir una orden en varios fills muy próximos. El preprocesado histórico agrupó fills del mismo activo/tipo/fecha separados <=2 segundos y usó:
amount_net = sum(amount + fee + tax).

### CaixaBank

Export actual: SpreadsheetML/XML de Excel.

Campos útiles:
- Fecha
- Concepto
- Categoría
- Importe
- Tipo Movimiento
- Cuenta/Tarjeta

El concepto 'PAG NOMINAS' es ambiguo sin importe, por eso se requieren reglas concept+amount.

### MyInvestor

CSV.

Uso actual muy simple:
- recibir 400 EUR desde Caixa;
- invertir en dos fondos.

El usuario cambió recientemente de Fidelity a fondos iShares, por lo que los nombres nuevos pueden no existir en el histórico. Una sola confirmación explícita del usuario debe poder crear una regla determinista para un activo específico.

## Modelo ML inicial sugerido

No empezar con deep learning.

Baseline:
- TF-IDF de texto (word + char n-grams);
- Logistic Regression o LinearSVC con calibración si se necesita probabilidad;
- features adicionales: banco, tipo, categoría bancaria, dirección, importe/rango, MCC;
- modelos separados o jerárquicos por target.

Recomendación:
1. tipo_transaccion
2. categoria_general
3. subtipo
4. activo
5. detalle

Evaluar por campo.

No permitir que ML rellene automáticamente campos con confianza insuficiente.

## PostgreSQL — esquema inicial sugerido

### imports
- id
- source_bank
- filename
- imported_at
- file_hash
- status

### transactions
- id
- import_id
- source_bank
- bank_date
- value_date
- amount_eur
- direction
- concept_raw
- description_raw
- bank_category
- bank_type
- account_ref
- counterparty_name
- counterparty_iban
- payment_reference
- mcc
- transaction_id
- concept_key
- raw_payload jsonb
- created_at

### classifications
- transaction_id
- tipo_transaccion
- tipo_gasto
- fiscalidad
- categoria_general
- subtipo
- activo
- detalle
- detalle2
- confirmed_by_user
- confirmed_at

### predictions
- transaction_id
- engine: rule|ml
- field
- predicted_value
- confidence
- rule_id nullable
- model_version nullable
- created_at

### rules
- id
- kind
- priority
- source: structural|learned|user_confirmed
- match jsonb
- set_values jsonb
- support
- historical_consistency
- enabled
- created_at

### training_examples
- transaction_id
- classification_id
- enabled
- source
- added_at

### model_versions
- id
- created_at
- training_example_count
- metrics jsonb
- artifact_path
- active

Puede simplificarse para la V0 si hace falta, pero conservar estas responsabilidades.

## UI V0

Una sola app web sencilla.

Pantallas mínimas:

### Importar
- selector de banco;
- upload de archivo;
- resultado de importación;
- detectar duplicados.

### Revisar
Tabla de transacciones con:
- fecha;
- concepto;
- importe;
- clasificación actual;
- origen de cada campo: regla / ML / manual;
- confianza;
- estado: completa / revisar / sin clasificar.

Permitir editar campos.

Al guardar:
- guardar clasificación;
- marcar como training example;
- opcionalmente crear regla.

### Reglas
Listado simple:
- match;
- valores;
- origen;
- soporte;
- activa/inactiva.

No hace falta editor avanzado todavía.

## Duplicados

No duplicar transacciones si el mismo extracto se carga dos veces.

Usar transaction_id cuando exista.

Fallback de huella:
source_bank + fecha + importe + concepto normalizado + datos bancarios relevantes.

## VPS / despliegue

La VPS ya aloja otros servicios y dispone de PostgreSQL.

Preparar:
- Dockerfile;
- docker-compose.yml;
- variables por .env;
- migrations;
- healthcheck;
- README de despliegue.

No asumir acceso público directo a PostgreSQL.

No hardcodear host, password ni credenciales.

## Criterios de aceptación V0

1. Importar correctamente los tres formatos reales entregados.
2. Persistir transacciones en PostgreSQL.
3. No duplicar importaciones.
4. Ejecutar reglas deterministas y mostrar su procedencia.
5. Ejecutar ML solo sobre campos sin resolver.
6. Permitir corrección manual.
7. Guardar correcciones para futuros entrenamientos.
8. Permitir crear regla determinista desde la UI.
9. Proponer regla cuando el histórico sea repetitivo y consistente.
10. Mantener todos los datos reales fuera del repositorio público.
11. Incluir tests para importadores, reglas y pipeline.
12. Ejecutable localmente con Docker Compose.

## Prioridad de implementación

1. DB + migrations
2. importadores
3. persistencia + deduplicación
4. motor de reglas integrado
5. UI de revisión
6. reglas confirmadas desde UI
7. training_examples
8. baseline ML
9. Docker/deploy VPS

No ampliar scope hasta que este flujo esté funcionando con los extractos reales.
