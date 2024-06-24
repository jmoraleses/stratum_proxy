import concurrent
import configparser
import hashlib
import random
import signal
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pandas as pd
import requests

from BlockTemplate import BlockTemplate
from DatabaseHandler import DatabaseHandler

class MinerCPU:
    def __init__(self, config):
        self.db_handler = DatabaseHandler(config.get('DATABASE', 'db_file')).create_table()
        self.stratum_processing = StratumProcessing(config, self)
        self.generated_merkle_roots = []
        self.valid_merkle_count = 0
        self.merkle_counts_file = config.get('FILES', 'merkle_file')
        self.merkle_counts = self.load_merkle_counts(self.merkle_counts_file)
        self.job_queue = Queue()
        self.merkle_roots = []

    def block_template_updater(self):
        while True:
            self.stratum_processing.block_template_fetcher.fetch_template()
            time.sleep(4)

    def merkle_root_counter(self):
        time.sleep(60)
        while True:
            self.count_valid_merkle_roots()
            time.sleep(60)

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
        valid_merkles = sum(1 for merkle in self.merkle_roots)
        print(f"Cantidad de merkle roots válidos en el último minuto: {valid_merkles} de {len(self.generated_merkle_roots)}")
        self.generated_merkle_roots = []

    def job_generator(self):
        time.sleep(2)
        while True:
            jobs = self.stratum_processing.generate_jobs()
            if jobs:
                for job in jobs:
                    self.job_queue.put(job)

    def job_processor(self):
        while True:
            job = self.job_queue.get()
            if job:
                self.stratum_processing.process_job(job)
                self.job_queue.task_done()
            else:
                time.sleep(0.1)  # Evita el uso intensivo de la CPU cuando la cola está vacía

    def start(self):
        threading.Thread(target=self.block_template_updater, daemon=True).start()
        threading.Thread(target=self.merkle_root_counter, daemon=True).start()

        # Usar ThreadPoolExecutor para manejar hilos productores y consumidores
        with ThreadPoolExecutor(max_workers=1000) as executor:
            for _ in range(10):  # Ajustar el número de hilos productores
                executor.submit(self.job_generator)
            for _ in range(990):  # Ajustar el número de hilos consumidores
                executor.submit(self.job_processor)

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
        self.target = None
        self.transactions_raw = None
        self.jobs = {}

    def process_job(self, job):
        ntime = job['ntime']
        nbits = job['nbits']
        prevhash = job['prevhash']
        version = job['version']
        coinbase1 = job['coinbase1']
        coinbase2 = job['coinbase2']
        merkle_branch = job['merkle_branch']
        merkle_root = job['merkle_root']

        if merkle_root not in self.proxy.merkle_roots:
            self.proxy.merkle_roots.append(merkle_root)
            nonce_df = self.check_merkle_nonce(merkle_root)
            if nonce_df is not None:
                nonce_init = int(nonce_df.ljust(8, '0'), 16)
                len_nonce = 8 - len(nonce_df)
                nonce_end = nonce_init + (16 ** len_nonce)

                for nonce in range(nonce_init, nonce_end):
                    nonce_hex = f"{nonce:08x}"
                    block_header = version + prevhash + merkle_root + ntime + nbits + nonce_hex
                    block_hash = hashlib.sha256(
                        hashlib.sha256(bytes.fromhex(block_header)).digest()
                    ).digest().hex()
                    target = int(self.bits_to_target(job['nbits']), 16)
                    target = '{:064x}'.format(target)
                    if int(block_hash, 16) < int(target, 16):
                        print(f"blockhash: {block_hash}")
                        try:
                            response = self.block_template_fetcher.submit_block(block_header)
                            print(f"Respuesta del servidor RPC: {response}")
                        except requests.exceptions.RequestException as e:
                            print(f"Error al enviar el bloque: {e}")

    def miner(self):
        while True:
            if self.proxy.generated_jobs:
                futures = []
                while self.proxy.generated_jobs:
                    job = self.proxy.generated_jobs.pop(0)
                    if job:
                        futures.append(self.proxy.executor.submit(self.process_job, job))

                for future in futures:
                    try:
                        future.result()  # Espera a que todas las tareas se completen
                    except Exception as e:
                        print(f"Error processing job: {e}")
            else:
                time.sleep(0.5)

    def check_merkle_nonce(self, merkle_root):
        match = self.proxy.merkle_counts[self.proxy.merkle_counts['merkle_root'].apply(lambda x: merkle_root.startswith(x))]
        if not match.empty:
            row = match.iloc[0]  # Obtener la primera coincidencia
            return row['nonce']
        return None

    def generate_jobs(self):
        self.update_block_template()
        if self.block_template is None:
            print("Plantilla de bloque no disponible. Esperando antes de intentar nuevamente...")
            time.sleep(1)  # Esperar antes de intentar nuevamente
            return

        prevhash = self.to_little_endian(swap_endianness_8chars_final(self.block_template['previousblockhash']))

        version = self.version_to_hex(self.block_template['version'])
        nbits = self.block_template['bits']
        ntime = self.to_little_endian(self.int2lehex(int(time.time()), 4))

        jobs = self.create_job_from_blocktemplate(version, prevhash, nbits, ntime)
        return jobs

    def update_block_template(self):
        template = self.block_template_fetcher.get_template()
        self.block_template = template
        if self.height != self.block_template['height']:
            self.proxy.generated_jobs = []
            self.proxy.merkle_roots = []
        self.height = self.block_template['height']

    def worker_job(self, proxy, version, prev_block, nbits, ntime, stop_event):
        while True:
            self.transactions = self.select_random_transactions()
            extranonce_placeholder = "00000000"
            miner_message = self.miner_message()
            coinbase1, coinbase2 = self.tx_make_coinbase_stratum(miner_message, extranonce_placeholder)
            coinbase_tx = coinbase1 + extranonce_placeholder + coinbase2
            coinbase_tx_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_tx)).digest()).digest().hex()

            merkle_hashes = [coinbase_tx_hash]
            for tx in self.transactions:
                if 'hash' in tx and 'data' in tx:
                    merkle_hashes.append(tx['hash'])
                else:
                    raise ValueError("Cada transacción debe contener 'hash' y 'data'")
            merkle_branch = self.compute_merkle_branch(merkle_hashes)
            merkle_root_candidate = self.compute_merkle_root(merkle_hashes)
            proxy.generated_merkle_roots.append(merkle_root_candidate)

            job = {
                'version': self.to_little_endian(version),
                'prevhash': prev_block,
                'coinbase1': coinbase1,
                'coinbase2': coinbase2,
                'merkle_branch': merkle_branch,
                'merkle_root': self.to_little_endian(merkle_root_candidate),
                'nbits': nbits,
                'ntime': ntime,
                'height': self.height,
            }
            return job

    def create_job_from_blocktemplate(self, version, prevhash, nbits, ntime):
        valid_jobs = []
        stop_event = threading.Event()
        height = self.height

        with ThreadPoolExecutor(max_workers=1000) as executor:
            futures = [executor.submit(self.worker_job, self.proxy, version, prevhash, nbits, ntime, stop_event) for _ in range(100000)]
            for future in concurrent.futures.as_completed(futures):
                job = future.result()
                if job is not None:
                    valid_jobs.append(job)
                    break
        if height != self.block_template['height']:
            return None
        return valid_jobs

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

    def tx_make_coinbase_stratum(self, miner_message, extranonce_placeholder="00000000", op_return_data=""):
        pubkey_script = "76a914" + self.bitcoinaddress2hash160(self.address) + "88ac"
        block_height = self.height
        coinbase_script = self.create_coinbase_script(block_height, miner_message, extranonce_placeholder)

        max_coinbase_script_size = 100 * 2  # 100 bytes in hex characters
        actual_script_size = len(coinbase_script) + len(extranonce_placeholder)
        if actual_script_size > max_coinbase_script_size:
            coinbase_script = coinbase_script[:max_coinbase_script_size - len(extranonce_placeholder)]

        coinbase1 = (
                "02000000"  # Transacción de versión
                + "01"  # Número de entradas
                + "0" * 64  # Hash previo (32 bytes de ceros)
                + "ffffffff"  # Índice previo (4 bytes de f's)
                + self.int2varinthex((len(coinbase_script) + len(extranonce_placeholder)) // 2)
                + coinbase_script
                + extranonce_placeholder
        )
        self.fee = self.calculate_coinbase_value()
        coinbase2 = (
                "ffffffff"  # Secuencia (4 bytes de f's)
                + "01"  # Número de salidas
                + self.int2lehex(self.fee, 8)  # Recompensa en little-endian (8 bytes)
                + self.int2varinthex(len(pubkey_script) // 2)  # Longitud del pubkey_script
                + pubkey_script  # Script público
                + "00000000"  # Bloque de tiempo de lock (4 bytes de ceros)
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

def main():
    config = configparser.ConfigParser()
    config.read('config.ini')
    miner_cpu = MinerCPU(config)
    miner_cpu.start()

    def signal_handler(sig, frame):
        print("Señal recibida, cerrando el servidor...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    main()
