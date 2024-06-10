# Stratum Proxy

Stratum Proxy is a cryptocurrency mining proxy that connects miners to a mining pool, managing difficulty and miner authorizations.

## StratumProxyZMQ
`StratumProxyZMQ` is a mining proxy that uses the ZeroMQ protocol to subscribe to and receive messages. It manages communication between miners and the mining server, facilitating efficient task distribution and result collection through a ZeroMQ-based message channel. This design enhances scalability and reduces latency in data transmission, making it ideal for high-demand mining environments.

## StratumProxyPool
`StratumProxyPool` is a mining proxy variant designed for integration with mining pools. Unlike `stratumproxyzmq`, which focuses on ZeroMQ message handling, `StratumProxyPool` aggregates multiple miner connections, manages job distribution, and collects shares from miners. It optimizes mining pool operations for better organization and efficiency.

## ProxyCPU
`ProxyCPU` is a mining proxy operating under CPU control. It manages connections and distributes mining tasks using CPU processing power. While less efficient than GPU or ASIC proxies, `ProxyCPU` is useful for testing, development, and environments where CPU usage is preferable or more accessible.


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
