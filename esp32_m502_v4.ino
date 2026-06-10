// =====================================================================
// M502 Robotic Arm — Firmware ESP32
// Servidor Modbus TCP multi-sesion (estilo PLC industrial)
//
// Inspirado en: Siemens S7-1200 (8 sesiones), Schneider M221 (8 sesiones)
// No utiliza ModbusIP_ESP8266 — usa FreeRTOS + WiFiServer
//
// Sesiones concurrentes : MAX_SESSIONS (configurable, default 8)
// Codigos de funcion soportados:
//   0x01 — Read Coils
//   0x03 — Read Holding Registers
//   0x05 — Write Single Coil
//   0x06 — Write Single Register
//   0x0F — Write Multiple Coils
//   0x10 — Write Multiple Registers
//   0x43 — MEI / Device Identification (read-only)
// =====================================================================

// ─── Librerias ─────────────────────────────────────────────────────────────
#include <WiFi.h>
#include <ESP32Servo.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>

// ─── Red ─────────────────────────────────────────────────────────────
const char* SSID     = "A05";
const char* PASSWORD = "12456789";

// ─── Pines ───────────────────────────────────────────────────────────
const int PIN_LED         = 2;
const int PINES_SERVOS[6] = {13, 12, 14, 27, 26, 33};

// ── Registros de Control Exclusivo (Exclusive Control Lock) ──────────
//   Similar a Siemens S7 "exclusive access" / Rockwell "ownership"
//
//   HREG 200  REG_LOCK_CMD   — Comando de lock:
//                               0x0000 = sin lock (libre)
//                               0xA5A5 = solicitar/mantener lock exclusivo
//                               0x0000 = liberar lock (escribir 0)
//
//   HREG 201  REG_LOCK_TOKEN — Token de sesion asignado por el PLC al
//                               conceder el lock (0 = sin lock activo).
//                               El cliente que tiene el lock debe leerlo
//                               y usarlo para identificarse.
//
//   HREG 202  REG_LOCK_TTL   — Tiempo de vida restante del lock en
//                               segundos (solo lectura). Si llega a 0
//                               el lock se libera automaticamente.
//
//   HREG 203  REG_LOCK_OWNER — ID de sesion Modbus que tiene el lock
//                               (solo lectura, 0xFF = sin duenio).
//
//   Logica:
//   - Si LOCK_CMD == 0xA5A5 y no hay lock activo → se concede el lock
//     a la sesion que lo solicitó. Token aleatorio asignado.
//   - Si LOCK_CMD == 0xA5A5 y hay lock activo de OTRA sesion →
//     excepcion Modbus 0x04 (Slave Device Failure) en escrituras
//     a registros 100-105 y coil 0.
//   - Si LOCK_CMD == 0x0000 y la sesion es la duenia → lock liberado.
//   - Lock timeout: LOCK_TTL_SECONDS sin renovar → auto-release.
//   - El lock se renueva automaticamente con cualquier escritura del
//     duenio (simula el heartbeat de PLCs Siemens).

// ─── Mapa de registros Modbus ─────────────────────────────────────────
//   COIL  0        → LED se controla como un coil
//   HREG  100-105  → Servos (Pinza, PinzaRot, Muneca, Codo, Hombro, Base) mediante registros
#define COIL_LED          0
#define REG_SERVO_BASE    100
#define NUM_SERVOS        6
#define NUM_COILS         8
#define NUM_HREGS         210   // 0-209

// Registros de control exclusivo
#define REG_LOCK_CMD      200
#define REG_LOCK_TOKEN    201
#define REG_LOCK_TTL      202
#define REG_LOCK_OWNER    203
#define LOCK_MAGIC        0xA5A5 // "Contraseña" para pedir prioridad
#define LOCK_TTL_SECONDS  15    // El lock dura maximo 15 segundos si no se renueva
#define LOCK_NONE         0xFF  // Sin dueño

// ─── Servidor Modbus TCP ──────────────────────────────────────────────
#define MODBUS_PORT     502
#define MAX_SESSIONS    8     // Sesiones al mismo tiempo
#define SESSION_TIMEOUT 30000 // Si un cliente no hace nada en 30 segundos, se desconecta

// ─── Identificacion de fabricante (0x43) ───────────────────────────
const char* MB_VENDOR_NAME  = "M502 Robotics";
const char* MB_PRODUCT_CODE = "ARM-6DOF-v2";
const char* MB_FIRMWARE_VER = "3.0.0";
const char* MB_PRODUCT_NAME = "M502 Robotic Arm ESP32";
const char* MB_MODEL_NAME   = "ARM-6DOF-ESP32";

// ─── Variables globales y estructuradas ──────────────────────────
SemaphoreHandle_t xRegMutex; // Sirve para evitar que 2 tareas escriban al mismo tiempo en la memoria (mutex)

bool     mbCoils[NUM_COILS]  = {false}; // Tablas de memoria del PLC (coil)
uint16_t mbHregs[NUM_HREGS]  = {0}; // Tablas de memoria del PLC (registers)

struct ExclusiveLock { // Estructura que guarda el estado actual del control exclusivo
    bool     active       = false;
    uint8_t  ownerSession = LOCK_NONE;  // ID de sesion Modbus dueña
    uint16_t token        = 0;          // token aleatorio
    uint32_t expiresAt    = 0;          // Tiempo de expiracion
} exclusiveLock;

// ─── Variables de servos y animacion ───────────────────────────────────────────────
Servo servos[NUM_SERVOS]; // Crea 6 objetos de tipo servo
float currentAngles[NUM_SERVOS]    = {90,90,90,90,90,90}; // Guarda actual posicion de cada servo
int   lastTargetAngles[NUM_SERVOS] = {90,90,90,90,90,90}; // Guarda la ultima posicion que se ordeno mover
const float VELOCIDAD_SERVO = 0.060f; // Velocidad de movimiento

bool     lastLedState   = false; // Guarda el ultimo estado del LED
uint32_t lastServoTime  = 0; // Guarda el ultimo momento en que se actualizaron los servos

// ─── HTTP + WebSocket ─────────────────────────────────────────────────
WebServer        httpServer(80); // Servidor web en puerto 80
WebSocketsServer wsServer(81); // Servidor WebSocket en el puerto 81 para aplicar los cambios
uint32_t         lastWsBroadcast = 0;
const uint32_t   WS_INTERVAL     = 50; // Cambios se aplican cada 50 ms

// ─── Estructura de sesion Modbus ──────────────────────────────────────
// Definir como es cada cliente conectado
struct ModbusSession {
    WiFiClient  client; // Conexion TCP
    bool        active      = false; // Si la sesion esta en uso
    uint32_t    lastSeen    = 0; // Ultima vez que hablo
    TaskHandle_t taskHandle = nullptr; // Numero de sesion (0 - 7)
    uint8_t     id          = 0;
    uint8_t     rxBuf[512]; // Buffer
    uint8_t     txBuf[512]; // Buffer
    uint8_t     pduReq[256];
    uint8_t     pduResp[256];
};

ModbusSession sessions[MAX_SESSIONS]; // 8 sesiones posibles
WiFiServer    modbusServer(MODBUS_PORT); // Escucha conexciones en el puerto 502
SemaphoreHandle_t xSessionMutex;

