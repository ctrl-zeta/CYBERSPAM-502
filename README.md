# CYBERSPAM-502 🏭

CYBERSPAM 502 es un proyecto que simula un entorno OT/IoT: un brazo robótico de 6 grados controlado por un ESP32 que actúa como un PLC utilizando el protocólo Modbus TCP.
El fin de este proyecto es demostrar las vulnerabilidades en redes OT: falta de control de acceso a Modbus TCP, influyendo en el control libre del brazo robótico por parte del atacante.

## Metasploit para Modbus

### modbusdetect:

Detecta si el servicio Modbus esta corriendo.

[image.png]

### modbus_findunitd:

Prueba diferentes Unit IDs para descubrir con cual el esclavo responde.

### modbus_banner_grabbing:

Envia el Function Code 43 (Read Device Identification) para obtener informacion detallada del dispositivo.
