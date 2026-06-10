import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, simpledialog
import os
from PIL import Image
import threading
import time
import queue

try:
    from pymodbus.client import ModbusTcpClient
    MODBUS_DISPONIBLE = True
except ImportError:
    MODBUS_DISPONIBLE = False

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("dark-blue")

C_NAVY    = "#1c3a5e"
C_NAVY2   = "#0a1e36"
C_GRAY    = "#c8c8c8"
C_GRAY2   = "#e8e8e8"
C_GRAY3   = "#d0d0d0"
C_WHITE   = "#ffffff"
C_GREEN   = "#22aa22"
C_YELLOW  = "#ddaa00"
C_RED     = "#cc2222"
C_ORANGE  = "#cc6600"
C_TEXT    = "#1a1a1a"
C_TEXT2   = "#444444"
C_TEXT3   = "#888888"
C_BLUE    = "#2980b9"
C_GROUP   = "#8B1A1A"   # borde rojo de grupo


# ══════════════════════════════════════════════════════════════════════
# SLIDER + ENTRY NUMÉRICO combinados
# ══════════════════════════════════════════════════════════════════════
class ServoControl(tk.Frame):
    """Slider horizontal + campo numérico editable, sincronizados."""
    def __init__(self, master, label="S?", reg=0, init_val=90, command=None, bg=C_GRAY2):
        super().__init__(master, bg=bg)
        self.command = command
        self._updating = False

        # Etiqueta
        tk.Label(self, text=label, bg=bg, fg=C_NAVY,
                 font=("Courier New", 9, "bold"), width=13, anchor="w").pack(side="left")
        tk.Label(self, text=f"R{reg}", bg=bg, fg=C_TEXT3,
                 font=("Courier New", 8), width=4).pack(side="left")

        # Entry numérico
        self.var = tk.StringVar(value=str(init_val))
        self.entry = tk.Entry(self, textvariable=self.var, width=5,
                              font=("Courier New", 10, "bold"),
                              bg=C_WHITE, fg=C_NAVY, relief="solid", bd=1,
                              justify="center", insertbackground=C_NAVY)
        self.entry.pack(side="left", padx=(4, 2))
        tk.Label(self, text="°", bg=bg, fg=C_TEXT2,
                 font=("Courier New", 9)).pack(side="left", padx=(0, 4))

        # Slider canvas
        self.canvas = tk.Canvas(self, height=22, bg=C_NAVY2,
                                highlightthickness=1, highlightbackground="#555",
                                cursor="sb_h_double_arrow")
        self.canvas.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._rect = self.canvas.create_rectangle(0, 0, 0, 22, fill=C_BLUE, outline="")
        self._dot  = self.canvas.create_oval(0, 3, 14, 19, fill="white", outline=C_GRAY3)

        self._value = int(init_val)
        self.canvas.bind("<Configure>", lambda e: self._draw_slider())
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Button-1>",  self._on_drag)
        self.var.trace_add("write", self._on_entry_change)

        self._draw_slider()

    @property
    def value(self):
        return self._value

    def set_value(self, v):
        v = int(max(0, min(180, v)))
        self._value = v
        self._updating = True
        self.var.set(str(v))
        self._updating = False
        self._draw_slider()

    def _draw_slider(self):
        w = self.canvas.winfo_width() or 200
        frac = self._value / 180
        xr = frac * w
        self.canvas.coords(self._rect, 0, 0, xr, 22)
        cx = max(7, min(xr, w - 7))
        self.canvas.coords(self._dot, cx - 7, 3, cx + 7, 19)

    def _on_drag(self, event):
        w = self.canvas.winfo_width() or 200
        v = int(max(0, min(180, (event.x / w) * 180)))
        self.set_value(v)
        if self.command:
            self.command(self._value)

    def _on_entry_change(self, *_):
        if self._updating:
            return
        try:
            v = int(self.var.get())
            v = max(0, min(180, v))
            self._value = v
            self._draw_slider()
            if self.command:
                self.command(v)
        except ValueError:
            pass


