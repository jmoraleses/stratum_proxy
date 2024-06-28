import configparser
import hashlib
import logging
import multiprocessing
import random
import signal
import struct
import sys
import threading
import time
from multiprocessing import Process, Manager
import pandas as pd
import requests
from BlockTemplate import BlockTemplate
from sha256_opencl import sha256_pyopencl

NUM_ZEROS = 15


class MinerCPU:
    def __init__(self, config):
        self.stratum_processing = StratumProcessing(config, self)
        self.valid_merkle_count = 0
        self.merkle_counts_file = config.get('FILES', 'merkle_file')
        self.merkle_counts = self.load_merkle_counts(self.merkle_counts_file)
        manager = Manager()
        self.job_queue = manager.list()
        self.generated_merkle_roots = manager.list()
        self.merkle_roots = manager.list()
        self.merkle_roots_done = manager.list()

    def block_template_updater(self):
        while True:
            self.stratum_processing.block_template_fetcher.fetch_template()
            time.sleep(2)

    def merkle_root_counter(self, job_queue, generated_merkle_roots, merkle_roots):
        wait = 60
        time.sleep(wait)
        while True:
            self.count_valid_merkle_roots(wait, job_queue, generated_merkle_roots, merkle_roots)
            time.sleep(wait)

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

    def count_valid_merkle_roots(self, wait, job_queue, generated_merkle_roots, merkle_roots):
        total_generated = len(generated_merkle_roots)
        hashrate_hps = (total_generated * (16 ** 8)) / wait
        hashrate_phps = hashrate_hps / 10 ** 12

        print(f"merkle roots {len(self.merkle_roots_done)}:{len(merkle_roots)} válidos de {total_generated} totales (Hashrate: {hashrate_phps:.5f} TH/s)")
        self.generated_merkle_roots[:] = []
        self.merkle_roots[:] = []
        self.merkle_roots_done[:] = []

    def job_generator(self, job_queue, generated_merkle_roots, merkle_roots):
        while True:
            try:
                self.stratum_processing.generate_jobs(job_queue, generated_merkle_roots, merkle_roots)
            except Exception as e:
                logging.error(f"Error en job_generator: {e}")
                time.sleep(1)

    def job_processor(self):
        time.sleep(10)
        while True:
            if self.job_queue:
                job = self.job_queue.pop(0)
                if job:
                    self.stratum_processing.process_job_opencl(job)
            else:
                time.sleep(0.1)

    def start(self):
        threading.Thread(target=self.block_template_updater, daemon=True).start()
        # threading.Thread(target=self.merkle_root_counter, daemon=True).start()
        counter_processes = [
            multiprocessing.Process(target=self.merkle_root_counter,
                                    args=(self.job_queue, self.generated_merkle_roots, self.merkle_roots))
            for _ in range(1)
        ]
        for process_c in counter_processes:
            process_c.start()

        time.sleep(3)

        num_generators = 4
        generator_processes = [
            multiprocessing.Process(target=self.job_generator, args=(self.job_queue, self.generated_merkle_roots, self.merkle_roots))
            for _ in range(num_generators)
        ]

        for process_g in generator_processes:
            process_g.start()

        try:
            self.job_processor()
        finally:
            for process_g in generator_processes:
                process_g.terminate()
                process_g.join()
            for process_c in counter_processes:
                process_c.terminate()
                process_c.join()


