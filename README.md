# Stratum Proxy

Stratum Proxy is a cryptocurrency mining proxy that connects miners to a mining pool, managing difficulty and miner authorizations.

## Features

- Connects to a mining pool using the Stratum protocol.
- Manages mining difficulty for connected miners.
- Authorizes miners.
- Processes and verifies mining jobs.
- Handles transactions and creates coinbases.

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
python3 stratum_proxy.py
```


18KT4CzQnz5CSC5u57apcKRwb7bBSwntmz
