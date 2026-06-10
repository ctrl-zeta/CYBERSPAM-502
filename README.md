# CYBERSPAM-502 🏭

CYBERSPAM 502 es un proyecto que simula un entorno OT/IoT: un brazo robótico de 6 grados controlado por un ESP32 que actúa como un PLC utilizando el protocólo Modbus TCP.
El fin de este proyecto es demostrar las vulnerabilidades en redes OT: falta de control de acceso a Modbus TCP, influyendo en el control libre del brazo robótico por parte del atacante.

## Redes OT

La denominada OT es la Tecnología de las Operaciones, que está dedicada a detectar o cambiar los procesos físicos a través del monitoreo y administración de dispositivos, como tuberías, válvulas o disyuntores.

Conceptos importantes:

- SCADA (Sistemas de Control y Adquisición de Datos): Representa una estructura de sistemas informáticos diseñados para supervisar y controlar infraestructuras y procesos industriales.
- Unidades Terminales Remotas (RTU): Recopilan datos de sensores y ejecutan comandos automáticos o desde el centro de control.
- Controladores Lógicos Programables (PLC): Utilizados en lugar o junto a las RTU, los PLC son dispositivos robustos que supervisan y controlan maquinaria o procesos industriales.
- Interfaz Hombre-Máquina (HMI): Es el panel de control donde los operadores interactúan con el sistema, visualizando datos, emitiendo comandos y monitorizando el estado del proceso.