class StratumProcessing:
    def __init__(self, config, proxy):
        self.config = config
        self.proxy = proxy
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

    def process_job_opencl(self, job):
        ntime = job['ntime']
        nbits = job['nbits']
        prevhash = job['prevhash']
        version = job['version']
        coinbase1 = job['coinbase1']
        coinbase2 = job['coinbase2']
        merkle_branch = job['merkle_branch']
        merkle_root = job['merkle_root']

        nonce_df = self.check_merkle_nonce(merkle_root)
        if nonce_df is not None:
            self.proxy.merkle_roots_done.append(merkle_root)
            for nonce_one in nonce_df:
                nonce_init = int(nonce_one.ljust(8, '0'), 16)
                len_nonce = 8 - len(nonce_one)
                nonce_end = nonce_init + (16 ** len_nonce)

                nonces = [f"{nonce:08x}" for nonce in range(nonce_init, nonce_end)]
                headers = [
                    version + prevhash + merkle_root + ntime + nbits + nonce
                    for nonce in nonces
                ]
                hashes = sha256_pyopencl(headers, num_zeros=NUM_ZEROS)
                target = int(self.bits_to_target(job['nbits']), 16)

                for i in range(len(hashes)):
                    hash_hex = hashes[i]['hash']
                    if int(hash_hex, 16) < target:
                        header = hashes[i]['data']
                        print(f"blockhash: {hash_hex}")
                        try:
                            response = self.block_template_fetcher.submit_block(header)
                            print(f"Respuesta del servidor RPC: {response}")
                        except requests.exceptions.RequestException as e:
                            print(f"Error al enviar el bloque: {e}")

    def process_job_cpu(self, job):
        ntime = job['ntime']
        nbits = job['nbits']
        prevhash = job['prevhash']
        version = job['version']
        coinbase1 = job['coinbase1']
        coinbase2 = job['coinbase2']
        merkle_branch = job['merkle_branch']
        merkle_root = job['merkle_root']

        # self.proxy.merkle_roots.append(merkle_root)
        nonce_df = self.check_merkle_nonce(merkle_root)
        if nonce_df is not None:
            self.proxy.merkle_roots_done.append(merkle_root)
            for nonce_one in nonce_df:
                nonce_init = int(nonce_one.ljust(8, '0'), 16)
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

    def check_merkle_nonce(self, merkle_root):
        matches = self.proxy.merkle_counts[
            self.proxy.merkle_counts['merkle_root'].apply(lambda x: merkle_root.startswith(x))]

        if not matches.empty:
            nonces = matches['nonce'].tolist()
            return nonces
        return []

    def generate_jobs(self, job_queue, generated_merkle_roots, merkle_roots):
        self.update_block_template()
        prevhash = self.to_little_endian(swap_endianness_8chars_final(self.block_template['previousblockhash']))
        version = self.version_to_hex(self.block_template['version'])
        nbits = self.block_template['bits']
        ntime = self.to_little_endian(self.int2lehex(int(time.time()), 4))
        self.worker_job(version, prevhash, nbits, ntime, job_queue, generated_merkle_roots, merkle_roots)

    def update_block_template(self):
        template = self.block_template_fetcher.get_template()
        self.block_template = template
        if self.height != self.block_template['height']:
            self.proxy.merkle_roots[:] = []
            self.proxy.job_queue[:] = []
            self.proxy.generated_merkle_roots[:] = []
            self.proxy.merkle_roots_done[:] = []
        self.height = self.block_template['height']

    def worker_job(self, version, prev_block, nbits, ntime, job_queue, generated_merkle_roots, merkle_roots):
        extranonce_placeholder = "00000000"
        miner_message = self.miner_message()
        coinbase1, coinbase2 = self.tx_make_coinbase_stratum(miner_message, extranonce_placeholder)
        coinbase_tx = coinbase1 + extranonce_placeholder + coinbase2
        coinbase_tx_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_tx)).digest()).digest().hex()

        while True:
            self.transactions = self.select_random_transactions()

            merkle_hashes = [coinbase_tx_hash]
            for tx in self.transactions:
                if 'hash' in tx and 'data' in tx:
                    merkle_hashes.append(tx['hash'])
                else:
                    raise ValueError("Cada transacción debe contener 'hash' y 'data'")
            merkle_branch = self.compute_merkle_branch(merkle_hashes)
            merkle_root_candidate = self.compute_merkle_root(merkle_hashes)

            generated_merkle_roots.append(merkle_root_candidate)
            if self.check_merkle_root(merkle_root_candidate):

                merkle_roots.append(merkle_root_candidate)

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
                job_queue.append(job)

    def compute_merkle_branch(self, merkle):
        branches = []
        while len(merkle) > 1:
            if len(merkle) % 2 != 0:
                merkle.append(merkle[-1])

            # Utilizar list comprehension para simplificar la creación de new_merkle
            new_merkle = [
                hashlib.sha256(hashlib.sha256(bytes.fromhex(merkle[i] + merkle[i + 1])).digest()).hexdigest()
                for i in range(0, len(merkle), 2)
            ]

            branches.extend(merkle[i] for i in range(0, len(merkle), 2))
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
        # self.fee = self.calculate_coinbase_value()
        self.fee = self.block_template['coinbasevalue']
        coinbase2 = (
                "ffffffff"
                + "01"
                + self.int2lehex(self.fee, 8)
                + self.int2varinthex(len(pubkey_script) // 2)
                + pubkey_script
                + "00000000"
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
        min_transactions, max_transactions = 4, 10 #min(2000, len(transactions))
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


def start_miner(config_path):
    config = configparser.ConfigParser()
    config.read(config_path)
    miner_cpu = MinerCPU(config)
    miner_cpu.start()

    def signal_handler(sig, frame):
        print("Señal recibida, cerrando el servidor...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main():
    config_path = 'config.ini'
    num_processes = 1

    processes = []

    for _ in range(num_processes):
        p = Process(target=start_miner, args=(config_path,))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()


if __name__ == '__main__':
    main()
