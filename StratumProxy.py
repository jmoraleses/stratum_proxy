import hashlib
import random
import struct
import concurrent.futures
import configparser
import json
import select
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import signal
import sys

import pandas as pd
import requests

from BlockTemplate import BlockTemplate


class StratumProxy:
    def __init__(self, pool_address, miner_port, config):
        self.pool_address = pool_address
        self.miner_port = miner_port
        self.pool_host = self.pool_address.split('//')[1].split(':')[0]
        self.pool_port = int(self.pool_address.split(':')[2])
        self.pool_sock = None
        self.stratum_processing = StratumProcessing(self, config)
        self.executor = ThreadPoolExecutor(max_workers=10000)
        self.job_semaphore = threading.Semaphore(1000)  # Limitar a 1000 trabajos
        self.generated_jobs = []
        self.generated_merkle_roots = []
        self.valid_merkle_count = 0
        self.merkle_counts_file = config.get('FILES', 'merkle_file')
        self.merkle_counts = self.load_merkle_counts(self.merkle_counts_file)
        self.server_sock = None
        self.active_sockets = []
        self.stop_event = threading.Event()
        self.mine = False
        self.jobs = {}

    def load_merkle_counts(self, file_path):
        try:
            df = pd.read_csv(file_path)
            return df
        except Exception as e:
            print(f"Error loading merkle counts: {e}")
            return pd.DataFrame(columns=['merkle_root'])

    def job_generator(self):
        time.sleep(2)
        while True:
            self.job_semaphore.acquire()
            try:
                self.stratum_processing.generate_jobs()
                # Verificar si hay trabajos generados y manejar la espera si no hay
                if not self.stratum_processing.block_template:
                    print("Plantilla de bloque no disponible. Esperando antes de intentar nuevamente...")
                    time.sleep(1)
                    continue
                if len(self.generated_jobs) == 0:
                    # print("No se generaron trabajos.")
                    time.sleep(1)
            except Exception as e:
                print(f"Error al generar trabajos: {e}")
            finally:
                self.job_semaphore.release()

    def block_template_updater(self):
        while not self.stop_event.is_set():
            self.stratum_processing.block_template_fetcher.fetch_template()
            time.sleep(2)

    def merkle_root_counter(self):
        time.sleep(60)
        while not self.stop_event.is_set():
            self.count_valid_merkle_roots()
            time.sleep(60)

    def check_merkle_root(self, merkle_root):
        if merkle_root:
            return self.merkle_counts['merkle_root'].apply(lambda root: merkle_root.startswith(root)).any()
        return False

    def count_valid_merkle_roots(self):
        valid_count = sum(1 for root in self.generated_merkle_roots if self.check_merkle_root(root))
        print(
            f"Cantidad de merkle roots válidos en el último minuto: {valid_count} de {len(self.generated_merkle_roots)}")
        self.generated_merkle_roots = []

    def start(self):
        threading.Thread(target=self.block_template_updater, daemon=True).start()
        threading.Thread(target=self.merkle_root_counter, daemon=True).start()
        threading.Thread(target=self.job_generator, daemon=True).start()
        print("Creando trabajos, esperando...")
        # time.sleep(10)
        if self.connect_to_pool():
            self.wait_for_miners()

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
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(('0.0.0.0', self.miner_port))
            self.server_sock.listen(1000)
            print(f"Esperando conexiones de mineros en el puerto {self.miner_port}...")

            while not self.stop_event.is_set():
                try:
                    miner_sock, addr = self.server_sock.accept()
                    self.active_sockets.append(miner_sock)
                    print(f"Conexión establecida con el minero: {addr}")
                    threading.Thread(target=self.handle_miner_connection, args=(miner_sock,), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Error aceptando conexión de minero: {e}")
        except Exception as e:
            print(f"Error esperando a los mineros: {e}")
        finally:
            self.stop()

    def handle_miner_connection(self, miner_sock):
        try:
            pool_sock = self.create_pool_socket()
            if not pool_sock:
                miner_sock.close()
                return

            pool_buffer = ""
            miner_buffer = ""

            while True:
                ready_socks, _, _ = select.select([miner_sock, pool_sock], [], [], 1)
                for sock in ready_socks:
                    if sock == miner_sock:
                        miner_buffer = self.proxy_message(miner_sock, pool_sock, miner_buffer, "miner")
                        if miner_buffer is None:
                            miner_sock.close()
                            pool_sock.close()
                            return
                    elif sock == pool_sock:
                        pool_buffer = self.proxy_message(pool_sock, miner_sock, pool_buffer, "pool")
                        if pool_buffer is None:
                            miner_sock.close()
                            pool_sock.close()
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
                message_json = json.loads(message)

                if message_json.get("method") == "mining.notify" and source == "pool" and self.mine is False:
                    self.mine = True
                    self.stratum_processing.send_job(message_json, message, dest_sock)
                elif message_json.get("method") == "mining.submit" and source == "miner":
                    dest_sock.sendall(message.encode('utf-8') + b'\n')
                    self.stratum_processing.process_submit(message_json)
                    print(f"Mensaje {source} => {message}")
                else:
                    dest_sock.sendall(message.encode('utf-8') + b'\n')
                    print(f"Mensaje {source} => {message}")
                    self.mine = False
            return buffer
        except Exception as e:
            print(f"Error en proxy_message: {e}")
            return None

    def stop(self):
        print("Cerrando todas las conexiones...")
        self.stop_event.set()
        for sock in self.active_sockets:
            sock.close()
        if self.server_sock:
            self.server_sock.close()
        if self.pool_sock:
            self.pool_sock.close()
        print("Conexiones cerradas y recursos liberados.")


class StratumProcessing:
    def __init__(self, proxy, config):
        self.config = config
        self.proxy = proxy
        self.db_file = config.get('DATABASE', 'db_file')
        self.block_template_fetcher = BlockTemplate(config)
        self.block_template = None
        self.coinbase_message = None
        self.transactions = None
        self.address = config.get('ADDRESS', 'address')
        self.version = None
        self.height = None
        self.prevhash = None
        self.nbits = None
        self.target = None
        self.transactions_raw = None
        self.min_difficulty = int(config.get('DIFFICULTY', 'min'))  # Inicializar la dificultad sugerida
        self.max_difficulty = int(config.get('DIFFICULTY', 'max'))  # Inicializar la dificultad sugerida
        self.first_job = False
        self.coinbase1 = None
        self.coinbase2 = None

    def generate_jobs(self):
        self.update_block_template()
        if self.block_template is None:
            print("Plantilla de bloque no disponible. Esperando antes de intentar nuevamente...")
            time.sleep(1)  # Esperar antes de intentar nuevamente
            return

        if self.coinbase1 is None or self.coinbase2 is None:
            # print("Coinbase no encontrada, esperando...")
            # time.sleep(1)  # Esperar antes de intentar nuevamente
            return


        prevhash = self.to_little_endian(swap_endianness_8chars_final(self.block_template['previousblockhash']))

        version = self.version_to_hex(self.block_template['version'])
        nbits = self.block_template['bits']
        ntime = self.to_little_endian(self.int2lehex(int(time.time()), 4))

        jobs = self.create_job_from_blocktemplate(version, prevhash, self.coinbase1, self.coinbase2, nbits, ntime)
        if jobs:
            self.proxy.generated_jobs.extend(jobs)  # Añadir trabajos generados a la lista

    def update_block_template(self):
        template = self.block_template_fetcher.get_template()
        self.block_template = template
        if self.height != self.block_template['height']:
            self.proxy.generated_jobs = []
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

    def worker_job(self, proxy, version, prev_block, coinbase1, coinbase2, nbits, ntime, stop_event):

        while not stop_event.is_set():
            transactions = self.select_random_transactions()
            extranonce = "00000000"
            coinbase_tx = coinbase1 + extranonce + coinbase2
            coinbase_tx_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_tx)).digest()).digest().hex()
            merkle_hashes = [coinbase_tx_hash]
            for tx in transactions:
                if 'hash' in tx and 'data' in tx:
                    merkle_hashes.append(tx['hash'])
                else:
                    raise ValueError("Cada transacción debe contener 'hash' y 'data'")
            merkle_branch = self.compute_merkle_branch(merkle_hashes)
            merkle_root_candidate = self.compute_merkle_root(merkle_hashes)
            proxy.generated_merkle_roots.append(merkle_root_candidate)

            if self.check_merkle_root(merkle_root_candidate):
                job = {
                    'version': version,
                    'prevhash': prev_block,
                    'coinbase1': coinbase1,
                    'coinbase2': coinbase2,
                    'merkle_branch': merkle_branch,
                    'merkle_root': merkle_root_candidate,
                    'nbits': nbits,
                    'ntime': ntime,
                    'height': self.height,
                    # 'job_id': random.randint(0, 0xFFFFFFFF),
                    'clean_jobs': False,
                }
                stop_event.set()  # Señalar a otros hilos que deben detenerse
                return job

    def create_job_from_blocktemplate(self, version, prevhash, coinbase1, coinbase2, nbits, ntime):
        valid_jobs = []
        stop_event = threading.Event()
        height = self.height

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(self.worker_job, self.proxy, version, prevhash,
                                       coinbase1, coinbase2, nbits, ntime, stop_event) for _ in range(1000)]
            for future in concurrent.futures.as_completed(futures):
                job = future.result()
                if job is not None:
                    valid_jobs.append(job)
                    break
        if height != self.block_template['height']:
            return None
        return valid_jobs

    def send_job(self, message_json, message, miner_sock):
        # job_id = str(uuid.uuid4().hex[:6])
        job_params = message_json["params"]
        self.coinbase1 = str(job_params[2])
        self.coinbase2 = str(job_params[3])
        job_id = str(job_params[0])
        ntime = job_params[7]
        clean_jobs = job_params[8]

        if len(self.proxy.generated_jobs) > 0:
            # for _ in range(len(self.proxy.generated_jobs)):
            job = self.proxy.generated_jobs.pop(0)  # Obtener el primer trabajo generado
            # self.proxy.job_semaphore.release()  # Incrementar el contador del semáforo
            # job['job_id'] = job_id
            self.proxy.jobs[job_id] = job
            # self.proxy.jobs_work.append(self.proxy.jobs[job_id])
            local_notify = {
                "id": None,
                "method": "mining.notify",
                "params": [job_id, job['prevhash'], self.coinbase1, self.coinbase2, job['merkle_branch'],
                           job['version'],
                           job['nbits'], ntime, clean_jobs]
            }
            miner_sock.sendall(json.dumps(local_notify).encode('utf-8') + b'\n')
            print(f"Local notify:\n{local_notify}")
            # print(f"Trabajo enviado: {job_id}")>
        else:
            print("No hay trabajos disponibles en este momento.")
            miner_sock.sendall(json.dumps(message).encode('utf-8') + b'\n')

    def process_submit(self, message_json):
        print(message_json)
        submit_params = message_json["params"]
        job_id = submit_params[1]
        extranonce2 = submit_params[2]
        ntime = submit_params[3]
        nonce = submit_params[4]

        print(f"Procesando submit del minero, ntime: {ntime}:")
        print(f"extranonce2: {extranonce2}, ntime: {ntime}, nonce: {nonce}")

        job = self.proxy.jobs[job_id]

        if job is None:
            print("Job ID no encontrado.")
            return

        if job['height'] != self.height:
            print("El bloque cambió, la altura no coincide.")
            return

        # Crear el coinbase transaction
        coinbase_transaction = job['coinbase1'] + extranonce2 + job['coinbase2']
        coinbase_tx_hash = double_sha256(coinbase_transaction)

        # Crear el Merkle Root
        merkle_hashes = [coinbase_tx_hash] + job['merkle_branch']
        merkle_root = self.compute_merkle_root(merkle_hashes)
        merkle_root = self.to_little_endian(merkle_root)

        # Crear el encabezado del bloque
        block_header = (
                job['version'] +
                job['prevhash'] +
                merkle_root +
                ntime +
                job['nbits'] +
                self.to_little_endian(nonce)
        )

        # Verificar el bloque
        target = self.bits_to_target(job['nbits'])

        if verify_block_header(block_header, target):
            print("¡¡¡El bloque es válido!!!")
            rpc_user = self.config.get('RPC', 'user')
            rpc_password = self.config.get('RPC', 'password')
            rpc_host = self.config.get('RPC', 'host')
            rpc_port = self.config.get('RPC', 'port')
            rpc_url = f"http://{rpc_user}:{rpc_password}@{rpc_host}:{rpc_port}"
            block_data = {"method": "submitblock", "params": [block_header], "id": 1, "jsonrpc": "2.0"}
            response = requests.post(rpc_url, json=block_data).json()
            print(f"Respuesta del servidor RPC: {response}")
        else:
            print("El bloque no es válido.")

        print(f"Encabezado del bloque: {block_header}")

        # message_json = {'id': job_id, 'error': None, 'result': True}
        # dest_sock.sendall(json.dumps(message_json).encode('utf-8') + b'\n')
        # if 'error' in response and response['error']:
        #     error = response['error']
        #     response = {"id": None, "result": None, "error": error}
        #     miner_sock.sendall(json.dumps(response).encode('utf-8') + b'\n')
        # else:
        #     response = {"id": None, "result": True, "error": None}
        #     miner_sock.sendall(json.dumps(response).encode('utf-8') + b'\n')

    def handle_extranonce_subscribe(self, request_id, miner_sock):
        try:
            response = {
                "id": request_id,
                "result": True,
                "error": None
            }
            miner_sock.sendall(json.dumps(response).encode('utf-8') + b'\n')
            print(f"Extranonce subscribe handled: {request_id}")
        except Exception as e:
            print(f"Error handling extranonce subscribe: {e}")
            response = {
                "id": request_id,
                "result": None,
                "error": str(e)
            }
            miner_sock.sendall(json.dumps(response).encode('utf-8') + b'\n')

    def suggest_difficulty(self, params, request_id, miner_sock):
        if len(params) == 0:
            response = {"id": request_id, "result": None, "error": [23, "No difficulty suggested", ""]}
            miner_sock.sendall(json.dumps(response).encode('utf-8') + b'\n')
            return

        suggested_difficulty = int(params[0])
        difficulty = max(self.min_difficulty, min(self.max_difficulty, suggested_difficulty))
        self.suggested_difficulty = difficulty
        print(f"Dificultad sugerida: {self.suggested_difficulty}, Establecida: {difficulty}")

        difficulty_message = {"id": request_id, "method": "mining.set_difficulty", "params": [difficulty]}
        miner_sock.sendall(json.dumps(difficulty_message).encode('utf-8') + b'\n')

    def authorize_miner(self, params, request_id, miner_sock):
        response = {"id": request_id, "result": True, "error": None}
        miner_sock.sendall(json.dumps(response).encode('utf-8') + b'\n')
        print(f"Minero {params[0]} autorizado")

    def set_difficulty(self, params, miner_sock):
        try:
            difficulty = int(params[0])
            difficulty = max(self.min_difficulty, min(self.max_difficulty, difficulty))
            print(f"Estableciendo dificultad a {difficulty}")
            difficulty_message = {"id": None, "method": "mining.set_difficulty", "params": [difficulty]}
            miner_sock.sendall(json.dumps(difficulty_message).encode('utf-8') + b'\n')
        except Exception as e:
            print(f"Error estableciendo la dificultad: {e}")

    def create_block_header(self, version, prev_block, merkle_branch, ntime, nbits, coinbase1, coinbase2):
        coinbase_transaction = coinbase1 + coinbase2
        coinbase_tx_hash = hashlib.sha256(
            hashlib.sha256(bytes.fromhex(coinbase_transaction)).digest()).digest().hex()
        merkle_hashes = [coinbase_tx_hash] + merkle_branch
        merkle_root = self.compute_merkle_root(merkle_hashes)

        # version = self.version_to_hex(self.block_template['version'])
        # version = swap_endianness_8chars_final(version)
        # prev_block = swap_endianness_8chars_final(prev_block)
        # prev_block = self.to_little_endian(prev_block)

        merkle_root = self.to_little_endian(merkle_root)
        # nbits = struct.pack("<L", int(nbits, 16)).hex()
        # nbits = swap_endianness_8chars_final(nbits)
        # ntime = struct.pack("<L", ntime).hex()
        # ntime = swap_endianness_8chars_final(ntime)
        nonce = struct.pack("<L", random.randint(0, 0xFFFFFFFF)).hex()  # Nonce simulado

        header = version + prev_block + merkle_root + ntime + nbits + nonce

        return header, version, prev_block, merkle_root, nbits, ntime, nonce, coinbase1, coinbase2

    def create_block_header_submit(self, version, prevhash, merkle_branch, ntime, nbits, coinbase1, coinbase2,
                                   nonce,
                                   extranonce2):
        version = self.to_little_endian(version)
        prevhash = self.to_little_endian(prevhash)
        coinbase_transaction = coinbase1 + extranonce2 + coinbase2
        coinbase_tx_hash = hashlib.sha256(
            hashlib.sha256(bytes.fromhex(coinbase_transaction)).digest()).digest().hex()
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
                        hashlib.sha256(
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

    def tx_make_coinbase_stratum(self, miner_message, extranonce_placeholder="00000000", coinbasevalue=3,
                                 op_return_data=""):
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

        coinbase2 = "ffffffff" + num_outputs + self.int2lehex(coinbasevalue, 8) + self.int2varinthex(
            len(pubkey_script) // 2) + pubkey_script + "00000000" + op_return_length + op_return_script
        return coinbase1, coinbase2

    def calculate_coinbase_value(self, transactions):
        base_reward = self.block_template['coinbasevalue']
        total_fees = sum(tx['fee'] for tx in transactions)
        return base_reward + total_fees

    def create_op_return_script(self, data):
        if len(data) > 80:
            raise ValueError("Los datos para OP_RETURN no deben exceder los 80 bytes.")
        return "6a" + data.encode('utf-8').hex()

    def select_random_transactions(self):
        transactions = self.block_template["transactions"]
        min_transactions, max_transactions = 4, 13
        num_transactions = random.randint(min_transactions, max_transactions)
        selected_transactions = random.sample(transactions, num_transactions)
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

    def check_merkle_root(self, merkle_root):
        if merkle_root:
            return self.proxy.merkle_counts['merkle_root'].apply(lambda root: merkle_root.startswith(root)).any()
        return False


def swap_endianness_8chars_final(hex_string):
    return ''.join(
        [hex_string[i + 6:i + 8] + hex_string[i + 4:i + 6] + hex_string[i + 2:i + 4] + hex_string[i:i + 2] for i in
         range(0, len(hex_string), 8)])


def double_sha256(data):
    """ Aplica SHA256 dos veces a los datos dados. """
    return hashlib.sha256(hashlib.sha256(bytes.fromhex(data)).digest()).hexdigest()


def verify_block_header(block_header, target):
    """ Verifica que el hash del encabezado del bloque cumple con el target. """
    block_hash = double_sha256(block_header)
    print(f"Hash: {block_hash}")
    return int(block_hash, 16) < int(target, 16)


def main():
    config = configparser.ConfigParser()
    config.read('config.ini')

    pool_address = f"stratum+tcp://{config['POOL']['host']}:{config['POOL']['port']}"
    miner_port = int(config['STRATUM']['port'])

    proxy = StratumProxy(pool_address, miner_port, config)

    def signal_handler(sig, frame):
        print('Interrupción recibida, cerrando conexiones...')
        proxy.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    proxy.start()


if __name__ == '__main__':
    main()
