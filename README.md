# CYBERSPAM-502 

CYBERSPAM 502 es un proyecto que simula un entorno OT/IoT: un brazo robótico de 6 grados controlado por un ESP32 que actúa como un PLC utilizando el protocólo Modbus TCP.
El fin de este proyecto es demostrar las vulnerabilidades en redes OT: falta de control de acceso a Modbus TCP, influyendo en el control libre del brazo robótico por parte del atacante.

## Redes OT

La denominada OT es la Tecnología de las Operaciones, que está dedicada a detectar o cambiar los procesos físicos a través del monitoreo y administración de dispositivos, como tuberías, válvulas o disyuntores.

Conceptos importantes:

- SCADA (Sistemas de Control y Adquisición de Datos): Representa una estructura de sistemas informáticos diseñados para supervisar y controlar infraestructuras y procesos industriales.
- Unidades Terminales Remotas (RTU): Recopilan datos de sensores y ejecutan comandos automáticos o desde el centro de control.
- Controladores Lógicos Programables (PLC): Utilizados en lugar o junto a las RTU, los PLC son dispositivos robustos que supervisan y controlan maquinaria o procesos industriales.
- Interfaz Hombre-Máquina (HMI): Es el panel de control donde los operadores interactúan con el sistema, visualizando datos, emitiendo comandos y monitorizando el estado del proceso.

## Modbus

Modbus es un protocolo de comunicación de capa de aplicación (nivel 7 del modelo OSI) diseñado para el intercambio de datos entre dispositivos industriales. Define cómo un dispositivo solicita información y cómo otro responde, utilizando una arquitectura cliente/servidor (tradicionalmente llamada maestro/esclavo).

Este cuenta con diferentes aplicaciones y tipos:

- Modbus RTU
- Modbus TCP
- Modbus ASCII
- Modbus Plus

Roles: Maestro (client) y esclavo (server). En Modbus el maestro siempre inicia la comunicacion.

1. El maestro envia un mensaje con una dirección específica y un código de función.
2. El esclavo revisa el encabezado: si la dirección coincide con la suya, procesa la solicitud.
3. El esclavo ejecuta la acción pedida.
4. El esclavo responde con un mensaje que contiene los datos solicitados o una confirmación de escritura.
5. Si el esclavo no responde dentro del tiempo definido, el maestro genera un error de “no response” o timeout.