// ─── Utilidades Modbus ──────────────────────────────────────
// Leer coil
bool readCoil(uint16_t addr) {
    if (addr >= NUM_COILS) return false;
    xSemaphoreTake(xRegMutex, portMAX_DELAY); // Tomar mutex para que nadie mas escriba al mismo tiempo
    bool v = mbCoils[addr];
    xSemaphoreGive(xRegMutex); // Libera mutex
    return v;
}

// Escribir coil
void writeCoil(uint16_t addr, bool val) {
    if (addr >= NUM_COILS) return;
    xSemaphoreTake(xRegMutex, portMAX_DELAY); // Tomar mutex para que nadie mas escriba al mismo tiempo
    mbCoils[addr] = val;
    xSemaphoreGive(xRegMutex); // Libera mutex
}

// Leer holding register
uint16_t readHreg(uint16_t addr) {
    if (addr >= NUM_HREGS) return 0;
    xSemaphoreTake(xRegMutex, portMAX_DELAY); // Tomar mutex para que nadie mas escriba al mismo tiempo
    uint16_t v = mbHregs[addr];
    xSemaphoreGive(xRegMutex); // Libera mutex
    return v;
}

// Escribir holding register
void writeHreg(uint16_t addr, uint16_t val) {
    if (addr >= NUM_HREGS) return;
    xSemaphoreTake(xRegMutex, portMAX_DELAY); // Tomar mutex para que nadie mas escriba al mismo tiempo
    mbHregs[addr] = val;
    xSemaphoreGive(xRegMutex); // Libera mutex
}

// ─── Gestion del control exclusivo (Lock) ──────────────────────────────────────
// Actualizar registros de lectura del lock (200 - 203) para que los clientes puedan leer el estado del lock
// Si hay lock actualiza el TTL, el token y el dueño
// Si no hay pone todo en 0
void _syncLockRegs() {
    if (exclusiveLock.active) { 
        uint32_t now = millis();
        int32_t ttlMs = (int32_t)(exclusiveLock.expiresAt - now);
        uint16_t ttlSec = (ttlMs > 0) ? (ttlMs / 1000) + 1 : 0;
        mbHregs[REG_LOCK_TTL]   = ttlSec;
        mbHregs[REG_LOCK_TOKEN] = exclusiveLock.token;
        mbHregs[REG_LOCK_OWNER] = exclusiveLock.ownerSession;
    } else { 
        mbHregs[REG_LOCK_CMD]   = 0x0000;
        mbHregs[REG_LOCK_TOKEN] = 0x0000;
        mbHregs[REG_LOCK_TTL]   = 0x0000;
        mbHregs[REG_LOCK_OWNER] = LOCK_NONE;
    }
}

// Intentar adquirir el lock para una sesion.
// Si nadie tiene el lock se lo da y genera un token aleatorio
// Si otra sesion lo tiene no se lo da
bool lockAcquire(uint8_t sessionId) {
    xSemaphoreTake(xRegMutex, portMAX_DELAY);
    bool granted = false;
    if (!exclusiveLock.active) {
        // Lock libre se concede
        exclusiveLock.active       = true;
        exclusiveLock.ownerSession = sessionId; // Numero de sesion que pide el lock
        exclusiveLock.token        = (uint16_t)(esp_random() & 0xFFFF);
        if (exclusiveLock.token == 0) exclusiveLock.token = 0x0001;
        exclusiveLock.expiresAt    = millis() + (LOCK_TTL_SECONDS * 1000UL);
        mbHregs[REG_LOCK_CMD]      = LOCK_MAGIC;
        _syncLockRegs();
        granted = true;
        Serial.printf("[LOCK] Sesion #%u adquirio control exclusivo. Token=0x%04X TTL=%ds\n",
                      sessionId, exclusiveLock.token, LOCK_TTL_SECONDS);
    } else if (exclusiveLock.ownerSession == sessionId) {
        // Renovar TTL si ya lo tiene
        exclusiveLock.expiresAt = millis() + (LOCK_TTL_SECONDS * 1000UL);
        _syncLockRegs();
        granted = true;
    } else {
        // Lo tiene otra sesion, no se lo da
        Serial.printf("[LOCK] Sesion #%u solicito lock pero lo tiene sesion #%u\n",
                      sessionId, exclusiveLock.ownerSession);
    }
    xSemaphoreGive(xRegMutex);
    return granted;
}

// Liberar el lock (solo el dueño puede liberarlo)
void lockRelease(uint8_t sessionId) {
    xSemaphoreTake(xRegMutex, portMAX_DELAY);
    if (exclusiveLock.active && exclusiveLock.ownerSession == sessionId) {
        Serial.printf("[LOCK] Sesion #%u libero el control exclusivo\n", sessionId);
        exclusiveLock.active       = false;
        exclusiveLock.ownerSession = LOCK_NONE;
        exclusiveLock.token        = 0;
        exclusiveLock.expiresAt    = 0;
        _syncLockRegs();
    }
    xSemaphoreGive(xRegMutex);
}

// Forzar liberacion por timeout si pasaron mas de 15 segundos sin renovar
void lockCheckTimeout() {
    xSemaphoreTake(xRegMutex, portMAX_DELAY);
    if (exclusiveLock.active && millis() >= exclusiveLock.expiresAt) {
        Serial.printf("[LOCK] Control exclusivo de sesion #%u expirado por timeout (%ds)\n",
                      exclusiveLock.ownerSession, LOCK_TTL_SECONDS);
        exclusiveLock.active       = false;
        exclusiveLock.ownerSession = LOCK_NONE;
        exclusiveLock.token        = 0;
        exclusiveLock.expiresAt    = 0;
        _syncLockRegs();
    } else if (exclusiveLock.active) {
        _syncLockRegs();  // Actualizar TTL en registro
    }
    xSemaphoreGive(xRegMutex);
}

// Antes de mover los servos
// Verificar si una sesion puede escribir en registros de control
// (servos, LED). Devuelve true si se puede escribir.
bool lockCanWrite(uint8_t sessionId) {
    xSemaphoreTake(xRegMutex, portMAX_DELAY);
    bool can;
    if (!exclusiveLock.active) {
        can = true;  // Sin lock cualquiera puede escribir
    } else {
        can = (exclusiveLock.ownerSession == sessionId); // Solo el dueño puede
    }
    xSemaphoreGive(xRegMutex);
    return can;
}

