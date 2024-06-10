# Stratum Proxy

Stratum Proxy is a cryptocurrency mining proxy that connects miners to a mining pool, managing difficulty and miner authorizations.

## StratumProxyZMQ
`StratumProxyZMQ` es una implementación de un proxy de minería que utiliza el protocolo ZeroMQ para suscribirse y recibir mensajes. Este componente se encarga de gestionar la comunicación entre los mineros y el servidor de minería, permitiendo la distribución eficiente de tareas y la recolección de resultados de minería a través de un canal de mensajes basado en ZeroMQ. Gracias a su diseño, `StratumProxyZMQ` facilita la escalabilidad y mejora la latencia en la transmisión de datos, proporcionando una solución robusta para entornos de minería de alta demanda.

`StratumProxyZMQ` is an implementation of a mining proxy that uses the ZeroMQ protocol to subscribe to and receive messages. This component manages the communication between miners and the mining server, allowing efficient distribution of tasks and collection of mining results through a message channel based on ZeroMQ. Thanks to its design, `StratumProxyZMQ` facilitates scalability and improves latency in data transmission, providing a robust solution for high-demand mining environments.

## StratumProxyPool
`StratumProxyPool` es una variante de proxy de minería diseñada para integrarse con pools de minería. A diferencia de `StratumProxyPool`, que se enfoca en la suscripción y manejo de mensajes mediante ZeroMQ, `StratumProxyPool` se centra en la agregación de múltiples conexiones de mineros, gestionando la distribución de trabajos y la recopilación de shares (acciones de trabajo) enviados por los mineros. Esta herramienta es ideal para aquellos que desean optimizar la operación de un pool de minería, permitiendo una mejor organización y eficiencia en el manejo de las conexiones de los mineros.

`StratumProxyPool` is a mining proxy variant designed to integrate with mining pools. Unlike `StratumProxyPool`, which focuses on subscribing to and managing messages via ZeroMQ, `StratumProxyPool` centers on aggregating multiple miner connections, managing job distribution, and collecting shares submitted by miners. This tool is ideal for those who want to optimize the operation of a mining pool, allowing better organization and efficiency in handling miner connections.

## ProxyCPU
`ProxyCPU` se refiere a un minero de criptomonedas que utiliza la CPU del ordenador para realizar las operaciones de minería. Este tipo de minero es generalmente más accesible, ya que cualquier computadora con un procesador puede participar en la minería. Sin embargo, la minería con CPU suele ser menos eficiente y rentable en comparación con la minería mediante GPU (tarjetas gráficas) o ASICs (circuitos integrados específicos para aplicaciones), debido a la menor capacidad de procesamiento de las CPUs. `ProxyCPU` es útil para fines educativos, pruebas o para participar en redes de criptomonedas que todavía sean viables para la minería con CPU.

`ProxyCPU` refers to a cryptocurrency miner that uses the computer's CPU to perform mining operations. This type of miner is generally more accessible since any computer with a processor can participate in mining. However, CPU mining is usually less efficient and profitable compared to GPU (graphics cards) or ASIC (application-specific integrated circuits) mining, due to the lower processing power of CPUs. `ProxyCPU` is useful for educational purposes, testing, or participating in cryptocurrency networks that are still viable for CPU mining.


## Features

- Connects to a mining pool using the Stratum protocol.
- Manages mining difficulty for connected miners.
- Authorizes miners.
- Processes and verifies mining jobs.
- Handles transactions and creates coinbases.
- Sends the block header, if found, via bitcoin core.

## Requirements

- Python 3.11 or higher
- Python packages:
  - socket
  - configparser
  - select
  - threading
  - json
  - hashlib
  - random
  - struct
  - time
  - pandas
  - requests

## Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/jmoraleses/miner_stratum_proxy.git
    cd stratum-proxy
    ```

2. Install the dependencies:
    ```sh
    pip install -r requirements.txt
    ```

3. Configure the `config.ini` file:
    ```ini
    [POOL]
    host = pool_host
    port = pool_port

    [STRATUM]
    port = stratum_port

    [FILES]
    merkle_file = merkle_file.csv

    [ADDRESS]
    address = your_bitcoin_address

    [DIFFICULTY]
    min = 1
    max = 1000

    [RPC]
    user = rpc_user
    password = rpc_password
    ```

## Usage

To start the proxy, run:
```sh
python3 StratumProxyPool.py
```


18KT4CzQnz5CSC5u57apcKRwb7bBSwntmz
