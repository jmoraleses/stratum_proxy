# Stratum Proxy

Stratum Pool is a cryptocurrency mining proxy that connects miners to a mining pool, managing difficulty and miner authorizations.


## StratumPool
`StratumPool` is a mining proxy designed for integration with mining pools. It connects to Bitcoin Core via RPC and aggregates multiple miner connections, managing job distribution, and collecting shares from miners. It optimizes mining pool operations for better organization and efficiency.

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
[RPC]
host = 127.0.0.1
port = 8332
user = user
password = password

[STRATUM]
port = 3330

[POOL]
host = stratum.solomining.io
port = 7777

[FILES]
merkle_file = merkle_counts_5.csv

[ADDRESS]
address = 18KT4CzQnz5CSC5u57apcKRwb7bBSwntmz

[DATABASE]
db_file = merkle_roots.db

[DIFFICULTY]
min = 23000
max = 128000
```

## Usage

To start the proxy, run:
```sh
python3 StratumProxyPool.py
```


18KT4CzQnz5CSC5u57apcKRwb7bBSwntmz
