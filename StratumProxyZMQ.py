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
import zmq
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
        self.stratum_processing = StratumProcessing(config)
        self.zmq_context = zmq.Context()

    def start(self):
        if self.connect_to_pool():
            threading.Thread(target=self.block_template_updater).start()
            self.wait_for_miners()

    def block_template_updater(self):
        socket_block = self.zmq_context.socket(zmq.SUB)
        socket_block.connect("tcp://127.0.0.1:3000")
        socket_block.setsockopt(zmq.SUBSCRIBE, b"")

        socket_tx = self.zmq_context.socket(zmq.SUB)
        socket_tx.connect("tcp://127.0.0.1:3001")
        socket_tx.setsockopt(zmq.SUBSCRIBE, b"")

        poller = zmq.Poller()
        poller.register(socket_block, zmq.POLLIN)
        poller.register(socket_tx, zmq.POLLIN)

        while True:
            socks = dict(poller.poll(timeout=1000))
            if socket_block in socks:
                message = socket_block.recv()
                self.stratum_processing.update_block_template_from_zmq(message)
            if socket_tx in socks:
                message = socket_tx.recv()
                self.stratum_processing.update_transactions_from_zmq(message)
            time.sleep(2)

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
                    continue  # Saltar al siguiente mensaje si hay un error de decodificación

                method = message_json.get("method")
                if method == "mining.submit":
                    self.stratum_processing.process_submit(message_json.get("params", []), dest_sock)
                elif method == "mining.notify":
                    self.stratum_processing.verify_job(message_json.get("params", []), dest_sock)

                dest_sock.sendall(message.encode('utf-8') + b'\n')
                print(f"Raw message: {message}")

            return buffer
        except Exception as e:
            print(f"Error en proxy_message: {e}")
            return None