# ══════════════════════════════════════════════════════════════════════
# ESTRUCTURA DE DATOS
#   grupo  = { "nombre": str, "posiciones": [ posicion, ... ] }
#   posicion = { "nombre": str,
#                "angulos": [int x6],
#                "delays_ms": [int x6],   ← delay individual por servo
#                "parar_aqui": bool }      ← parar al terminar este grupo
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# DIÁLOGO: editar / crear posición
# ══════════════════════════════════════════════════════════════════════
class DialogoPosicion(ctk.CTkToplevel):
    NOMBRES_S = ["Pinza", "Pinza-Rot.", "Muñeca", "Codo", "Hombro", "Base"]

    def __init__(self, master, titulo="Posición", datos=None):
        super().__init__(master)
        self.title(f"M502 — {titulo}")
        self.geometry("640x560")
        self.resizable(False, False)
        self.configure(fg_color=C_GRAY)
        self.grab_set()
        self.resultado = None

        # Titlebar
        bar = tk.Frame(self, bg=C_NAVY2, height=30)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text=f"  {titulo}", bg=C_NAVY2, fg="white",
                 font=("Courier New", 11, "bold")).pack(side="left", fill="y")

        scroll = ctk.CTkScrollableFrame(self, fg_color=C_GRAY2)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)

        # Nombre
        tk.Label(scroll, text="NOMBRE DE LA POSICIÓN", bg=C_GRAY2, fg=C_NAVY,
                 font=("Courier New", 9, "bold")).pack(anchor="w", padx=6, pady=(6, 2))
        self.entry_nombre = tk.Entry(scroll, font=("Courier New", 11),
                                      bg=C_WHITE, fg=C_TEXT, relief="solid", bd=1)
        nombre_init = datos["nombre"] if datos else ""
        self.entry_nombre.insert(0, nombre_init)
        self.entry_nombre.pack(fill="x", padx=6, pady=(0, 8))

        # Tabla servos
        hdr = tk.Frame(scroll, bg=C_NAVY)
        hdr.pack(fill="x", padx=6)
        for txt, w in [("Servo", 14), ("Ángulo (°)", 10), ("Delay (ms)", 10)]:
            tk.Label(hdr, text=txt, bg=C_NAVY, fg="white",
                     font=("Courier New", 9, "bold"), width=w,
                     anchor="w").pack(side="left", ipady=4, padx=4)

        self.angulos_vars = []
        self.delays_vars  = []

        for i in range(6):
            ang_init   = datos["angulos"][i]   if datos else 90
            delay_init = datos["delays_ms"][i] if datos else 500
            bg = C_WHITE if i % 2 == 0 else "#f0f0f0"
            fila = tk.Frame(scroll, bg=bg)
            fila.pack(fill="x", padx=6)

            tk.Label(fila, text=f"  S{i+1} — {self.NOMBRES_S[i]}",
                     bg=bg, fg=C_NAVY, font=("Courier New", 9, "bold"),
                     width=16, anchor="w").pack(side="left", ipady=6)

            var_ang = tk.StringVar(value=str(ang_init))
            e_ang = tk.Entry(fila, textvariable=var_ang, width=7,
                             font=("Courier New", 10), bg=bg, fg=C_TEXT,
                             relief="solid", bd=1, justify="center")
            e_ang.pack(side="left", padx=8, pady=4)
            self.angulos_vars.append(var_ang)

            var_del = tk.StringVar(value=str(delay_init))
            e_del = tk.Entry(fila, textvariable=var_del, width=8,
                             font=("Courier New", 10), bg=bg, fg=C_ORANGE,
                             relief="solid", bd=1, justify="center")
            e_del.pack(side="left", padx=8, pady=4)
            self.delays_vars.append(var_del)

        # Parar al terminar el grupo
        self.var_parar = tk.BooleanVar(value=datos["parar_aqui"] if datos else False)
        fp = tk.Frame(scroll, bg=C_GRAY2)
        fp.pack(fill="x", padx=6, pady=(10, 4))
        ctk.CTkCheckBox(fp, text="⏹  Parar secuencia al terminar este grupo",
                        variable=self.var_parar,
                        font=ctk.CTkFont("Arial", 10),
                        text_color=C_RED,
                        fg_color=C_RED,
                        hover_color="#aa1111").pack(side="left")

        # Botones
        bf = tk.Frame(self, bg=C_GRAY)
        bf.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(bf, text="CANCELAR", bg=C_GRAY3, fg=C_TEXT,
                  font=("Arial", 10, "bold"), relief="flat", padx=14, pady=6,
                  cursor="hand2", command=self.destroy).pack(side="left", padx=(0, 4))
        tk.Button(bf, text="GUARDAR POSICIÓN", bg=C_NAVY, fg="white",
                  font=("Arial", 10, "bold"), relief="flat", padx=14, pady=6,
                  cursor="hand2", command=self._guardar).pack(side="left")

    def _guardar(self):
        try:
            angulos  = [max(0, min(180, int(v.get()))) for v in self.angulos_vars]
            delays   = [max(0, int(v.get())) for v in self.delays_vars]
        except ValueError:
            messagebox.showerror("Error", "Ingresa valores numéricos válidos.")
            return
        nombre = self.entry_nombre.get().strip() or "Posición"
        self.resultado = {
            "nombre":    nombre,
            "angulos":   angulos,
            "delays_ms": delays,
            "parar_aqui": self.var_parar.get()
        }
        self.destroy()