// ─── Cerebro del Firmware ──────────────────────────────────────
// Recibe comando del cliente, lo analiza y devuelve respuesta
int processPDU(const uint8_t* req, int reqLen, uint8_t* resp, uint8_t sessionId) {
    if (reqLen < 1) return 0;
    uint8_t fc = req[0]; // Lee Codigo de funcion 

    // Macros auxiliar: Sonn atajos para comprobar si el registro es de servos o del lock
    #define IS_CTRL_HREG(addr) ((addr) >= REG_SERVO_BASE && (addr) < REG_SERVO_BASE + NUM_SERVOS)
    #define IS_LOCK_REG(addr)  ((addr) == REG_LOCK_CMD)

    // ── FC 0x01: Read Coils ──────────────────────────────────────────
    if (fc == 0x01) {
        if (reqLen < 5) goto exception_illegal;
        uint16_t startAddr = (req[1] << 8) | req[2];
        uint16_t qty       = (req[3] << 8) | req[4];
        if (qty < 1 || qty > 2000) goto exception_illegal;
        if (startAddr + qty > NUM_COILS) goto exception_illegal_addr;

        uint8_t byteCount = (qty + 7) / 8;
        resp[0] = fc;
        resp[1] = byteCount;
        memset(&resp[2], 0, byteCount);
        for (int i = 0; i < qty; i++) {
            if (readCoil(startAddr + i))
                resp[2 + i/8] |= (1 << (i % 8));
        }
        return 2 + byteCount;
    }

    // ── FC 0x03: Read Holding Registers ─────────────────────────────
    if (fc == 0x03) {
        if (reqLen < 5) goto exception_illegal;
        uint16_t startAddr = (req[1] << 8) | req[2];
        uint16_t qty       = (req[3] << 8) | req[4];
        if (qty < 1 || qty > 125) goto exception_illegal;
        if (startAddr + qty > NUM_HREGS) goto exception_illegal_addr;

        resp[0] = fc;
        resp[1] = qty * 2;
        for (int i = 0; i < qty; i++) {
            uint16_t v = readHreg(startAddr + i);
            resp[2 + i*2]     = v >> 8;
            resp[2 + i*2 + 1] = v & 0xFF;
        }
        return 2 + qty * 2;
    }

    // ── FC 0x05: Write Single Coil ───────────────────────────────────
    if (fc == 0x05) {
        if (reqLen < 5) goto exception_illegal;
        uint16_t addr = (req[1] << 8) | req[2];
        uint16_t val  = (req[3] << 8) | req[4];
        if (addr >= NUM_COILS) goto exception_illegal_addr;
        // Coil 0 (LED) es de control: respetar lock
        if (addr == COIL_LED && !lockCanWrite(sessionId)) goto exception_lock;
        writeCoil(addr, val == 0xFF00);
        memcpy(resp, req, 5);
        return 5;
    }

    // ── FC 0x06: Write Single Register ──────────────────────────────
    if (fc == 0x06) {
        if (reqLen < 5) goto exception_illegal;
        uint16_t addr = (req[1] << 8) | req[2];
        uint16_t val  = (req[3] << 8) | req[4];
        if (addr >= NUM_HREGS) goto exception_illegal_addr;

        // Registro de lock: procesamiento especial
        if (IS_LOCK_REG(addr)) { // Si se escriben en registro 200
            if (val == LOCK_MAGIC) { // Si se escriben 0XA5A5
                if (!lockAcquire(sessionId)) goto exception_lock; 
            } else if (val == 0x0000) {
                lockRelease(sessionId); 
            }
            memcpy(resp, req, 5);
            return 5;
        }

        // Registros de control de servos: respetar lock (bloquear movimientos de servos)
        if (IS_CTRL_HREG(addr) && !lockCanWrite(sessionId)) goto exception_lock; 

        writeHreg(addr, val);
        memcpy(resp, req, 5);
        return 5;
    }

    // ── FC 0x0F: Write Multiple Coils ────────────────────────────────
    if (fc == 0x0F) {
        if (reqLen < 6) goto exception_illegal;
        uint16_t startAddr = (req[1] << 8) | req[2];
        uint16_t qty       = (req[3] << 8) | req[4];
        uint8_t  byteCount = req[5];
        if (qty < 1 || qty > 1968) goto exception_illegal;
        if (startAddr + qty > NUM_COILS) goto exception_illegal_addr;
        if (reqLen < 6 + byteCount) goto exception_illegal;
        if (startAddr == COIL_LED && !lockCanWrite(sessionId)) goto exception_lock;

        for (int i = 0; i < qty; i++) {
            bool v = (req[6 + i/8] >> (i % 8)) & 0x01;
            writeCoil(startAddr + i, v);
        }
        resp[0] = fc;
        resp[1] = req[1]; resp[2] = req[2];
        resp[3] = req[3]; resp[4] = req[4];
        return 5;
    }

    // ── FC 0x10: Write Multiple Registers ───────────────────────────
    if (fc == 0x10) {
        if (reqLen < 6) goto exception_illegal;
        uint16_t startAddr = (req[1] << 8) | req[2];
        uint16_t qty       = (req[3] << 8) | req[4];
        uint8_t  byteCount = req[5];
        if (qty < 1 || qty > 123) goto exception_illegal;
        if (startAddr + qty > NUM_HREGS) goto exception_illegal_addr;
        if (reqLen < 6 + byteCount) goto exception_illegal;

        // Si el rango incluye REG_LOCK_CMD, manejar lock primero
        for (int i = 0; i < qty; i++) {
            uint16_t addr = startAddr + i;
            if (IS_LOCK_REG(addr)) {
                uint16_t v = (req[6 + i*2] << 8) | req[6 + i*2 + 1];
                if (v == LOCK_MAGIC) {
                    if (!lockAcquire(sessionId)) goto exception_lock;
                } else if (v == 0x0000) {
                    lockRelease(sessionId);
                }
                continue;
            }
            // Registros de control: respetar lock (bloquear movimientos de servos)
            if (IS_CTRL_HREG(addr) && !lockCanWrite(sessionId)) goto exception_lock;
        }

        for (int i = 0; i < qty; i++) {
            uint16_t addr = startAddr + i;
            if (IS_LOCK_REG(addr)) continue;  // ya procesado
            uint16_t v = (req[6 + i*2] << 8) | req[6 + i*2 + 1];
            writeHreg(addr, v);
        }
        resp[0] = fc;
        resp[1] = req[1]; resp[2] = req[2];
        resp[3] = req[3]; resp[4] = req[4];
        return 5;
    }

    // ── FC 0x43: MEI / Device Identification ─────────────────────────
    if (fc == 0x43) {
        if (reqLen < 3) goto exception_illegal;
        if (req[1] != 0x0E) goto exception_illegal;

        resp[0] = 0x43;
        resp[1] = 0x0E;
        resp[2] = req[2];
        resp[3] = 0x01;
        resp[4] = 0x00;
        resp[5] = 0x00;
        resp[6] = 0x03;

        int pi = 7;
        const char* objs[3] = {MB_VENDOR_NAME, MB_PRODUCT_CODE, MB_FIRMWARE_VER};
        for (int i = 0; i < 3; i++) {
            uint8_t len = strlen(objs[i]);
            resp[pi++] = i;
            resp[pi++] = len;
            memcpy(&resp[pi], objs[i], len);
            pi += len;
        }
        return pi;
    }

    // ── Excepciones ───────────────────────────────────────────────────
    exception_illegal:
    resp[0] = fc | 0x80;
    resp[1] = 0x01;  // Illegal Function
    return 2;

    exception_illegal_addr:
    resp[0] = fc | 0x80;
    resp[1] = 0x02;  // Illegal Data Address
    return 2;

    exception_lock:
    // Lock activo, error
    // (usado por Siemens S7 cuando hay acceso exclusivo activo)
    resp[0] = fc | 0x80;
    resp[1] = 0x06;  // Slave Device Busy
    Serial.printf("[LOCK] Sesion #%u bloqueada por control exclusivo de sesion #%u\n",
                  sessionId, exclusiveLock.ownerSession);
    return 2;

    #undef IS_CTRL_HREG
    #undef IS_LOCK_REG
}

