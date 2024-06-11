import socket
import configparser
import select
import threading
import json
import hashlib
import random
import struct
import time
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor
from BlockTemplate import BlockTemplate
from DatabaseHandler import DatabaseHandler


class StratumProxy:
    def __init__(self, pool_address, miner_port, config):
        self.pool_address = pool_address
        self.miner_port = miner_port
        self.pool_host = self.pool_address.split('//')[1].split(':')[0]
        self.pool_port = int(self.pool_address.split(':')[2])
        self.pool_sock = None
        self.db_handler = DatabaseHandler(config.get('DATABASE', 'db_file')).create_table()
        self.stratum_processing = StratumProcessing(config, self)
        self.generated_merkle_roots = []
        self.valid_merkle_count = 0
        self.merkle_counts_file = config.get('FILES', 'merkle_file')
        self.merkle_counts = self.load_merkle_counts(self.merkle_counts_file)
        self.executor = ThreadPoolExecutor(max_workers=5)

    def start(self):
        if self.connect_to_pool():
            threading.Thread(target=self.block_template_updater).start()
            threading.Thread(target=self.merkle_root_counter).start()
            self.wait_for_miners()

    def block_template_updater(self):
        while True:
            self.stratum_processing.block_template_fetcher.fetch_template()
            time.sleep(2)

    def merkle_root_counter(self):
        while True:
            self.count_valid_merkle_roots()
            time.sleep(600)

    def load_merkle_counts(self, file_path):
        try:
            df = pd.read_csv(file_path)
            return df
        except Exception as e:
            print(f"Error loading merkle counts: {e}")
            return pd.DataFrame(columns=['merkle_root'])

    def check_merkle_root(self, merkle_root):
        if merkle_root:
            return self.merkle_counts['merkle_root'].apply(lambda root: merkle_root.startswith(root)).any()
        return False

    def count_valid_merkle_roots(self):
        valid_count = sum(1 for root in self.generated_merkle_roots if self.check_merkle_root(root))
        print(
            f"Cantidad de merkle roots válidos en el último minuto: {valid_count} de {len(self.generated_merkle_roots)}")
        self.valid_merkle_count = 0
        self.generated_merkle_roots = []

    def connect_to_pool(self):
        try:
            print(f"Resolviendo el dominio {self.pool_host}...")
            print(f"Conectando a la pool en {self.pool_host}:{self.pool_port}...")
            self.pool_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.pool_sock.settimeout(30)
            self.pool_sock.connect((self.pool_host, self.pool_port))
            print("Conexión establecida con la pool.")
            return True
        except Exception as e:
            print(f"Error al conectar a la pool: {e}")
            return False

    def wait_for_miners(self):
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.bind(('0.0.0.0', self.miner_port))
            server_sock.listen(5)
            print(f"Esperando conexiones de mineros en el puerto {self.miner_port}...")
            while True:
                miner_sock, addr = server_sock.accept()
                print(f"Conexión establecida con el minero: {addr}")
                threading.Thread(target=self.handle_miner_connection, args=(miner_sock,)).start()
        except Exception as e:
            print(f"Error esperando a los mineros: {e}")

    def handle_miner_connection(self, miner_sock):
        try:
            pool_sock = self.create_pool_socket()
            if not pool_sock:
                miner_sock.close()
                return

            pool_buffer = ""
            miner_buffer = ""

            while True:
                ready_socks, _, _ = select.select([miner_sock, pool_sock], [], [])
                for sock in ready_socks:
                    if sock == miner_sock:
                        miner_buffer = self.proxy_message(miner_sock, pool_sock, miner_buffer, "miner")
                        if miner_buffer is None:
                            return
                    elif sock == pool_sock:
                        pool_buffer = self.proxy_message(pool_sock, miner_sock, pool_buffer, "pool")
                        if pool_buffer is None:
                            return
        except Exception as e:
            print(f"Error en handle_miner_connection: {e}")
        finally:
            miner_sock.close()
            self.pool_sock.close()

    def create_pool_socket(self):
        try:
            pool_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            pool_sock.settimeout(30)
            pool_sock.connect((self.pool_host, self.pool_port))
            return pool_sock
        except Exception as e:
            print(f"Error al crear socket de conexión a la pool: {e}")
            return None

    def proxy_message(self, source_sock, dest_sock, buffer, source):
        try:
            data = source_sock.recv(4096).decode('utf-8')
            if not data:
                return None
            buffer += data
            while '}' in buffer:
                end_idx = buffer.index('}') + 1
                message = buffer[:end_idx]
                buffer = buffer[end_idx:]
                try:
                    message_json = json.loads(message)
                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON: {e}")
                    continue

                method = message_json.get("method")
                if method == "mining.submit":
                    self.executor.submit(self.stratum_processing.process_submit, message_json.get("params", []),
                                         dest_sock)
                elif method == "mining.notify":
                    self.executor.submit(self.stratum_processing.verify_job, message_json.get("params", []), dest_sock)

                dest_sock.sendall(message.encode('utf-8') + b'\n')
                print(f"Raw message: {message}")

            return buffer
        except Exception as e:
            print(f"Error en proxy_message: {e}")
            return None