# ══════════════════════════════════════════════════════════════════════
# HMI PRINCIPAL
# ══════════════════════════════════════════════════════════════════════
class M502HMI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("M502 Robotic Arm — Control System HMI")
        self.geometry("1500x880")
        self.minsize(1300, 760)
        self.configure(fg_color=C_GRAY)

        self.modbus_client  = None
        self._lock_heartbeat_activo = False
        self.led_encendido  = False
        self.ultimos_envios = {}
        self.cola_comandos  = queue.Queue()
        # grupos = [ {"nombre": str, "posiciones": [...]} ]
        self.grupos         = []
        self.ejecutando_seq = False

        self.hilo_trabajador = threading.Thread(target=self._procesador_cola, daemon=True)
        self.hilo_trabajador.start()

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, weight=0)

        self._build_titlebar()
        self._build_menubar()
        self._build_left()
        self._build_center()
        self._build_right()
        self._build_statusbar()
        self._tick_clock()

    # ══ TITLEBAR ══════════════════════════════════════════════════════
    def _build_titlebar(self):
        bar = tk.Frame(self, bg=C_NAVY2, height=34)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        bar.grid_propagate(False)
        tk.Label(bar, text="  M502", bg=C_NAVY2, fg="white",
                 font=("Courier New", 13, "bold")).pack(side="left")
        tk.Label(bar, text="ROBOTIC ARM CONTROL SYSTEM  —  Monitor & Control · Modbus TCP",
                 bg=C_NAVY2, fg="#a8c4e0", font=("Arial", 10)).pack(side="left", padx=10)
        self.lbl_clock = tk.Label(bar, text="--:--:--", bg=C_NAVY2, fg="#7ec8e3",
                                   font=("Courier New", 12, "bold"))
        self.lbl_clock.pack(side="right", padx=16)
        tk.Label(bar, text="IP: ", bg=C_NAVY2, fg="#a8c4e0",
                 font=("Arial", 9)).pack(side="right", padx=(16, 0))
        self.lbl_ip_top = tk.Label(bar, text="--", bg=C_NAVY2, fg="white",
                                    font=("Courier New", 10, "bold"))
        self.lbl_ip_top.pack(side="right")

    # ══ MENUBAR ═══════════════════════════════════════════════════════
    def _build_menubar(self):
        bar = tk.Frame(self, bg=C_GRAY2, height=24, relief="flat", bd=0)
        bar.grid(row=1, column=0, columnspan=3, sticky="ew")
        bar.grid_propagate(False)
        for texto in ["Monitor", "Registros", "Alarmas", "Config."]:
            activo = texto == "Monitor"
            tk.Label(bar, text=texto, bg=C_WHITE if activo else C_GRAY2,
                     fg=C_NAVY if activo else C_TEXT,
                     font=("Arial", 10, "bold" if activo else "normal"),
                     padx=14, pady=2).pack(side="left")
        self.dot_ws = tk.Label(bar, text="●", bg=C_GRAY2, fg=C_RED, font=("Arial", 10))
        self.dot_ws.pack(side="right", padx=(0, 2))
        self.lbl_ws = tk.Label(bar, text="Sin conexión", bg=C_GRAY2, fg=C_TEXT3,
                                font=("Arial", 9))
        self.lbl_ws.pack(side="right", padx=(8, 0))
        tk.Label(bar, text=" | ", bg=C_GRAY2, fg="#aaa", font=("Arial", 9)).pack(side="right")
        self.dot_led = tk.Label(bar, text="●", bg=C_GRAY2, fg=C_YELLOW, font=("Arial", 10))
        self.dot_led.pack(side="right", padx=(0, 2))
        self.lbl_led_mb = tk.Label(bar, text="LED APAGADO", bg=C_GRAY2, fg=C_TEXT3,
                                    font=("Arial", 9))
        self.lbl_led_mb.pack(side="right", padx=(8, 0))
        tk.Label(bar, text=" | Modbus TCP :502 | ", bg=C_GRAY2, fg=C_TEXT3,
                 font=("Arial", 9)).pack(side="right")

    # ══ LEFT ══════════════════════════════════════════════════════════
    def _build_left(self):
        col = tk.Frame(self, bg=C_GRAY)
        col.grid(row=2, column=0, sticky="nsew", padx=(6, 3), pady=5)

        s1 = self._panel(col, "CONEXIÓN MODBUS TCP")
        tk.Label(s1, text="Dirección IP", bg=C_GRAY2, fg=C_TEXT2,
                 font=("Arial", 9)).pack(anchor="w", padx=10, pady=(4, 0))
        self.entry_ip = tk.Entry(s1, font=("Courier New", 11), bg=C_WHITE, fg=C_TEXT,
                                  relief="solid", bd=1, insertbackground=C_TEXT)
        self.entry_ip.insert(0, "10.10.10.")
        self.entry_ip.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(s1, text="Puerto", bg=C_GRAY2, fg=C_TEXT2,
                 font=("Arial", 9)).pack(anchor="w", padx=10)
        self.entry_port = tk.Entry(s1, font=("Courier New", 11), bg=C_WHITE, fg=C_TEXT,
                                    relief="solid", bd=1, insertbackground=C_TEXT)
        self.entry_port.insert(0, "502")
        self.entry_port.pack(fill="x", padx=10, pady=(0, 6))
        self.btn_connect = self._btn(s1, "ENLAZAR SISTEMA", self._conectar, C_NAVY)
        self.btn_connect.pack(fill="x", padx=10, pady=(0, 4))
        self.lbl_estado = tk.Label(s1, text="● Sin conexión", bg=C_GRAY2, fg=C_RED,
                                    font=("Arial", 9, "bold"), anchor="w")
        self.lbl_estado.pack(anchor="w", padx=10, pady=(0, 8))

        # Info fabricante
        s_info = self._panel(col, "INFORMACIÓN DEL FABRICANTE")
        for campo, valor in [
            ("Fabricante",  "M502 Robotics"),
            ("Producto",    "ARM-6DOF-v2"),
            ("Firmware",    "2.1.0"),
            ("Modelo",      "ARM-6DOF-ESP32"),
            ("Controlador", "ESP32"),
            ("Protocolo",   "Modbus TCP / PWM"),
        ]:
            f = tk.Frame(s_info, bg=C_GRAY2)
            f.pack(fill="x", padx=10, pady=1)
            tk.Label(f, text=f"{campo}:", bg=C_GRAY2, fg=C_TEXT3,
                     font=("Courier New", 8), width=11, anchor="w").pack(side="left")
            tk.Label(f, text=valor, bg=C_GRAY2, fg=C_NAVY,
                     font=("Courier New", 9, "bold"), anchor="w").pack(side="left")
        tk.Label(s_info, text="", bg=C_GRAY2).pack(pady=2)

        # Acciones
        s2 = self._panel(col, "ACCIONES")
        self._btn(s2, "GUARDAR POSICIÓN ACTUAL", self._guardar_pos_actual, C_NAVY).pack(
            fill="x", padx=10, pady=4)
        self._btn(s2, "RESETEAR POSICIONES", self._resetear, C_NAVY).pack(
            fill="x", padx=10, pady=(0, 8))

        # LED
        s3 = self._panel(col, "LED — GPIO2  |  COIL 0")
        self.lbl_led_lamp = tk.Label(s3, text="  ●  APAGADO", bg=C_GRAY2, fg=C_TEXT3,
                                      font=("Courier New", 11, "bold"), anchor="w")
        self.lbl_led_lamp.pack(fill="x", padx=10, pady=(2, 4))
        self.btn_led = self._btn(s3, "ENCENDER LED", self._toggle_led, "#2c4a6e")
        self.btn_led.pack(fill="x", padx=10, pady=(0, 8))

        self._btn(col, "SALIR", self._cerrar, C_RED).pack(fill="x", padx=0, pady=(8, 0))

    # ══ CENTER ════════════════════════════════════════════════════════
    def _build_center(self):
        col = tk.Frame(self, bg=C_GRAY)
        col.grid(row=2, column=1, sticky="nsew", padx=3, pady=5)
        col.grid_rowconfigure(1, weight=1)
        col.grid_columnconfigure(0, weight=1)

        hdr = tk.Frame(col, bg=C_NAVY, height=26)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        tk.Label(hdr, text="  CONTROL MANUAL — Servomotores (slider + valor numérico)",
                 bg=C_NAVY, fg="white", font=("Courier New", 10, "bold")).pack(side="left", fill="y")

        outer = tk.Frame(col, bg=C_GRAY2, bd=1, relief="solid")
        outer.grid(row=1, column=0, sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_columnconfigure(1, weight=1)

        # Panel imagen
        frame_img = tk.Frame(outer, bg="#eeeeee", bd=1, relief="solid")
        frame_img.grid(row=0, column=0, sticky="nsew", padx=(6, 3), pady=6)
        if os.path.exists("robot.png"):
            from PIL import Image as PILImage
            img = PILImage.open("robot.png")
            ci  = ctk.CTkImage(light_image=img, size=(200, 380))
            ctk.CTkLabel(frame_img, text="", image=ci).pack(expand=True)
        else:
            tk.Label(frame_img, text="M502\nROBOT\nARM", bg="#eeeeee", fg=C_GRAY3,
                     font=("Courier New", 18, "bold"), justify="center").pack(expand=True)

        # Panel controles
        frame_ctrl = tk.Frame(outer, bg=C_GRAY2)
        frame_ctrl.grid(row=0, column=1, sticky="nsew", padx=(3, 6), pady=6)

        ph = tk.Frame(frame_ctrl, bg=C_NAVY2, height=22)
        ph.pack(fill="x")
        ph.pack_propagate(False)
        tk.Label(ph, text="  SERVO   REG    ÁNGULO    DELAY/SERVO →  POSICIÓN",
                 bg=C_NAVY2, fg="#a8c4e0", font=("Courier New", 8, "bold")).pack(side="left", fill="y")

        self.servo_ctrls = []
        NOMBRES = ["Pinza", "Pinza-Rot.", "Muñeca", "Codo", "Hombro", "Base"]
        REGS    = [100, 101, 102, 103, 104, 105]
        for i in range(6):
            sc = ServoControl(
                frame_ctrl,
                label=f"S{i+1} — {NOMBRES[i]}",
                reg=REGS[i],
                init_val=90,
                command=lambda v, idx=i: self._enviar("servo", idx + 1, v),
                bg=C_WHITE if i % 2 == 0 else C_GRAY2
            )
            sc.pack(fill="x", padx=4, pady=2)
            self.servo_ctrls.append(sc)

        # Delay de esta posición (campo debajo de los servos)
        df = tk.Frame(frame_ctrl, bg=C_GRAY2)
        df.pack(fill="x", padx=4, pady=(8, 2))
        tk.Label(df, text="Delay global posición (ms):", bg=C_GRAY2, fg=C_TEXT2,
                 font=("Arial", 9)).pack(side="left")
        self.entry_delay_global = tk.Entry(df, width=7, font=("Courier New", 10),
                                            bg=C_WHITE, fg=C_TEXT, relief="solid", bd=1)
        self.entry_delay_global.insert(0, "500")
        self.entry_delay_global.pack(side="left", padx=(4, 0))
        tk.Label(df, text="(se usa al guardar si no se cambia cada servo)",
                 bg=C_GRAY2, fg=C_TEXT3, font=("Arial", 8)).pack(side="left", padx=6)

    # ══ RIGHT ═════════════════════════════════════════════════════════
    def _build_right(self):
        col = tk.Frame(self, bg=C_GRAY)
        col.grid(row=2, column=2, sticky="nsew", padx=(3, 6), pady=5)
        col.grid_rowconfigure(1, weight=1)
        col.grid_columnconfigure(0, weight=1)

        # Header
        shdr = tk.Frame(col, bg=C_GRAY2, bd=1, relief="solid")
        shdr.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ph = tk.Frame(shdr, bg=C_NAVY, height=24)
        ph.pack(fill="x")
        ph.pack_propagate(False)
        tk.Label(ph, text="  SECUENCIAS — GRUPOS DE MOVIMIENTO",
                 bg=C_NAVY, fg="white", font=("Courier New", 10, "bold")).pack(side="left", fill="y")

        tk.Label(shdr, text="  Agrupa posiciones; cada grupo puede pausar la secuencia al terminar",
                 bg=C_GRAY2, fg=C_TEXT3, font=("Arial", 9)).pack(anchor="w", pady=(4, 2), padx=4)

        bf = tk.Frame(shdr, bg=C_GRAY2)
        bf.pack(fill="x", padx=8, pady=(2, 8))
        self._btn(bf, "+  NUEVO GRUPO", self._nuevo_grupo, C_NAVY2).pack(
            side="left", padx=(0, 4))
        self._btn(bf, "+  AÑADIR POSICIÓN", self._nueva_posicion_dialogo, C_NAVY).pack(
            side="left", padx=(0, 4))
        self._btn(bf, "GUARDAR ACTUAL →", self._guardar_pos_actual, C_BLUE).pack(side="left")

        # Lista scrollable
        wrap = tk.Frame(col, bg=C_GRAY2, bd=1, relief="solid")
        wrap.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        lh = tk.Frame(wrap, bg=C_NAVY, height=22)
        lh.grid(row=0, column=0, sticky="ew")
        lh.grid_propagate(False)
        tk.Label(lh, text="  GRUPOS Y POSICIONES CARGADAS",
                 bg=C_NAVY, fg="#a8c4e0", font=("Courier New", 9, "bold")).pack(side="left", fill="y")

        self.frame_lista = ctk.CTkScrollableFrame(wrap, fg_color="transparent")
        self.frame_lista.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.frame_lista.grid_columnconfigure(0, weight=1)
        self._refresh_lista()

        # Panel ejecución
        ep = tk.Frame(col, bg=C_GRAY2, bd=1, relief="solid")
        ep.grid(row=2, column=0, sticky="ew")

        eh = tk.Frame(ep, bg=C_NAVY, height=22)
        eh.pack(fill="x")
        eh.pack_propagate(False)
        tk.Label(eh, text="  EJECUCIÓN", bg=C_NAVY, fg="#a8c4e0",
                 font=("Courier New", 9, "bold")).pack(side="left", fill="y")

        opt = tk.Frame(ep, bg=C_GRAY2)
        opt.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(opt, text="Reps:", bg=C_GRAY2, fg=C_TEXT2, font=("Arial", 9)).pack(side="left")
        self.entry_rep = tk.Entry(opt, width=4, font=("Courier New", 10),
                                   bg=C_WHITE, fg=C_TEXT, relief="solid", bd=1)
        self.entry_rep.insert(0, "1")
        self.entry_rep.pack(side="left", padx=(4, 10))
        self.var_loop = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt, text="Loop ∞", variable=self.var_loop,
                        font=ctk.CTkFont("Arial", 9), text_color=C_TEXT2).pack(side="left")
        tk.Label(opt, text="  Pausa ciclo (s):", bg=C_GRAY2, fg=C_TEXT2,
                 font=("Arial", 9)).pack(side="left", padx=(10, 0))
        self.entry_pausa = tk.Entry(opt, width=4, font=("Courier New", 10),
                                     bg=C_WHITE, fg=C_TEXT, relief="solid", bd=1)
        self.entry_pausa.insert(0, "2")
        self.entry_pausa.pack(side="left", padx=(4, 0))

        self.barra_prog = ctk.CTkProgressBar(ep, height=8, fg_color=C_GRAY3,
                                              progress_color=C_GREEN)
        self.barra_prog.set(0)
        self.barra_prog.pack(fill="x", padx=8, pady=(4, 2))
        self.lbl_prog = tk.Label(ep, text="Listo", bg=C_GRAY2, fg=C_TEXT3,
                                  font=("Courier New", 8), anchor="w")
        self.lbl_prog.pack(fill="x", padx=8, pady=(0, 4))

        br = tk.Frame(ep, bg=C_GRAY2)
        br.pack(fill="x", padx=8, pady=(0, 8))
        self.btn_ejecutar = self._btn(br, "▶  EJECUTAR SECUENCIA",
                                       self._ejecutar_secuencia, C_NAVY)
        self.btn_ejecutar.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._btn(br, "■  STOP", self._detener_secuencia, C_RED, width=70).pack(side="right")

    # ══ STATUSBAR ═════════════════════════════════════════════════════
    def _build_statusbar(self):
        bar = tk.Frame(self, bg=C_GRAY3, height=20, bd=0)
        bar.grid(row=3, column=0, columnspan=3, sticky="ew")
        bar.grid_propagate(False)
        for txt in ["PROTOCOLO: Modbus TCP", "PWM: 50 Hz",
                    "PULSO: 500–2400 µs", "SERVOS: 6-DOF", "MODELO: ESP32"]:
            f = tk.Frame(bar, bg=C_GRAY3)
            f.pack(side="left")
            tk.Label(f, text=txt, bg=C_GRAY3, fg=C_TEXT3,
                     font=("Courier New", 8), padx=10, pady=2).pack(side="left")
            tk.Frame(f, bg="#aaaaaa", width=1, height=18).pack(side="left")
        self.lbl_sb_time = tk.Label(bar, text="--:--:--", bg=C_GRAY3, fg=C_TEXT3,
                                     font=("Courier New", 8), padx=10)
        self.lbl_sb_time.pack(side="right")

    # ══ HELPERS ═══════════════════════════════════════════════════════
    def _panel(self, parent, titulo):
        outer = tk.Frame(parent, bg=C_GRAY2, bd=1, relief="solid")
        outer.pack(fill="x", pady=(0, 5))
        ph = tk.Frame(outer, bg=C_NAVY, height=22)
        ph.pack(fill="x")
        ph.pack_propagate(False)
        tk.Label(ph, text=f"  {titulo}", bg=C_NAVY, fg="white",
                 font=("Courier New", 9, "bold")).pack(side="left", fill="y")
        return outer

    @staticmethod
    def _btn(parent, texto, cmd, color, width=None):
        kw = dict(text=texto, command=cmd, bg=color, fg="white",
                  font=("Arial", 10, "bold"), relief="flat", bd=0,
                  padx=10, pady=6, cursor="hand2",
                  activebackground=color, activeforeground="white")
        if width:
            kw["width"] = width
        return tk.Button(parent, **kw)

    def _tick_clock(self):
        import datetime
        ahora = datetime.datetime.now()
        clk = ahora.strftime("%H:%M:%S")
        self.lbl_clock.configure(text=clk)
        if hasattr(self, "lbl_sb_time"):
            self.lbl_sb_time.configure(text=clk)
        self.after(1000, self._tick_clock)

    # ══ GRUPOS Y POSICIONES ═══════════════════════════════════════════
    def _nuevo_grupo(self):
        nombre = simpledialog.askstring(
            "Nuevo grupo", "Nombre del grupo:", initialvalue=f"Grupo {len(self.grupos)+1}")
        if nombre:
            self.grupos.append({"nombre": nombre, "posiciones": []})
            self._refresh_lista()

    def _nueva_posicion_dialogo(self, grupo_idx=None, editar_pos_idx=None):
        """Abre el diálogo para crear o editar una posición."""
        datos_init = None
        titulo     = "Nueva Posición"
        if editar_pos_idx is not None and grupo_idx is not None:
            datos_init = self.grupos[grupo_idx]["posiciones"][editar_pos_idx]
            titulo     = f"Editar Posición #{editar_pos_idx+1}"

        dlg = DialogoPosicion(self, titulo=titulo, datos=datos_init)
        self.wait_window(dlg)
        if dlg.resultado is None:
            return

        pos = dlg.resultado

        if editar_pos_idx is not None and grupo_idx is not None:
            self.grupos[grupo_idx]["posiciones"][editar_pos_idx] = pos
        else:
            # Elegir grupo destino
            if not self.grupos:
                self.grupos.append({"nombre": "Grupo 1", "posiciones": []})
            if len(self.grupos) == 1 or grupo_idx is not None:
                gi = grupo_idx if grupo_idx is not None else 0
            else:
                # Preguntar a qué grupo
                nombres = [f"{i+1}. {g['nombre']}" for i, g in enumerate(self.grupos)]
                win = ctk.CTkToplevel(self)
                win.title("Elegir grupo")
                win.geometry("300x200")
                win.configure(fg_color=C_GRAY)
                win.grab_set()
                gi_var = tk.IntVar(value=0)
                tk.Label(win, text="Agregar al grupo:", bg=C_GRAY, fg=C_TEXT,
                         font=("Arial", 10)).pack(pady=8)
                for i, n in enumerate(nombres):
                    tk.Radiobutton(win, text=n, variable=gi_var, value=i,
                                   bg=C_GRAY, fg=C_TEXT).pack(anchor="w", padx=20)
                result = [None]
                def _ok():
                    result[0] = gi_var.get()
                    win.destroy()
                tk.Button(win, text="OK", bg=C_NAVY, fg="white",
                          font=("Arial", 10, "bold"), relief="flat",
                          command=_ok).pack(pady=10)
                self.wait_window(win)
                gi = result[0] if result[0] is not None else 0

            self.grupos[gi]["posiciones"].append(pos)

        self._refresh_lista()

    def _refresh_lista(self):
        for w in self.frame_lista.winfo_children():
            w.destroy()
        if not self.grupos:
            tk.Label(self.frame_lista,
                     text="Sin grupos.\nUsa '+ NUEVO GRUPO'.",
                     font=("Arial", 9), fg="#cccccc", justify="center").pack(pady=30)
            return
        for gi, grupo in enumerate(self.grupos):
            self._render_grupo(gi, grupo)

    def _render_grupo(self, gi, grupo):
        # Marco externo con borde rojo = estilo grupo
        outer = tk.Frame(self.frame_lista, bg=C_GROUP, bd=2, relief="solid")
        outer.pack(fill="x", pady=(0, 8), padx=2)

        # Header del grupo
        gh = tk.Frame(outer, bg=C_GROUP)
        gh.pack(fill="x")
        tk.Label(gh, text=f"  ▣  {grupo['nombre']}",
                 bg=C_GROUP, fg="white",
                 font=("Courier New", 10, "bold")).pack(side="left", ipady=5, padx=4)
        # Botones del grupo
        for txt, cmd, col in [
            ("+ POS", lambda g=gi: self._nueva_posicion_dialogo(grupo_idx=g), C_NAVY),
            ("✎", lambda g=gi: self._renombrar_grupo(g), C_YELLOW),
            ("✕", lambda g=gi: self._borrar_grupo(g), C_RED),
        ]:
            tk.Button(gh, text=txt, command=cmd, bg=col, fg="white",
                      font=("Arial", 8, "bold"), relief="flat", bd=0,
                      padx=6, pady=3, cursor="hand2",
                      activebackground=col).pack(side="right", padx=2, pady=3)

        inner = tk.Frame(outer, bg=C_GRAY2)
        inner.pack(fill="x", padx=2, pady=(0, 2))

        if not grupo["posiciones"]:
            tk.Label(inner, text="  Sin posiciones en este grupo.",
                     bg=C_GRAY2, fg=C_TEXT3, font=("Arial", 9)).pack(anchor="w", pady=6)
        else:
            for pi, pos in enumerate(grupo["posiciones"]):
                self._render_posicion(inner, gi, pi, pos)

    def _render_posicion(self, parent, gi, pi, pos):
        bg = C_WHITE if pi % 2 == 0 else "#f4f4f4"
        card = tk.Frame(parent, bg=bg, bd=0)
        card.pack(fill="x", pady=1)

        # Header posición
        ph = tk.Frame(card, bg=C_NAVY)
        ph.pack(fill="x")
        parar_txt = "  ⏹PARAR" if pos.get("parar_aqui") else ""
        tk.Label(ph, text=f"  #{pi+1}  {pos['nombre']}{parar_txt}",
                 bg=C_NAVY, fg="white" if not parar_txt else "#ff9999",
                 font=("Courier New", 9, "bold")).pack(side="left", ipady=3)

        # Ángulos
        ang_txt = "  ".join([f"S{j+1}:{pos['angulos'][j]}°" for j in range(6)])
        tk.Label(card, text=f"  {ang_txt}", bg=bg, fg=C_TEXT2,
                 font=("Courier New", 8), anchor="w").pack(fill="x", padx=2, pady=(2, 0))

        # Delays individuales
        del_txt = "  ".join([f"S{j+1}:{pos['delays_ms'][j]}ms" for j in range(6)])
        tk.Label(card, text=f"  ⏱ {del_txt}", bg=bg, fg=C_ORANGE,
                 font=("Courier New", 8), anchor="w").pack(fill="x", padx=2, pady=(0, 2))

        # Botones
        br = tk.Frame(card, bg=bg)
        br.pack(fill="x", padx=4, pady=(0, 4))
        for txt, cmd, col, w in [
            ("▲", lambda g=gi, p=pi: self._mover_pos(g, p, -1), C_GRAY3, 24),
            ("▼", lambda g=gi, p=pi: self._mover_pos(g, p,  1), C_GRAY3, 24),
            ("↗ APLICAR", lambda g=gi, p=pi: self._aplicar_pos(g, p), C_BLUE, 80),
            ("✎ EDITAR",  lambda g=gi, p=pi: self._nueva_posicion_dialogo(gi, p), C_YELLOW, 72),
        ]:
            tk.Button(br, text=txt, command=cmd,
                      bg=col, fg=C_TEXT if col == C_GRAY3 else "white",
                      font=("Arial", 8, "bold"), relief="flat", bd=0,
                      padx=4, pady=3, cursor="hand2", width=w,
                      activebackground=col).pack(side="left", padx=(0, 2))
        tk.Button(br, text="✕", command=lambda g=gi, p=pi: self._borrar_pos(g, p),
                  bg=C_RED, fg="white", font=("Arial", 8, "bold"), relief="flat",
                  bd=0, padx=6, pady=3, cursor="hand2",
                  activebackground=C_RED).pack(side="right")

    def _renombrar_grupo(self, gi):
        nombre = simpledialog.askstring(
            "Renombrar", "Nuevo nombre:", initialvalue=self.grupos[gi]["nombre"])
        if nombre:
            self.grupos[gi]["nombre"] = nombre
            self._refresh_lista()

    def _borrar_grupo(self, gi):
        if messagebox.askyesno("Borrar grupo", f"¿Borrar '{self.grupos[gi]['nombre']}' y todas sus posiciones?"):
            self.grupos.pop(gi)
            self._refresh_lista()

    def _mover_pos(self, gi, pi, delta):
        poss = self.grupos[gi]["posiciones"]
        ni = pi + delta
        if 0 <= ni < len(poss):
            poss[pi], poss[ni] = poss[ni], poss[pi]
            self._refresh_lista()

    def _borrar_pos(self, gi, pi):
        self.grupos[gi]["posiciones"].pop(pi)
        self._refresh_lista()

    def _aplicar_pos(self, gi, pi):
        pos = self.grupos[gi]["posiciones"][pi]
        for i in range(6):
            self.servo_ctrls[i].set_value(pos["angulos"][i])
            self._enviar("servo", i + 1, pos["angulos"][i])

    def _guardar_pos_actual(self, grupo_idx=None):
        """Guarda los valores actuales de los sliders como nueva posición."""
        angulos = [sc.value for sc in self.servo_ctrls]
        try:
            delay_g = int(self.entry_delay_global.get())
        except (ValueError, AttributeError):
            delay_g = 500
        datos = {
            "nombre":    f"Posición {sum(len(g['posiciones']) for g in self.grupos)+1}",
            "angulos":   angulos,
            "delays_ms": [delay_g] * 6,
            "parar_aqui": False
        }
        if not self.grupos:
            self.grupos.append({"nombre": "Grupo 1", "posiciones": []})
        gi = grupo_idx if grupo_idx is not None else len(self.grupos) - 1
        self.grupos[gi]["posiciones"].append(datos)
        self._refresh_lista()

    def _resetear(self):
        for i, sc in enumerate(self.servo_ctrls):
            sc.set_value(90)
            self._enviar("servo", i + 1, 90)

    # ══ EJECUCIÓN ═════════════════════════════════════════════════════
    def _ejecutar_secuencia(self):
        total_pos = sum(len(g["posiciones"]) for g in self.grupos)
        if total_pos == 0:
            messagebox.showwarning("Sin posiciones", "No hay posiciones cargadas.")
            return
        if self.ejecutando_seq:
            return
        self.ejecutando_seq = True
        self.btn_ejecutar.configure(state="disabled", text="⏳ Ejecutando...")
        threading.Thread(target=self._hilo_ejecucion, daemon=True).start()

    def _hilo_ejecucion(self):
        try:
            reps = int(self.entry_rep.get())
        except ValueError:
            reps = 1
        try:
            pausa_ciclo = float(self.entry_pausa.get())
        except ValueError:
            pausa_ciclo = 2.0
        loop  = self.var_loop.get()
        ciclo = 0

        while self.ejecutando_seq:
            ciclo += 1
            total_pos = sum(len(g["posiciones"]) for g in self.grupos)
            ejecutadas = 0

            for gi, grupo in enumerate(self.grupos):
                if not self.ejecutando_seq:
                    break

                for pi, pos in enumerate(grupo["posiciones"]):
                    if not self.ejecutando_seq:
                        break
                    ejecutadas += 1
                    pct  = ejecutadas / max(total_pos, 1)
                    name = pos["nombre"]
                    gname = grupo["nombre"]
                    self.after(0, lambda p=pct, nm=name, gn=gname:
                               self._set_prog(p, f"[{gn}] #{pi+1} {nm}"))

                    # Aplicar ángulos con delay individual por servo
                    for j in range(6):
                        if not self.ejecutando_seq:
                            break
                        v = pos["angulos"][j]
                        dj = pos["delays_ms"][j] / 1000.0
                        self.after(0, lambda sc=self.servo_ctrls[j], vv=v:
                                   sc.set_value(vv))
                        self._enviar("servo", j + 1, v)
                        time.sleep(dj)

                # Parar al terminar el grupo si alguna posición lo indica
                if pos.get("parar_aqui") and self.ejecutando_seq:
                    self.ejecutando_seq = False
                    self.after(0, lambda gn=gname:
                               self._set_prog(1.0, f"⏹ Detenido al terminar [{gn}]"))
                    break

            if not self.ejecutando_seq:
                break

            if loop:
                self.after(0, lambda: self._set_prog(
                    1.0, f"⏸ Pausa entre ciclos ({pausa_ciclo:.1f} s)..."))
                restante = pausa_ciclo
                while restante > 0 and self.ejecutando_seq:
                    time.sleep(min(0.1, restante))
                    restante -= 0.1
            else:
                if ciclo >= reps:
                    break

        self.ejecutando_seq = False
        self.after(0, lambda: (
            self.btn_ejecutar.configure(state="normal", text="▶  EJECUTAR SECUENCIA"),
            self._set_prog(0, "Listo")))

    def _detener_secuencia(self):
        self.ejecutando_seq = False

    def _set_prog(self, val, txt):
        self.barra_prog.set(val)
        self.lbl_prog.configure(text=txt)

    # ══ MODBUS ════════════════════════════════════════════════════════
    def _toggle_led(self):
        self.led_encendido = not self.led_encendido
        if self.led_encendido:
            self.btn_led.configure(text="APAGAR LED", bg=C_YELLOW)
            self.lbl_led_lamp.configure(text="  ●  ENCENDIDO", fg=C_YELLOW)
            self.lbl_led_mb.configure(text="LED ENCENDIDO", fg=C_YELLOW)
            self.dot_led.configure(fg=C_YELLOW)
        else:
            self.btn_led.configure(text="ENCENDER LED", bg="#2c4a6e")
            self.lbl_led_lamp.configure(text="  ●  APAGADO", fg=C_TEXT3)
            self.lbl_led_mb.configure(text="LED APAGADO", fg=C_TEXT3)
            self.dot_led.configure(fg=C_TEXT3)
        self._enviar("led", 0, 1 if self.led_encendido else 0)

    # ══ MODBUS + LOCK EXCLUSIVO ════════════════════════════════════════

    # Registros de control exclusivo (deben coincidir con el firmware)
    REG_LOCK_CMD   = 200
    REG_LOCK_TOKEN = 201
    REG_LOCK_TTL   = 202
    REG_LOCK_OWNER = 203
    LOCK_MAGIC     = 0xA5A5