// ── Tarea que maneja cada cliente conectado ───────────────────────────────────────────────────
void sessionTask(void* param) {
    ModbusSession* sess = (ModbusSession*)param;
    // Usar buffers del struct (ya en memoria estatica, no en el stack de la tarea)
    uint8_t* rxBuf   = sess->rxBuf;
    uint8_t* txBuf   = sess->txBuf;
    uint8_t* pduReq  = sess->pduReq;
    uint8_t* pduResp = sess->pduResp;

    Serial.printf("[MB] Sesion #%u abierta — IP: %s\n",
                  sess->id, sess->client.remoteIP().toString().c_str());

    while (sess->active && sess->client.connected()) {

        // Timeout de inactividad (30 segundos)
        if (millis() - sess->lastSeen > SESSION_TIMEOUT) {
            Serial.printf("[MB] Sesion #%u timeout por inactividad\n", sess->id);
            break;
        }

        if (!sess->client.available()) {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }

        // Leer MBAP header (7 bytes)
        int n = 0;
        uint32_t t0 = millis();
        while (n < 7 && millis() - t0 < 1000) {
            if (sess->client.available())
                rxBuf[n++] = sess->client.read();
            else
                vTaskDelay(pdMS_TO_TICKS(1));
        }
        if (n < 7) continue;

        sess->lastSeen = millis();

        uint16_t txId    = (rxBuf[0] << 8) | rxBuf[1];
        // rxBuf[2..3] = Protocol ID (0x0000)
        // rxBuf[4..5] = MBAP Length = Unit ID (1 byte) + PDU
        uint16_t mbapLen = (rxBuf[4] << 8) | rxBuf[5];
        // rxBuf[6]    = Unit ID  <- ya consumido, descontarlo
        // BUG FIX: el campo Length del MBAP incluye el byte de Unit ID,
        // por lo que el PDU real tiene (mbapLen - 1) bytes.
        // Sin este fix, se lee un byte de mas y el Function Code queda desplazado, causando que processPDU no reconozca ningun codigo de funcion. 
        uint16_t pduLen  = mbapLen - 1;

        if (pduLen < 1 || pduLen > 252) continue;

        // Leer PDU (bytes de datos reales, sin el Unit ID)
        n = 0;
        t0 = millis();
        while (n < (int)pduLen && millis() - t0 < 1000) {
            if (sess->client.available())
                pduReq[n++] = sess->client.read();
            else
                vTaskDelay(pdMS_TO_TICKS(1));
        }
        if (n < (int)pduLen) continue;

        // Procesar PDU con longitud correcta
        int respLen = processPDU(pduReq, pduLen, pduResp, sess->id); // sess-id se pasa el numero de sesion para saber quien esta escribiendo	
        if (respLen <= 0) continue;

        // Armar respuesta MBAP + PDU
        // BUG FIX: el campo Length de la respuesta MBAP debe ser
        // respLen + 1 porque incluye el byte de Unit ID
        // Envia al cliente
        uint16_t respMbapLen = respLen + 1;
        txBuf[0] = txId >> 8;
        txBuf[1] = txId & 0xFF;
        txBuf[2] = 0x00;
        txBuf[3] = 0x00;
        txBuf[4] = respMbapLen >> 8;
        txBuf[5] = respMbapLen & 0xFF;
        txBuf[6] = rxBuf[6];  // echo Unit ID
        memcpy(&txBuf[7], pduResp, respLen);

        sess->client.write(txBuf, 7 + respLen);
        sess->client.flush();
    }

    Serial.printf("[MB] Sesion #%u cerrada\n", sess->id);
    // Liberar el lock si esta sesion lo tenia 
    lockRelease(sess->id);
    sess->client.stop();
    xSemaphoreTake(xSessionMutex, portMAX_DELAY);
    sess->active     = false;
    sess->taskHandle = nullptr;
    xSemaphoreGive(xSessionMutex);

    vTaskDelete(nullptr);
}

// ── Tarea de aceptacion, escucha nuevas conexiones en el puerto 502 ───────────────────────────────────────────────────
// Corre en Core 0 del ESP32
// Inicia servidor Modbus en puerto 502
void acceptTask(void* param) {
    Serial.println("[MB] Tarea de aceptacion iniciada en Core 0");
    modbusServer.begin();

    while (true) { // Bucle infinito
        WiFiClient newClient = modbusServer.accept(); // Espera que llegue una nueva conexion TCP
        if (!newClient) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        xSemaphoreTake(xSessionMutex, portMAX_DELAY);

        // Buscar slot libre de las 8 sesiones TCP
        int slot = -1;
        for (int i = 0; i < MAX_SESSIONS; i++) {
            if (!sessions[i].active) { slot = i; break; }
        }

        if (slot == -1) {
            // Sin slots disponibles se acepta conexion TCP pero responde con excepcion
            // Cierra la conexion nueva. Libera el candado y continua escuchando
            Serial.printf("[MB] Sesiones llenas (%d/%d) — rechazando %s\n",
                          MAX_SESSIONS, MAX_SESSIONS,
                          newClient.remoteIP().toString().c_str());
            newClient.stop();
            xSemaphoreGive(xSessionMutex);
            continue;
        }

        sessions[slot].client   = newClient;
        sessions[slot].active   = true;
        sessions[slot].lastSeen = millis();
        sessions[slot].id       = slot;

        // Guarda la nueva conexion en slot libre. Marca esta como activa. Crea nueva tarea dedicada a este cliente
        char taskName[16];
        snprintf(taskName, sizeof(taskName), "mb_sess_%d", slot);
        xTaskCreatePinnedToCore(
            sessionTask,       // Funcion que se va a ejecutar
            taskName,		   // Nombre de la tarea
            8192,              // Tamaño de la memoria
            &sessions[slot],   // Parametro que se le pasa
            5,                 // Prioridad
            &sessions[slot].taskHandle,
            0                  // Core 0
        );

        xSemaphoreGive(xSessionMutex);
        Serial.printf("[MB] Sesion #%d asignada — %d/%d slots ocupados\n",
                      slot, slot + 1, MAX_SESSIONS);
    }
}