class StratumProcessing:
    def __init__(self, config):
        self.config = config
        self.db_file = config.get('DATABASE', 'db_file')
        self.db_handler = DatabaseHandler(self.db_file)
        self.block_template_fetcher = BlockTemplate(config)
        self.block_template = None
        self.merkle_counts_file = config.get('FILES', 'merkle_file')
        self.merkle_counts = self.load_merkle_counts(self.merkle_counts_file)
        self.coinbase_message = None
        self.transactions = None
        self.address = config.get('ADDRESS', 'address')
        self.version = None
        self.height = None
        self.prevhash = None
        self.nbits = None
        self.fee = None
        self.transactions_raw = None
        self.min_difficulty = int(config.get('DIFFICULTY', 'min'))  # Inicializar la dificultad sugerida
        self.max_difficulty = int(config.get('DIFFICULTY', 'max'))  # Inicializar la dificultad sugerida
        self.subscriptions = {}
        self.current_client_id = None
        self.current_job_id = None
        self.ready = False
        self.target = None
        self.jobs = {}  # Diccionario para almacenar los trabajos por job_id
        self.first_job = False  # Variable para controlar el primer trabajo

    def load_merkle_counts(self, file_path):
        try:
            df = pd.read_csv(file_path)
            return df
        except Exception as e:
            print(f"Error loading merkle counts: {e}")
            return pd.DataFrame(columns=['merkle_root'])

    def check_merkle_root(self, merkle_root):
        return self.merkle_counts['merkle_root'].apply(lambda root: merkle_root.startswith(root)).any()

    def update_block_template(self):
        template = self.block_template_fetcher.get_template()
        # if self.height_now != template['height']:
        self.block_template = template
        self.nbits = self.block_template['bits']
        self.version = self.block_template['version']
        self.prevhash = self.block_template['previousblockhash']
        self.fee = self.calculate_coinbase_value()
        self.transactions_raw = self.block_template['transactions']
        self.height = self.block_template['height']
        # self.height_now = self.height

    def update_block_template_from_zmq(self, message):
        # Procesar el mensaje ZMQ para actualizar el template del bloque
        block_data = message.hex()
        print(f"Nuevo bloque recibido por ZMQ: {block_data}")
        self.update_block_template()

    def update_transactions_from_zmq(self, message):
        # Procesar el mensaje ZMQ para actualizar las transacciones
        tx_data = message.hex()
        print(f"Nueva transacción recibida por ZMQ: {tx_data}")

    def create_job_from_blocktemplate(self):
        try:
            version = self.version_to_hex(self.block_template['version'])
            prev_block = self.block_template['previousblockhash']
            extranonce_placeholder = "00000000"
            miner_message = self.miner_message()

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
            self.target = self.bits_to_target(self.nbits)
            timestamp = int(time.time())
            nbits = self.block_template['bits']
            job_id = random.randint(0, 0xFFFFFFFF)
            clean_jobs = self.first_job or (self.height != self.block_template[
                'height'])  # Establecer clean_jobs como True la primera vez o si el height es diferente

            job = {
                'job_id': job_id,
                'version': version,
                'prevhash': prev_block,
                'coinbase1': coinbase1,
                'coinbase2': coinbase2,
                'merkle_branch': merkle_branch,
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
        prevhash = job_params[1]
        coinbase1 = job_params[2]
        coinbase2 = job_params[3]
        merkle_branch = job_params[4]
        version = job_params[5]
        nbits = job_params[6]
        ntime = job_params[7]
        clean_jobs = job_params[8]

        self.update_block_template()
        job = self.create_job_from_blocktemplate()
        if job is not None:
            local_header, _version, _prevhash, _merkle_root, _nbits, _ntime, _nonce, _coinbase1, _coinbase2 = self.create_block_header(
                job['version'],
                job['prevhash'],
                job['merkle_branch'],
                job['ntime'],
                job['nbits'],
                job['coinbase1'],
                job['coinbase2']
            )
            _merkle_branch = job['merkle_branch']
            print(f"Verificando job {job_id}...")
            if _merkle_root and self.check_merkle_root(_merkle_root):
                self.jobs[job_id] = {
                    "job_id": job_id,
                    "prevhash": _prevhash,
                    "coinbase1": _coinbase1,
                    "coinbase2": _coinbase2,
                    "merkle_root": _merkle_root,
                    "merkle_branch": _merkle_branch,
                    "version": _version,
                    "nbits": _nbits,
                    "local_header": local_header
                }

                local_notify = {
                    "id": None,
                    "method": "mining.notify",
                    "params": [job_id, _prevhash, _coinbase1, _coinbase2, _merkle_branch, _version, _nbits, _ntime,
                               clean_jobs]
                }
                miner_sock.sendall(json.dumps(local_notify).encode('utf-8') + b'\n')
                print("Trabajo enviado: {}", job_id)

    def verify_local_job(self, miner_sock):
        job_id = None
        clean_jobs = False
        self.update_block_template()
        job = self.create_job_from_blocktemplate()

        local_header, _version, _prevhash, _merkle_root, _nbits, _ntime, _nonce, _coinbase1, _coinbase2 = self.create_block_header(
            job['version'],
            job['prevhash'],
            job['merkle_branch'],
            job['ntime'],
            job['nbits'],
            job['coinbase1'],
            job['coinbase2']
        )

        _merkle_branch = job['merkle_branch']
        print(f"Header generado localmente: {local_header}")

        local_notify = {
            "id": None,
            "method": "mining.notify",
            "params": [job_id, _prevhash, _coinbase1, _coinbase2, _merkle_branch, _version, _nbits, _ntime, clean_jobs]
        }

        miner_sock.sendall(json.dumps(local_notify).encode('utf-8') + b'\n')
        print(f'JSON Formatted Local: {json.dumps(local_notify, indent=4)}')

    def process_submit(self, submit_params, miner_sock):
        worker_name = submit_params[0]
        job_id = submit_params[1]
        extranonce2 = submit_params[2]
        ntime = submit_params[3]
        nonce = submit_params[4]

        print(f"Procesando submit del minero {worker_name} para el job {job_id}...")

        job = self.jobs.get(job_id)
        print("job id: {}", job['job_id'])

        block_header = self.create_block_header_submit(job['version'], job['prevhash'], job['merkle_branch'],
                                                       self.to_little_endian(ntime),
                                                       self.to_little_endian(job['nbits']), job['coinbase1'],
                                                       job['coinbase2'], self.to_little_endian(nonce), extranonce2)

        print("header:       {}", block_header)
        print("local_header: {}", job['local_header'])

        block_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(block_header)).digest()).digest().hex()
        target = int(self.bits_to_target(job['nbits']), 16)
        print("block hash: {}", block_hash)

        if int(block_hash, 16) < target:
            rpc_user = self.config.get('RPC', 'user')
            rpc_password = self.config.get('RPC', 'password')
            rpc_url = f"http://{rpc_user}:{rpc_password}@127.0.0.1:8332"

            block_data = {
                "method": "submitblock",
                "params": [block_header],
                "id": 1,
                "jsonrpc": "2.0"
            }

            response = requests.post(rpc_url, json=block_data).json()

    def set_difficulty(self, params, miner_sock):
        try:
            difficulty = int(params[0])
            if difficulty > self.max_difficulty:
                difficulty = self.max_difficulty
            elif difficulty < self.min_difficulty:
                difficulty = self.min_difficulty
            print(f"Estableciendo dificultad a {difficulty}")
            difficulty_message = {
                "id": None,
                "method": "mining.set_difficulty",
                "params": [difficulty]
            }
            miner_sock.sendall(json.dumps(difficulty_message).encode('utf-8'))
        except Exception as e:
            print(f"Error estableciendo la dificultad: {e}")

    def authorize_miner(self, params, miner_sock):
        username = params[0]
        password = params[1]
        response = {
            "id": None,
            "result": True,
            "error": None
        }

        miner_sock.sendall(json.dumps(response).encode('utf-8'))
        print(f"Minero {username} autorizado")

    def create_pool_header(self, version, prev_block, merkle_branch, ntime, nbits, coinbase1, coinbase2):
        version = struct.pack("<L", int(version, 16)).hex()
        prev_block = self.to_little_endian(prev_block)
        merkle_root = self.compute_merkle_root(merkle_branch + [coinbase1 + coinbase2])
        merkle_root = self.to_little_endian(merkle_root)
        nbits = struct.pack("<L", int(nbits, 16)).hex()
        ntime = struct.pack("<L", int(ntime, 16)).hex()
        nonce = struct.pack("<L", random.randint(0, 0xFFFFFFFF)).hex()

        header = version + prev_block + merkle_root + ntime + nbits + nonce
        return header

    def create_block_header(self, version, prev_block, merkle_branch, ntime, nbits, coinbase1, coinbase2):
        version = struct.pack("<L", int(version, 16)).hex()
        prev_block = swap_endianness_8chars_final(prev_block)

        merkle_root = self.compute_merkle_root(merkle_branch + [coinbase1 + coinbase2])
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
                new_merkle.append(hashlib.sha256(
                    hashlib.sha256(bytes.fromhex(merkle[i] + merkle[i + 1])).digest()).hexdigest())
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
                    new_merkle.append(hashlib.sha256(
                        hashlib.sha256(bytes.fromhex(merkle[i] + merkle[i + 1])).digest()).hexdigest())
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
        block_height = self.height
        coinbase_script = self.create_coinbase_script(block_height, miner_message, extranonce_placeholder)

        max_coinbase_script_size = 100 * 2
        actual_script_size = len(coinbase_script) + len(extranonce_placeholder)
        if actual_script_size > max_coinbase_script_size:
            coinbase_script = coinbase_script[:max_coinbase_script_size - len(extranonce_placeholder)]

        coinbase1 = (
                "02000000"
                + "01"
                + "0" * 64
                + "ffffffff"
                + self.int2varinthex((len(coinbase_script) + len(extranonce_placeholder)) // 2)
                + coinbase_script
                + extranonce_placeholder
        )

        num_outputs = "01"
        if op_return_data != "":
            op_return_script = self.create_op_return_script(op_return_data)
            op_return_length = self.int2varinthex(len(op_return_script) // 2)
            num_outputs = "02"
        else:
            op_return_script = ""
            op_return_length = ""

        coinbase2 = (
                "ffffffff"
                + num_outputs
                + self.int2lehex(self.block_template['coinbasevalue'], 8)
                + self.int2varinthex(len(pubkey_script) // 2)
                + pubkey_script
                + "00000000"
                + op_return_length
                + op_return_script
        )

        return coinbase1, coinbase2

    def calculate_coinbase_value(self):
        base_reward = self.block_template['coinbasevalue']
        total_fees = sum(tx['fee'] for tx in self.transactions)
        coinbase_value = base_reward + total_fees
        self.fee = coinbase_value
        return self.fee

    def create_op_return_script(self, data):
        if len(data) > 80:
            raise ValueError("Los datos para OP_RETURN no deben exceder los 80 bytes.")
        data_hex = data.encode('utf-8').hex()
        return "6a" + data_hex

    def select_random_transactions(self):
        max_size_limit = random.randint(200, 1024) * 1024

        transactions = self.block_template['transactions'].copy()
        random.shuffle(transactions)

        selected_transactions = []
        total_size = 0

        for transaction in transactions:
            transaction_size = 0
            if transaction:

                if transaction['weight']:
                    transaction_size = transaction['weight']

                    projected_size = total_size + transaction_size
                    if projected_size <= max_size_limit:
                        selected_transactions.append(transaction)
                        total_size += transaction_size
                    else:
                        break

        return selected_transactions

    def to_little_endian(self, hex_string):
        return ''.join([hex_string[i:i + 2] for i in range(0, len(hex_string), 2)][::-1])

    def version_to_hex(self, version):
        version_hex = '{:08x}'.format(version)
        return version_hex

    def bits_to_target(self, nbits):
        nbits = int(nbits, 16)
        exponent = nbits >> 24
        mantisa = nbits & 0xFFFFFF
        target = mantisa * (2 ** (8 * (exponent - 3)))
        target_hex = '{:064x}'.format(target)
        return target_hex

    def miner_message(self):
        longitud_frase = 100
        caracteres = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890!@#$%^&*()_+-=[]{}|;:,.<>?`~"
        frase = ''.join(random.choice(caracteres) for _ in range(longitud_frase))
        return frase

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
        if height is None:
            raise ValueError("Height is None")
        width = (height.bit_length() + 7) // 8
        fmt = '{:0%dx}' % (width * 2)
        coinbase_height = fmt.format(height)
        return struct.pack('<B', width).hex() + coinbase_height

    def bitcoinaddress2hash160(self, addr):
        table = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        num = 0
        for char in addr:
            num = num * 58 + table.index(char)
        combined = num.to_bytes(25, byteorder='big')
        if hashlib.sha256(hashlib.sha256(combined[:-4]).digest()).digest()[:4] != combined[-4:]:
            raise ValueError("Checksum does not match")
        hash160 = combined[1:-4]
        return hash160.hex()


def swap_endianness_8chars_final(hex_string):
    return ''.join(
        [hex_string[i + 6:i + 8] + hex_string[i + 4:i + 6] + hex_string[i + 2:i + 4] + hex_string[i:i + 2] for i in
         range(0, len(hex_string), 8)])


def main():
    config = configparser.ConfigParser()
    config.read('config_zmq.ini')

    pool_address = f"stratum+tcp://{config['POOL']['host']}:{config['POOL']['port']}"
    miner_port = int(config['STRATUM']['port'])

    proxy = StratumProxy(pool_address, miner_port, config)
    proxy.start()


if __name__ == '__main__':
    main()