# CONEXION MODBUS
    def _conectar(self):
        if not MODBUS_DISPONIBLE:
            self.btn_connect.configure(text="pymodbus no instalado", bg=C_RED)
            return
        try:
            ip     = self.entry_ip.get()
            puerto = int(self.entry_port.get())
            self.modbus_client = ModbusTcpClient(ip, port=puerto, timeout=2.0)
            if self.modbus_client.connect():
                self.btn_connect.configure(text="EN LÍNEA ✓", bg=C_GREEN)
                self.lbl_estado.configure(text="● Conectado", fg=C_GREEN)
                self.dot_ws.configure(fg=C_GREEN)
                self.lbl_ws.configure(text=f"Conectado a {ip}")
                self.lbl_ip_top.configure(text=ip)
                # Iniciar hilo de heartbeat del lock
                self._lock_heartbeat_activo = True
                threading.Thread(target=self._hilo_lock_heartbeat, daemon=True).start()
            else:
                self.btn_connect.configure(text="ERROR DE ENLACE", bg=C_RED)
                self.lbl_estado.configure(text="● Sin conexión", fg=C_RED)
                self.dot_ws.configure(fg=C_RED)
                self.lbl_ws.configure(text="Sin conexión")
        except Exception as e:
            self.btn_connect.configure(text="ERROR DE ENLACE", bg=C_RED)
            self.lbl_estado.configure(text=f"● Error: {e}", fg=C_RED)

    def _asegurar_conexion(self):
        """Verifica y reconecta el socket si está caído."""
        if not MODBUS_DISPONIBLE or self.modbus_client is None:
            return False
        if self.modbus_client.is_socket_open():
            return True
        try:
            print("[MODBUS] Reconectando...")
            self.modbus_client.close()
            ok = self.modbus_client.connect()
            if ok:
                print("[MODBUS] Reconexión OK")
                self.after(0, lambda: (
                    self.btn_connect.configure(text="EN LÍNEA ✓", bg=C_GREEN),
                    self.lbl_estado.configure(text="● Conectado", fg=C_GREEN),
                ))
            return ok
        except Exception as e:
            print(f"[MODBUS] Error reconectando: {e}")
            return False