// ── HMI ───────────────────────────────────────────────────
const char DASHBOARD_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>M502 Robotic Arm — HMI</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:'Arial',sans-serif}
body{background:#c8c8c8;color:#1a1a1a;font-size:11px;height:100vh;display:flex;flex-direction:column;overflow:hidden}
#titlebar{background:#1c3a5e;height:32px;display:flex;align-items:center;padding:0 10px;gap:10px;flex-shrink:0;border-bottom:2px solid #0a1e36}
.tb-logo{font-size:12px;font-weight:700;color:#fff;letter-spacing:1px;padding-right:10px;border-right:1px solid #3a5a7e}
.tb-title{font-size:11px;color:#a8c4e0;letter-spacing:.5px}
.tb-right{margin-left:auto;display:flex;align-items:center;gap:14px}
.tb-stat{font-size:10px;color:#a8c4e0}
.tb-stat b{color:#fff}
#tb-clk{font-size:12px;color:#7ec8e3;font-weight:700;font-family:'Courier New',monospace}
#menubar{background:#e8e8e8;height:22px;display:flex;align-items:stretch;border-bottom:2px solid #999;flex-shrink:0}
.mb-tab{padding:0 12px;font-size:10px;color:#1a1a1a;display:flex;align-items:center;border-right:1px solid #bbb;cursor:default}
.mb-tab.act{background:#fff;border-top:2px solid #1c3a5e;color:#1c3a5e;font-weight:700}
.mb-right{margin-left:auto;display:flex;align-items:center;gap:0}
.mb-status{display:flex;align-items:center;gap:5px;padding:0 10px;border-left:1px solid #bbb;font-size:10px;color:#333}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;border:1px solid #555}
.dot.g{background:#22aa22;border-color:#117711}
.dot.y{background:#ddaa00;border-color:#aa7700}
.dot.r{background:#cc2222;border-color:#991111}
#main{flex:1;display:flex;gap:5px;overflow:hidden;background:#c8c8c8;padding:5px}
.panel{background:#e8e8e8;border:1px solid #999;border-top:2px solid #1c3a5e;display:flex;flex-direction:column;overflow:hidden}
.ph{background:#1c3a5e;color:#fff;font-size:10px;font-weight:700;letter-spacing:.8px;padding:3px 8px;flex-shrink:0;text-transform:uppercase;display:flex;align-items:center;justify-content:space-between}
.ph-sub{font-size:9px;color:#a8c4e0;font-weight:400;letter-spacing:0}
#col-left{width:185px;flex-shrink:0;display:flex;flex-direction:column;gap:5px}
.info-tbl{width:100%;border-collapse:collapse}
.info-tbl tr{border-bottom:1px solid #ccc}
.info-tbl tr:last-child{border-bottom:none}
.info-tbl td{padding:3px 6px;font-size:10px;vertical-align:middle}
.info-tbl td:first-child{color:#444;width:50%}
.info-tbl td:last-child{font-weight:700;color:#1a1a1a;font-family:'Courier New',monospace;font-size:10px}
.info-tbl td.ok{color:#117711}.info-tbl td.warn{color:#aa6600}.info-tbl td.err{color:#991111}
.led-blk{padding:5px 6px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #ccc}
.led-name{font-size:10px;color:#333;font-weight:700}
.led-lamp{width:18px;height:18px;border-radius:50%;background:#888;border:2px solid #555;flex-shrink:0}
.led-lamp.on{background:#ffdd00;border-color:#aa8800}
.led-txt{font-size:9px;font-weight:700;color:#888;font-family:'Courier New',monospace}
.led-txt.on{color:#aa6600}
.reg-tbl{width:100%;border-collapse:collapse}
.reg-tbl th{background:#d0d0d0;border:1px solid #bbb;font-size:9px;padding:2px 4px;text-align:left;color:#333;font-weight:700}
.reg-tbl td{border:1px solid #ccc;font-size:9px;padding:2px 4px;background:#fff;vertical-align:middle}
.reg-tbl tr:nth-child(even) td{background:#f4f4f4}
.tag{font-size:8px;padding:1px 4px;font-weight:700;border:1px solid}
.tag.c{color:#1c3a5e;border-color:#1c3a5e;background:#dde8f4}
.tag.h{color:#117711;border-color:#117711;background:#e0f0e0}
.rval{font-family:'Courier New',monospace;font-size:10px;font-weight:700;text-align:right;color:#1a1a1a}
#col-center{flex:1;display:flex;flex-direction:column;gap:5px;min-width:0}
.servo-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;padding:6px;flex:1;overflow:auto}
.sc{background:#fff;border:2px solid #999;display:flex;flex-direction:column;overflow:hidden}
.sc.moving{border-color:#ddaa00}.sc.fault{border-color:#cc2222}
.sc-head{background:#d0d4dc;border-bottom:1px solid #bbb;padding:3px 6px;display:flex;align-items:center;justify-content:space-between}
.sc-num{font-size:9px;color:#555;font-family:'Courier New',monospace}
.sc-name{font-size:10px;font-weight:700;color:#1c3a5e;letter-spacing:.5px}
.sc-lamp{width:10px;height:10px;border-radius:50%;background:#888;border:1px solid #555;flex-shrink:0}
.sc-lamp.ok{background:#22aa22;border-color:#116611}.sc-lamp.mv{background:#ffdd00;border-color:#aa8800}
.sc-body{padding:6px;display:flex;flex-direction:column;gap:5px;flex:1}
.gauge-row{display:flex;justify-content:center;align-items:flex-end;gap:8px;margin-bottom:2px}
.ang-big{text-align:center}
.ang-big .num{font-size:22px;font-weight:700;color:#1a1a1a;font-family:'Courier New',monospace;line-height:1}
.ang-big .lbl{font-size:8px;color:#666;letter-spacing:.5px}
.ang-big .num.tgt{font-size:13px;color:#1c3a5e}
.ang-sep{font-size:16px;color:#bbb;line-height:1;padding-bottom:4px}
.bar-outer{background:#d0d0d0;border:1px solid #bbb;height:12px;position:relative;overflow:hidden}
.bar-inner{height:100%;background:#1c3a5e;transition:width .12s linear}
.bar-inner.moving{background:#ddaa00}
.bar-tgt-line{position:absolute;top:0;width:2px;height:100%;background:#cc2222;opacity:.8;transition:left .12s}
.sc-foot{display:flex;justify-content:space-between;align-items:center;padding:2px 6px;background:#f0f0f0;border-top:1px solid #ccc}
.sc-mode{font-size:8px;color:#555;font-family:'Courier New',monospace}
.sc-delta{font-size:8px;font-family:'Courier New',monospace;color:#888}
.sc-delta.moving{color:#aa6600;font-weight:700}
#col-right{width:175px;flex-shrink:0;display:flex;flex-direction:column;gap:5px}
.tele-tbl{width:100%;border-collapse:collapse}
.tele-tbl tr{border-bottom:1px solid #ccc}
.tele-tbl tr:last-child{border-bottom:none}
.tele-tbl td{padding:3px 6px;font-size:10px}
.tele-tbl td:first-child{color:#444;width:45%}
.tele-tbl td:last-child{font-weight:700;font-family:'Courier New',monospace;font-size:10px;color:#1a1a1a}
.tele-tbl td.ok{color:#117711}
.alarm-tbl{width:100%;border-collapse:collapse;font-size:9px}
.alarm-tbl tr{border-bottom:1px solid #ccc}
.alarm-tbl td{padding:2px 5px;vertical-align:middle}
.alarm-row-act td{background:#fff3cd}
.alarm-ts{font-family:'Courier New',monospace;color:#666;white-space:nowrap}
.alarm-msg{color:#1a1a1a}.alarm-msg.ev{color:#1c3a5e;font-weight:700}.alarm-msg.warn{color:#aa6600;font-weight:700}
#statusbar{background:#d0d0d0;border-top:2px solid #999;height:20px;display:flex;align-items:center;padding:0 8px;gap:0;flex-shrink:0}
.sbi{padding:0 10px;border-right:1px solid #aaa;font-size:9px;color:#333;height:100%;display:flex;align-items:center;gap:4px;font-family:'Courier New',monospace}
.sbi b{color:#1c3a5e}.sbi:first-child{padding-left:0}
.sbi-right{margin-left:auto;color:#555;font-size:9px;font-family:'Courier New',monospace}
/* Badge de sesiones activas */
.sess-badge{display:inline-block;background:#1c3a5e;color:#fff;font-size:9px;font-weight:700;padding:1px 5px;border-radius:2px;font-family:'Courier New',monospace}
.sess-badge.warn{background:#aa6600}
</style>
</head>
<body>
<div id="titlebar">
  <span class="tb-logo">M502</span>
  <span class="tb-title">ROBOTIC ARM CONTROL SYSTEM &mdash; Firmware 3.0 &mdash; Multi-Sesion Modbus TCP</span>
  <div class="tb-right">
    <span class="tb-stat">IP: <b id="t-ip">--</b></span>
    <span class="tb-stat">RSSI: <b id="t-rssi">--</b> dBm</span>
    <span class="tb-stat">Uptime: <b id="t-upt">--:--:--</b></span>
    <span id="tb-clk">--:--:--</span>
  </div>
</div>
<div id="menubar">
  <div class="mb-tab act">Monitor</div>
  <div class="mb-tab">Registros</div>
  <div class="mb-tab">Alarmas</div>
  <div class="mb-tab">Config.</div>
  <div class="mb-right">
    <div class="mb-status"><div class="dot g" id="ws-dot"></div><span id="ws-lbl">Sin conexi&oacute;n</span></div>
    <div class="mb-status"><div class="dot g"></div>Modbus TCP :502</div>
    <div class="mb-status">Sesiones: <span class="sess-badge" id="sess-badge">0/8</span></div>
    <div class="mb-status"><div class="dot y" id="led-dot"></div>LED <span id="led-mbst">APAGADO</span></div>
  </div>
</div>
<div id="main">
  <div id="col-left">
    <div class="panel" style="flex-shrink:0">
      <div class="ph">Fn 0x43 &mdash; Fabricante</div>
      <table class="info-tbl">
        <tr><td>Fabricante</td><td>M502 Robotics</td></tr>
        <tr><td>Modelo</td><td>ARM-6DOF-v2</td></tr>
        <tr><td>Firmware</td><td>3.0.0</td></tr>
        <tr><td>Sesiones m&aacute;x.</td><td>8 (S7-1200)</td></tr>
        <tr><td>DOF</td><td>6</td></tr>
        <tr><td>Protocolo</td><td>Modbus TCP</td></tr>
        <tr><td>Timeout ses.</td><td>30 s</td></tr>
      </table>
    </div>
    <div class="panel" style="flex-shrink:0">
      <div class="ph">Conexi&oacute;n</div>
      <table class="info-tbl">
        <tr><td>Estado</td><td class="ok" id="conn-st">ONLINE</td></tr>
        <tr><td>IP</td><td id="d-ip">--</td></tr>
        <tr><td>Puerto MB</td><td>502</td></tr>
        <tr><td>Sesiones</td><td id="d-sess">0/8</td></tr>
      </table>
    </div>
    <div class="panel" style="flex-shrink:0">
      <div class="ph">LED GPIO2 <span class="ph-sub">COIL 0</span></div>
      <div class="led-blk">
        <span class="led-name">LED INTEGRADO</span>
        <div class="led-lamp" id="led-lamp"></div>
        <span class="led-txt" id="led-txt">APAGADO</span>
      </div>
    </div>
    <div class="panel" style="flex:1;overflow:hidden;min-height:0">
      <div class="ph">Mapa de registros</div>
      <div style="overflow:auto;flex:1;height:100%">
        <table class="reg-tbl">
          <thead><tr><th>REG</th><th>Nombre</th><th>T.</th><th>Val</th></tr></thead>
          <tbody>
            <tr><td style="font-family:'Courier New',monospace;color:#555">0</td><td>LED</td><td><span class="tag c">COIL</span></td><td class="rval" id="rv-led">0</td></tr>
            <tr><td style="font-family:'Courier New',monospace;color:#555">100</td><td>Pinza</td><td><span class="tag h">HREG</span></td><td class="rval" id="rv-0">90</td></tr>
            <tr><td style="font-family:'Courier New',monospace;color:#555">101</td><td>Pin-Rot</td><td><span class="tag h">HREG</span></td><td class="rval" id="rv-1">90</td></tr>
            <tr><td style="font-family:'Courier New',monospace;color:#555">102</td><td>Mu&ntilde;eca</td><td><span class="tag h">HREG</span></td><td class="rval" id="rv-2">90</td></tr>
            <tr><td style="font-family:'Courier New',monospace;color:#555">103</td><td>Codo</td><td><span class="tag h">HREG</span></td><td class="rval" id="rv-3">90</td></tr>
            <tr><td style="font-family:'Courier New',monospace;color:#555">104</td><td>Hombro</td><td><span class="tag h">HREG</span></td><td class="rval" id="rv-4">90</td></tr>
            <tr><td style="font-family:'Courier New',monospace;color:#555">105</td><td>Base</td><td><span class="tag h">HREG</span></td><td class="rval" id="rv-5">90</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
  <div id="col-center">
    <div class="panel" style="flex-shrink:0">
      <div class="ph">Control de servomotores &mdash; Solo lectura <span class="ph-sub" id="ph-pkt">PKT: 0 | LAT: -- ms</span></div>
    </div>
    <div class="panel" style="flex:1;overflow:hidden">
      <div class="servo-grid" id="servo-grid"></div>
    </div>
  </div>
  <div id="col-right">
    <div class="panel" style="flex-shrink:0">
      <div class="ph">Telemetr&iacute;a</div>
      <table class="tele-tbl">
        <tr><td>IP</td><td class="ok" id="tr-ip">--</td></tr>
        <tr><td>RSSI</td><td id="tr-rssi">--</td></tr>
        <tr><td>SSID</td><td id="tr-ssid">--</td></tr>
        <tr><td>Uptime</td><td id="tr-upt">--</td></tr>
        <tr><td>Latencia</td><td id="tr-lat">--</td></tr>
        <tr><td>Pkt. Rx</td><td class="ok" id="tr-pkt">0</td></tr>
        <tr><td>Sesiones act.</td><td class="ok" id="tr-sess">0/8</td></tr>
        <tr><td>Modelo</td><td>ESP32</td></tr>
      </table>
    </div>
    <div class="panel" style="flex:1;overflow:hidden;min-height:0">
      <div class="ph">Registro de eventos</div>
      <div style="overflow-y:auto;flex:1;height:100%;padding:3px" id="log-wrap">
        <table class="alarm-tbl"><tbody id="alarm-body"></tbody></table>
      </div>
    </div>
  </div>
</div>
<div id="statusbar">
  <div class="sbi">ESTADO: <b id="sb-st">STANDBY</b></div>
  <div class="sbi">SSID: <b id="sb-ssid">--</b></div>
  <div class="sbi">SESIONES: <b id="sb-sess">0/8</b></div>
  <div class="sbi">PKT: <b id="sb-pkt">0</b></div>
  <div class="sbi">LED: <b id="sb-led">APAGADO</b></div>
  <div class="sbi">PROTOCOLO: <b>Modbus TCP / WS:81</b></div>
  <div class="sbi-right" id="sb-time">--:--:--</div>
</div>
<script>
const NAMES=['Pinza','Pin-Rot','Mu\u00f1eca','Codo','Hombro','Base'];
const REGS=[100,101,102,103,104,105];
let state={angles:[90,90,90,90,90,90],targets:[90,90,90,90,90,90],led:false,ssid:'--',rssi:0,ip:'--',uptime:0,sessions:0};
let pktCount=0,lastPktTime=Date.now(),fpsFrames=0,lastFpsTime=Date.now(),logCount=0;
const grid=document.getElementById('servo-grid');
NAMES.forEach(function(name,i){
  grid.innerHTML+='<div class="sc" id="sc-'+i+'"><div class="sc-head"><span class="sc-num">S'+(i+1)+' &middot; REG '+REGS[i]+'</span><span class="sc-name">'+name+'</span><div class="sc-lamp ok" id="sl-'+i+'"></div></div><div class="sc-body"><div class="gauge-row"><div class="ang-big"><div class="num" id="cur-'+i+'">90</div><div class="lbl">ACTUAL &deg;</div></div><div class="ang-sep">/</div><div class="ang-big"><div class="num tgt" id="tgt-'+i+'">90</div><div class="lbl">OBJ. &deg;</div></div></div><div class="bar-outer"><div class="bar-inner" id="bf-'+i+'" style="width:50%"></div><div class="bar-tgt-line" id="bt-'+i+'" style="left:50%"></div></div></div><div class="sc-foot"><span class="sc-mode">AUTO</span><span class="sc-delta" id="sd-'+i+'">&Delta; 0&deg;</span></div></div>';
});
function fmtTime(s){var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=s%60;return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(ss).padStart(2,'0');}
function updateUI(){
  for(var i=0;i<6;i++){
    var cur=Math.round(state.angles[i]),tgt=Math.round(state.targets[i]),dlt=Math.abs(cur-tgt),mv=dlt>1;
    document.getElementById('cur-'+i).textContent=cur;
    document.getElementById('tgt-'+i).textContent=tgt;
    document.getElementById('sd-'+i).innerHTML='&Delta; '+dlt+'&deg;';
    document.getElementById('sd-'+i).className='sc-delta'+(mv?' moving':'');
    document.getElementById('bf-'+i).style.width=(cur/180*100)+'%';
    document.getElementById('bf-'+i).className='bar-inner'+(mv?' moving':'');
    document.getElementById('bt-'+i).style.left=(tgt/180*100)+'%';
    document.getElementById('sc-'+i).className='sc'+(mv?' moving':'');
    document.getElementById('sl-'+i).className='sc-lamp'+(mv?' mv':' ok');
    document.getElementById('rv-'+i).textContent=tgt;
  }
  var ledOn=state.led;
  document.getElementById('led-lamp').className='led-lamp'+(ledOn?' on':'');
  document.getElementById('led-txt').textContent=ledOn?'ENCENDIDO':'APAGADO';
  document.getElementById('led-txt').className='led-txt'+(ledOn?' on':'');
  document.getElementById('led-mbst').textContent=ledOn?'ENCENDIDO':'APAGADO';
  document.getElementById('led-dot').className='dot '+(ledOn?'y':'g');
  document.getElementById('sb-led').textContent=ledOn?'ENCENDIDO':'APAGADO';
  document.getElementById('rv-led').textContent=ledOn?1:0;
  var upt=fmtTime(state.uptime);
  document.getElementById('t-upt').textContent=upt;
  document.getElementById('t-rssi').textContent=state.rssi;
  document.getElementById('t-ip').textContent=state.ip;
  document.getElementById('d-ip').textContent=state.ip;
  document.getElementById('tr-ip').textContent=state.ip;
  document.getElementById('tr-rssi').textContent=state.rssi+' dBm';
  document.getElementById('tr-ssid').textContent=state.ssid;
  document.getElementById('tr-upt').textContent=upt;
  document.getElementById('tr-pkt').textContent=pktCount;
  document.getElementById('sb-ssid').textContent=state.ssid;
  document.getElementById('sb-pkt').textContent=pktCount;
  document.getElementById('sb-st').textContent='ONLINE';
  var sessTxt=state.sessions+'/8';
  document.getElementById('sess-badge').textContent=sessTxt;
  document.getElementById('sess-badge').className='sess-badge'+(state.sessions>=6?' warn':'');
  document.getElementById('d-sess').textContent=sessTxt;
  document.getElementById('tr-sess').textContent=sessTxt;
  document.getElementById('sb-sess').textContent=sessTxt;
  fpsFrames++;
  var now2=Date.now();
  if(now2-lastFpsTime>=1000){fpsFrames=0;lastFpsTime=now2;}
}
function addLog(msg,type){
  type=type||'sys';
  var body=document.getElementById('alarm-body'),wrap=document.getElementById('log-wrap');
  var d=new Date(),ts=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0');
  var tr=document.createElement('tr');
  tr.className=type==='ev'?'alarm-row-act':'';
  tr.innerHTML='<td class="alarm-ts">'+ts+'</td><td class="alarm-msg '+type+'">'+msg+'</td>';
  body.appendChild(tr);
  if(++logCount>80)body.removeChild(body.children[0]);
  wrap.scrollTop=wrap.scrollHeight;
}
function tick(){var d=new Date(),clk=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0');document.getElementById('tb-clk').textContent=clk;document.getElementById('sb-time').textContent=clk;}
var ws;
function connect(){
  var host=location.hostname||'192.168.1.100';
  ws=new WebSocket('ws://'+host+':81');
  ws.onopen=function(){
    document.getElementById('ws-dot').className='dot g';
    document.getElementById('ws-lbl').textContent='Conectado';
    document.getElementById('conn-st').textContent='ONLINE';
    document.getElementById('conn-st').className='ok';
    addLog('WS conectado &mdash; '+host,'ev');
  };
  ws.onmessage=function(e){
    try{
      var d=JSON.parse(e.data);
      pktCount++;
      var lat=Date.now()-lastPktTime;
      lastPktTime=Date.now();
      document.getElementById('ph-pkt').textContent='PKT: '+pktCount+' | LAT: '+lat+' ms';
      document.getElementById('tr-lat').textContent=lat+' ms';
      if(d.targets){for(var i=0;i<6;i++){if(d.targets[i]!==state.targets[i])addLog(NAMES[i]+' [REG '+REGS[i]+'] &rarr; '+d.targets[i]+'&deg;','ev');}}
      if(d.led!==undefined&&d.led!==state.led)addLog('LED &rarr; '+(d.led?'ENCENDIDO':'APAGADO'),'warn');
      if(d.sessions!==undefined&&d.sessions!==state.sessions)addLog('Sesiones Modbus: '+d.sessions+'/8',d.sessions>1?'warn':'ev');
      if(d.angles)state.angles=d.angles;
      if(d.targets)state.targets=d.targets;
      if(d.led!==undefined)state.led=d.led;
      if(d.ssid)state.ssid=d.ssid;
      if(d.rssi!==undefined)state.rssi=d.rssi;
      if(d.ip)state.ip=d.ip;
      if(d.uptime!==undefined)state.uptime=d.uptime;
      if(d.sessions!==undefined)state.sessions=d.sessions;
      updateUI();
    }catch(er){}
  };
  ws.onclose=function(){
    document.getElementById('ws-dot').className='dot r';
    document.getElementById('ws-lbl').textContent='Sin conexi\u00f3n';
    document.getElementById('conn-st').textContent='OFFLINE';
    document.getElementById('conn-st').className='err';
    addLog('Conexi\u00f3n perdida &mdash; reintentando en 3 s','warn');
    setTimeout(connect,3000);
  };
  ws.onerror=function(){ws.close();};
}
addLog('Sistema M502 v3.0 iniciado &mdash; Multi-Sesion Modbus TCP','sys');
addLog('Pool de sesiones: 8 slots (compatible S7-1200 / M221)','sys');
connect();
setInterval(tick,1000);
tick();
</script>
</body>
</html>
)rawliteral";

// ── Handlers HTTP + WebSockets ───────────────────────────────────────────────────
void handleRoot()     { httpServer.send(200, "text/html", DASHBOARD_HTML); }
void handleNotFound() { httpServer.send(404, "text/plain", "Not found"); }

void webSocketEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
    if      (type == WStype_CONNECTED)    Serial.printf("[WS] Cliente #%u conectado\n", num);
    else if (type == WStype_DISCONNECTED) Serial.printf("[WS] Cliente #%u desconectado\n", num);
}

int countActiveSessions() { // Cuantas sesiones Modbus estan activas actualmente
    xSemaphoreTake(xSessionMutex, portMAX_DELAY);
    int count = 0;
    for (int i = 0; i < MAX_SESSIONS; i++)
        if (sessions[i].active) count++;
    xSemaphoreGive(xSessionMutex);
    return count;
}

void broadcastState() { // Llena el JSON con angulos, targets, led, ip, sesiones, lockActive, etc. Crea paquete JSON con todo el estado actual para el Dashboard
    StaticJsonDocument<640> doc;
    JsonArray angles  = doc.createNestedArray("angles");
    JsonArray targets = doc.createNestedArray("targets");
    for (int i = 0; i < NUM_SERVOS; i++) {
        angles.add((int)round(currentAngles[i]));
        targets.add((int)readHreg(REG_SERVO_BASE + i));
    }
    doc["led"]      = readCoil(COIL_LED);
    doc["ssid"]     = String(SSID);
    doc["rssi"]     = WiFi.RSSI();
    doc["ip"]       = WiFi.localIP().toString();
    doc["uptime"]   = millis() / 1000;
    doc["sessions"] = countActiveSessions();

    // Estado del control exclusivo
    xSemaphoreTake(xRegMutex, portMAX_DELAY);
    doc["lockActive"] = exclusiveLock.active;
    if (exclusiveLock.active) {
        doc["lockOwner"] = exclusiveLock.ownerSession;
        int32_t ttlMs = (int32_t)(exclusiveLock.expiresAt - millis());
        doc["lockTTL"]   = max(0, (int)(ttlMs / 1000));
    }
    xSemaphoreGive(xRegMutex);

    String json;
    serializeJson(doc, json);
    wsServer.broadcastTXT(json);
}

// ── Setup ───────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);

    // Inicializar registros con posicion de reposo
    for (int i = 0; i < NUM_SERVOS; i++)
        mbHregs[REG_SERVO_BASE + i] = 90;

    // Mutexes
    xRegMutex     = xSemaphoreCreateMutex();
    xSessionMutex = xSemaphoreCreateMutex();

    // Servos
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    ESP32PWM::allocateTimer(2);
    ESP32PWM::allocateTimer(3);
    for (int i = 0; i < NUM_SERVOS; i++) {
        servos[i].setPeriodHertz(50);
        servos[i].attach(PINES_SERVOS[i], 500, 2400);
        servos[i].write(90);
    }

    // WiFi
    Serial.print("Conectando a WiFi...");
    WiFi.begin(SSID, PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500); Serial.print(".");
    }
    Serial.println();
    Serial.println("WiFi OK — IP: " + WiFi.localIP().toString());
    Serial.println("Dashboard: http://" + WiFi.localIP().toString());

    Serial.println("--- Fn 0x43 Device Identification ---");
    Serial.println("Fabricante : " + String(MB_VENDOR_NAME));
    Serial.println("Producto   : " + String(MB_PRODUCT_NAME));
    Serial.println("Firmware   : " + String(MB_FIRMWARE_VER));
    Serial.println("Sesiones   : " + String(MAX_SESSIONS));
    Serial.println("-------------------------------------");

    // HTTP + WebSocket
    httpServer.on("/", handleRoot);
    httpServer.onNotFound(handleNotFound);
    httpServer.begin();
    Serial.println("[HTTP] Puerto 80 listo");

    wsServer.begin();
    wsServer.onEvent(webSocketEvent);
    Serial.println("[WS]   Puerto 81 listo");

    // Tarea de aceptacion Modbus TCP en Core 0 (alta prioridad)
    xTaskCreatePinnedToCore(
        acceptTask,
        "mb_accept",
        6144,
        nullptr,
        10,    // prioridad alta para no perder conexiones
        nullptr,
        0      // Core 0
    );
    Serial.printf("[MB] Servidor Modbus TCP iniciado en puerto %d (%d sesiones max)\n",
                  MODBUS_PORT, MAX_SESSIONS);

    lastServoTime = millis();
}

// ── Loop Principal ───────────────────────────────────────────────────
// Core 1
// Solo maneja: animacion de servos, LED, HTTP, WebSocket
// El Modbus corre completamente en Core 0 / tareas FreeRTOS
void loop() {
    httpServer.handleClient();
    wsServer.loop();

    // Verificar timeout del control exclusivo
    lockCheckTimeout();

    // LED
    bool currentLedState = readCoil(COIL_LED);
    if (currentLedState != lastLedState) {
        digitalWrite(PIN_LED, currentLedState ? HIGH : LOW);
        lastLedState = currentLedState;
        Serial.printf("[LED] %s\n", currentLedState ? "ENCENDIDO" : "APAGADO");
    }

    // Animacion suave de servos
    uint32_t now = millis();
    if (now - lastServoTime >= 15) {
        float dt = now - lastServoTime;
        for (int i = 0; i < NUM_SERVOS; i++) {
            int target = constrain((int)readHreg(REG_SERVO_BASE + i), 0, 180);
            if (target != lastTargetAngles[i]) {
                Serial.printf("[MOD] Servo %d (REG %d) -> %d deg\n",
                              i+1, REG_SERVO_BASE+i, target);
                lastTargetAngles[i] = target;
            }
            if (fabs(currentAngles[i] - target) > 0.01f) {
                float step = VELOCIDAD_SERVO * dt;
                float prev = currentAngles[i];
                currentAngles[i] += (prev < target) ? step : -step;
                if ((prev < target) != (currentAngles[i] < target))
                    currentAngles[i] = target;
                servos[i].write((int)currentAngles[i]);
            }
        }
        lastServoTime = now;
    }

    // Broadcast WebSocket
    if (millis() - lastWsBroadcast >= WS_INTERVAL) {
        broadcastState();
        lastWsBroadcast = millis();
    }
}