class StratumProcessing:
    def __init__(self, config, proxy):
        self.config = config
        self.proxy = proxy
        self.db_file = config.get('DATABASE', 'db_file')
        self.db_handler = DatabaseHandler(self.db_file)
        self.block_template_fetcher = BlockTemplate(config)
        self.block_template = None
        self.coinbase_message = None
        self.transactions = None
        self.address = config.get('ADDRESS', 'address')
        self.version = None
        self.height = None
        self.prevhash = None
        self.nbits = None
        self.fee = None
        self.target = None
        self.transactions_raw = None
        self.min_difficulty = int(config.get('DIFFICULTY', 'min'))  # Inicializar la dificultad sugerida
        self.max_difficulty = int(config.get('DIFFICULTY', 'max'))  # Inicializar la dificultad sugerida
        self.jobs = {}
        self.first_job = False

    def update_block_template(self):
        template = self.block_template_fetcher.get_template()
        self.block_template = template
        self.height = self.block_template['height']

    def update_block_template_from_rpc(self):
        rpc_user = self.config.get('RPC', 'user')
        rpc_password = self.config.get('RPC', 'password')
        rpc_host = self.config.get('RPC', 'host')
        rpc_port = self.config.get('RPC', 'port')
        rpc_url = f"http://{rpc_user}:{rpc_password}@{rpc_host}:{rpc_port}"
        headers = {'content-type': 'application/json'}
        payload = json.dumps({"method": "getblocktemplate", "params": [{}], "jsonrpc": "2.0", "id": 0})

        try:
            response = requests.post(rpc_url, headers=headers, data=payload).json()
            self.block_template = response['result']
            print(f"Nuevo bloque recibido por RPC: {self.block_template}")
        except Exception as e:
            print(f"Error al obtener el template del bloque por RPC: {e}")

    def create_job_from_blocktemplate(self):
        try:
            version = self.version_to_hex(self.block_template['version'])
            prev_block = self.block_template['previousblockhash']
            extranonce_placeholder = "00000000"
            miner_message = self.miner_message()
            merkle_root = None

            while not self.proxy.check_merkle_root(merkle_root):
                self.transactions = self.select_random_transactions()

                self.fee = self.calculate_coinbase_value()
                coinbase1, coinbase2 = self.tx_make_coinbase_stratum(miner_message, extranonce_placeholder)
                coinbase_tx = coinbase1 + coinbase2
                coinbase_tx_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_tx)).digest()).digest().hex()

                merkle_hashes = [coinbase_tx_hash]
                for tx in self.transactions:
                    if 'hash' in tx and 'data' in tx:
                        merkle_hashes.append(tx['hash'])
                    else:
                        raise ValueError("Cada transacción debe contener 'hash' y 'data'")

                merkle_branch = self.compute_merkle_branch(merkle_hashes)
                merkle_root = self.compute_merkle_root(merkle_hashes)
                self.proxy.generated_merkle_roots.append(merkle_root)  # Guardar el merkle root generado

                if self.proxy.check_merkle_root(merkle_root):

                    self.target = self.bits_to_target(self.block_template["bits"])
                    timestamp = int(time.time())
                    nbits = self.block_template['bits']
                    job_id = random.randint(0, 0xFFFFFFFF)
                    clean_jobs = self.first_job or (self.height != self.block_template['height'])

                    job = {
                        'job_id': job_id,
                        'version': version,
                        'prevhash': prev_block,
                        'coinbase1': coinbase1,
                        'coinbase2': coinbase2,
                        'merkle_branch': merkle_branch,
                        'merkle_root': merkle_root,
                        'nbits': nbits,
                        'ntime': timestamp,
                        'clean_jobs': clean_jobs,
                    }
                    return job

        except Exception as e:
            print(f"Error creating job from blocktemplate: {e}")
            return None

    def verify_job(self, job_params, miner_sock):
        job_id = job_params[0]
        self.update_block_template()
        job = self.create_job_from_blocktemplate()
        if job:
            local_header, _version, _prevhash, _merkle_root, _nbits, _ntime, _nonce, _coinbase1, _coinbase2 = self.create_block_header(
                job['version'], job['prevhash'], job['merkle_root'], job['ntime'], job['nbits'], job['coinbase1'],
                job['coinbase2'])
            if self.proxy.check_merkle_root(_merkle_root):
                self.jobs[job_id] = {
                    "job_id": job_id,
                    "prevhash": _prevhash,
                    "coinbase1": _coinbase1,
                    "coinbase2": _coinbase2,
                    "merkle_root": _merkle_root,
                    "merkle_branch": job['merkle_branch'],
                    "version": _version,
                    "nbits": _nbits,
                    "local_header": local_header
                }
                local_notify = {
                    "id": None,
                    "method": "mining.notify",
                    "params": [job_id, _prevhash, _coinbase1, _coinbase2, job['merkle_branch'], _version, _nbits,
                               job['ntime'], job['clean_jobs']]
                }
                miner_sock.sendall(json.dumps(local_notify).encode('utf-8') + b'\n')
                print(f"Trabajo enviado: {job_id}")

    def process_submit(self, submit_params, miner_sock):
        worker_name = submit_params[0]
        job_id = submit_params[1]
        extranonce2 = submit_params[2]
        ntime = submit_params[3]
        nonce = submit_params[4]

        print(f"Procesando submit del minero {worker_name} para el job {job_id}...")

        job = self.jobs.get(job_id)
        if not job:
            response = {"id": None, "result": None, "error": [23, "Job ID not found", ""]}
            miner_sock.sendall(json.dumps(response).encode('utf-8'))
            return

        block_header = self.create_block_header_submit(job['version'], job['prevhash'], job['merkle_branch'],
                                                       self.to_little_endian(ntime),
                                                       self.to_little_endian(job['nbits']),
                                                       job['coinbase1'], job['coinbase2'], self.to_little_endian(nonce),
                                                       extranonce2)

        print(f"header: {block_header}")
        print(f"local_header: {job['local_header']}")

        block_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(block_header)).digest()).digest().hex()
        target = int(self.bits_to_target(job['nbits']), 16)
        print(f"block hash: {block_hash}")

        if int(block_hash, 16) < target:
            rpc_user = self.config.get('RPC', 'user')
            rpc_password = self.config.get('RPC', 'password')
            rpc_host = self.config.get('RPC', 'host')
            rpc_port = self.config.get('RPC', 'port')
            rpc_url = f"http://{rpc_user}:{rpc_password}@{rpc_host}:{rpc_port}"
            block_data = {"method": "submitblock", "params": [block_header], "id": 1, "jsonrpc": "2.0"}

            response = requests.post(rpc_url, json=block_data).json()
            if 'error' in response and response['error']:
                error = response['error']
                response = {"id": None, "result": None, "error": error}
                miner_sock.sendall(json.dumps(response).encode('utf-8'))
            else:
                response = {"id": None, "result": True, "error": None}
                miner_sock.sendall(json.dumps(response).encode('utf-8'))
        else:
            response = {"id": None, "result": None, "error": [23, "Difficulty too low", ""]}
            miner_sock.sendall(json.dumps(response).encode('utf-8'))

    def set_difficulty(self, params, miner_sock):
        try:
            difficulty = int(params[0])
            difficulty = max(self.min_difficulty, min(self.max_difficulty, difficulty))
            print(f"Estableciendo dificultad a {difficulty}")
            difficulty_message = {"id": None, "method": "mining.set_difficulty", "params": [difficulty]}
            miner_sock.sendall(json.dumps(difficulty_message).encode('utf-8'))
        except Exception as e:
            print(f"Error estableciendo la dificultad: {e}")

    def authorize_miner(self, params, miner_sock):
        response = {"id": None, "result": True, "error": None}
        miner_sock.sendall(json.dumps(response).encode('utf-8'))
        print(f"Minero {params[0]} autorizado")

    def create_block_header(self, version, prev_block, merkle_root, ntime, nbits, coinbase1, coinbase2):
        version = struct.pack("<L", int(version, 16)).hex()
        prev_block = swap_endianness_8chars_final(prev_block)
        # merkle_root = self.compute_merkle_root(merkle_branch + [coinbase1 + coinbase2])
        merkle_root = self.to_little_endian(merkle_root)
        nbits = struct.pack("<L", int(nbits, 16)).hex()
        ntime = struct.pack("<L", ntime).hex()
        nonce = struct.pack("<L", random.randint(0, 0xFFFFFFFF)).hex()
        header = version + prev_block + merkle_root + ntime + nbits + nonce
        version = swap_endianness_8chars_final(version)
        prev_block = self.to_little_endian(prev_block)
        nbits = swap_endianness_8chars_final(nbits)
        ntime = swap_endianness_8chars_final(ntime)
        return header, version, prev_block, merkle_root, nbits, ntime, nonce, coinbase1, coinbase2

    def create_block_header_submit(self, version, prevhash, merkle_branch, ntime, nbits, coinbase1, coinbase2, nonce,
                                   extranonce2):
        version = self.to_little_endian(version)
        prevhash = self.to_little_endian(prevhash)
        coinbase_transaction = coinbase1 + extranonce2 + coinbase2
        coinbase_tx_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_transaction)).digest()).digest().hex()
        merkle_hashes = [coinbase_tx_hash] + merkle_branch
        merkle_root = self.compute_merkle_root(merkle_hashes)
        merkle_root = self.to_little_endian(merkle_root)
        header = version + prevhash + merkle_root + ntime + nbits + nonce
        return header

    def compute_merkle_branch(self, merkle):
        branches = []
        while len(merkle) > 1:
            if len(merkle) % 2 != 0:
                merkle.append(merkle[-1])
            new_merkle = []
            for i in range(0, len(merkle), 2):
                branches.append(merkle[i])
                new_merkle.append(
                    hashlib.sha256(hashlib.sha256(bytes.fromhex(merkle[i] + merkle[i + 1])).digest()).hexdigest())
            merkle = new_merkle
        return branches

    def compute_merkle_root(self, merkle):
        if len(merkle) == 0:
            return None
        elif len(merkle) == 1:
            return merkle[0]
        else:
            while len(merkle) > 1:
                if len(merkle) % 2 != 0:
                    merkle.append(merkle[-1])
                new_merkle = []
                for i in range(0, len(merkle), 2):
                    new_merkle.append(
                        hashlib.sha256(hashlib.sha256(bytes.fromhex(merkle[i] + merkle[i + 1])).digest()).hexdigest())
                merkle = new_merkle
            return merkle[0]

    def create_coinbase_script(self, block_height, miner_message, extranonce_placeholder):
        block_height_script = self.tx_encode_coinbase_height(block_height)
        total_allowed_size = 100
        block_height_size = len(block_height_script) // 2
        extranonce_size = len(extranonce_placeholder) // 2
        remaining_size = total_allowed_size - block_height_size - extranonce_size
        miner_message_hex = miner_message.encode('utf-8').hex()
        if len(miner_message_hex) // 2 > remaining_size:
            miner_message_hex = miner_message_hex[:remaining_size * 2]
        coinbase_script = block_height_script + miner_message_hex
        return coinbase_script

    def tx_make_coinbase_stratum(self, miner_message, extranonce_placeholder="00000000", op_return_data=""):
        pubkey_script = "76a914" + self.bitcoinaddress2hash160(self.address) + "88ac"
        block_height = self.block_template['height']
        coinbase_script = self.create_coinbase_script(block_height, miner_message, extranonce_placeholder)
        max_coinbase_script_size = 100 * 2
        actual_script_size = len(coinbase_script) + len(extranonce_placeholder)
        if actual_script_size > max_coinbase_script_size:
            coinbase_script = coinbase_script[:max_coinbase_script_size - len(extranonce_placeholder)]
        coinbase1 = "02000000" + "01" + "0" * 64 + "ffffffff" + self.int2varinthex(
            (len(coinbase_script) + len(extranonce_placeholder)) // 2) + coinbase_script + extranonce_placeholder
        num_outputs = "01"
        op_return_script = ""
        op_return_length = ""
        if op_return_data:
            op_return_script = self.create_op_return_script(op_return_data)
            op_return_length = self.int2varinthex(len(op_return_script) // 2)
            num_outputs = "02"
        coinbase2 = "ffffffff" + num_outputs + self.int2lehex(self.block_template['coinbasevalue'],
                                                              8) + self.int2varinthex(
            len(pubkey_script) // 2) + pubkey_script + "00000000" + op_return_length + op_return_script
        return coinbase1, coinbase2

    def calculate_coinbase_value(self):
        base_reward = self.block_template['coinbasevalue']
        total_fees = sum(tx['fee'] for tx in self.transactions)
        return base_reward + total_fees

    def create_op_return_script(self, data):
        if len(data) > 80:
            raise ValueError("Los datos para OP_RETURN no deben exceder los 80 bytes.")
        return "6a" + data.encode('utf-8').hex()

    def select_random_transactions(self):
        max_size_limit = random.randint(200, 1024) * 1024
        transactions = self.block_template['transactions'].copy()
        random.shuffle(transactions)
        selected_transactions = []
        total_size = 0
        for transaction in transactions:
            transaction_size = transaction.get('weight', 0)
            if total_size + transaction_size <= max_size_limit:
                selected_transactions.append(transaction)
                total_size += transaction_size
            else:
                break
        return selected_transactions

    def to_little_endian(self, hex_string):
        return ''.join([hex_string[i:i + 2] for i in range(0, len(hex_string), 2)][::-1])

    def version_to_hex(self, version):
        return '{:08x}'.format(version)

    def bits_to_target(self, nbits):
        nbits = int(nbits, 16)
        exponent = nbits >> 24
        mantisa = nbits & 0xFFFFFF
        target = mantisa * (2 ** (8 * (exponent - 3)))
        return '{:064x}'.format(target)

    def miner_message(self):
        caracteres = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890!@#$%^&*()_+-=[]{}|;:,.<>?`~"
        return ''.join(random.choice(caracteres) for _ in range(100))

    def int2lehex(self, value, width):
        return value.to_bytes(width, byteorder='little').hex()

    def int2varinthex(self, value):
        if value < 0xfd:
            return '{:02x}'.format(value)
        elif value <= 0xffff:
            return 'fd' + '{:04x}'.format(value)
        elif value <= 0xffffffff:
            return 'fe' + '{:08x}'.format(value)
        return 'ff' + '{:016x}'.format(value)

    def tx_encode_coinbase_height(self, height):
        width = (height.bit_length() + 7) // 8
        fmt = '{:0%dx}' % (width * 2)
        return struct.pack('<B', width).hex() + fmt.format(height)

    def bitcoinaddress2hash160(self, addr):
        table = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        num = 0
        for char in addr:
            num = num * 58 + table.index(char)
        combined = num.to_bytes(25, byteorder='big')
        if hashlib.sha256(hashlib.sha256(combined[:-4]).digest()).digest()[:4] != combined[-4:]:
            raise ValueError("Checksum does not match")
        return combined[1:-4].hex()


def swap_endianness_8chars_final(hex_string):
    return ''.join(
        [hex_string[i + 6:i + 8] + hex_string[i + 4:i + 6] + hex_string[i + 2:i + 4] + hex_string[i:i + 2] for i in
         range(0, len(hex_string), 8)])


def main():
    config = configparser.ConfigParser()
    config.read('config.ini')
    pool_address = f"stratum+tcp://{config['POOL']['host']}:{config['POOL']['port']}"
    miner_port = int(config['STRATUM']['port'])
    proxy = StratumProxy(pool_address, miner_port, config)
    proxy.start()


if __name__ == '__main__':
    main()