# Adquiere el control exclusivo del PLC escribiendo 0xA5A5 en REG 200.
    def _lock_adquirir(self):
        if not self._asegurar_conexion():
            return False
        try:
            r = self.modbus_client.write_register(self.REG_LOCK_CMD, self.LOCK_MAGIC)
            if r.isError():
                print("[LOCK] Firmware rechazó el lock (otro cliente lo tiene)")
                return False
            print("[LOCK] Control exclusivo ADQUIRIDO (REG 200 = 0xA5A5)")
            return True
        except Exception as e:
            print(f"[LOCK] Error adquiriendo lock: {e}")
            return False

    def _lock_liberar(self):
        """Libera el control exclusivo escribiendo 0x0000 en REG 200."""
        if not self._asegurar_conexion():
            return
        try:
            self.modbus_client.write_register(self.REG_LOCK_CMD, 0x0000)
            print("[LOCK] Control exclusivo LIBERADO (REG 200 = 0x0000)")
        except Exception as e:
            print(f"[LOCK] Error liberando lock: {e}")

# =========== FUNCIONES DE LOCK ===========
# Lee los registros 200-203 para saber si hay lock activo y quién lo tiene.
    def _lock_leer_estado(self):
        if not self._asegurar_conexion():
            return None
        try:
            r = self.modbus_client.read_holding_registers(self.REG_LOCK_CMD, count=4)
            if r.isError():
                return None
            cmd, token, ttl, owner = r.registers
            active = (cmd == self.LOCK_MAGIC)
            return {"active": active, "token": token, "ttl": ttl, "owner": owner}
        except Exception:
            return None

# 1. Lee el estado del lock en el PLC
# 2. Si el lock está activo pero no es nuestro muestra alerta en UI
# 3. Si se esta ejecutando una secuencia y el lock está bloqueado por un atacante pausa la secuencia y espera hasta recuperarlo
# El HMI legítimo NO adquiere el lock proactivamente; solo monitorea y reacciona. 
    def _hilo_lock_heartbeat(self):
        while self._lock_heartbeat_activo:
            estado = self._lock_leer_estado()
            if estado:
                if estado["active"]:
                    # Hay lock activo
                    ttl = estado["ttl"]
                    owner = estado["owner"]
                    # Actualizar UI con advertencia
                    self.after(0, lambda t=ttl, o=owner: self._ui_lock_alerta(o, t))
                else:
                    # Sin lock: UI normal
                    self.after(0, self._ui_lock_libre)
            time.sleep(3)

    def _ui_lock_alerta(self, owner, ttl):
        """Muestra en la barra de estado que hay un lock activo (posible ataque)."""
        try:
            if hasattr(self, "lbl_estado"):
                self.lbl_estado.configure(
                    text=f"⚠ LOCK ACTIVO — Sesión #{owner} | TTL: {ttl}s",
                    fg=C_RED
                )
        except Exception:
            pass

    def _ui_lock_libre(self):
        """Restaura la barra de estado cuando el lock se libera."""
        try:
            if hasattr(self, "lbl_estado") and self.modbus_client and self.modbus_client.is_socket_open():
                self.lbl_estado.configure(text="● Conectado", fg=C_GREEN)
        except Exception:
            pass

# Mandar codigos de funcion. No envía directamente. Solo pone el comando en una cola para que sea procesado de forma ordenada
    def _enviar(self, dispositivo, id_disp, valor):
        clave = f"{dispositivo}_{id_disp}"
        ahora = time.time()
        if clave in self.ultimos_envios and (ahora - self.ultimos_envios[clave]) < 0.05:
            return
        self.ultimos_envios[clave] = ahora
        self.cola_comandos.put((dispositivo, id_disp, valor))

# Envio de codigos de funcion
    def _procesador_cola(self):
        while True:
            dispositivo, id_disp, valor = self.cola_comandos.get()
            if MODBUS_DISPONIBLE and self.modbus_client:
                if self._asegurar_conexion():
                    try:
                        if dispositivo == "servo":
                            r = self.modbus_client.write_register(
                                100 + (id_disp - 1), int(valor))
                            if hasattr(r, 'isError') and r.isError():
                                # Excepción 0x06 = Slave Device Busy (lock activo)
                                # Reencolar tras 1s para reintentar cuando se libere
                                print(f"[HMI] Registro bloqueado (lock activo), reintentando en 1s...")
                                def _reencolar(d=dispositivo, i=id_disp, v=valor):
                                    time.sleep(1.0)
                                    self.cola_comandos.put((d, i, v))
                                threading.Thread(target=_reencolar, daemon=True).start()
                        elif dispositivo == "led":
                            self.modbus_client.write_coil(0, bool(valor))
                    except Exception as e:
                        print(f"[MODBUS] Error al enviar: {e}")
                        try:
                            self.modbus_client.close()
                        except Exception:
                            pass
                else:
                    # Sin conexión: reencolar
                    def _reencolar2(d=dispositivo, i=id_disp, v=valor):
                        time.sleep(1.0)
                        self.cola_comandos.put((d, i, v))
                    threading.Thread(target=_reencolar2, daemon=True).start()
            time.sleep(0.05)
            self.cola_comandos.task_done()

    def _cerrar(self):
        self.ejecutando_seq = False
        self._lock_heartbeat_activo = False
        if MODBUS_DISPONIBLE and self.modbus_client:
            self._lock_liberar()   # liberar lock si lo tenemos al salir
            self.modbus_client.close()
        self.destroy()


if __name__ == "__main__":
    M502HMI().mainloop()
