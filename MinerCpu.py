import configparser
import threading
import json
import hashlib
import random
import struct
import time
import pandas as pd
import requests
from BlockTemplate import BlockTemplate


class StratumProxy:
    def __init__(self, config):
        self.config = config
        self.stratum_processing = StratumProcessing(config)
        self.merkle_nonce_df = pd.read_csv('merkle_nonce.csv')  # Leer el archivo CSV

    def start(self):
        threading.Thread(target=self.update_block_template_periodically).start()  # Iniciar el hilo para actualizar el block template
        time.sleep(1)
        threading.Thread(target=self.cpu_miner).start()  # Iniciar el hilo de minería de CPU

    def update_block_template_periodically(self):
        while True:
            try:
                self.stratum_processing.update_block_template()
                # print("Block template actualizado")
            except Exception as e:
                print(f"Error actualizando block template: {e}")
            time.sleep(10)  # Esperar 1 segundo antes de actualizar nuevamente

    def cpu_miner(self):
            while True:
                # try:
                # Obtener un nuevo trabajo para minar
                job = self.stratum_processing.create_job_from_blocktemplate()

                if job:
                    merkle_root = job['merkle_branch'][-1]
                    ntime = job['ntime']
                    nbits = job['nbits']
                    prevhash = job['prevhash']
                    version = job['version']
                    coinbase1 = job['coinbase1']
                    coinbase2 = job['coinbase2']
                    ntime = f"{ntime:08x}"

                    matching_rows = self.merkle_nonce_df[self.merkle_nonce_df['merkle_root'].apply(
                        lambda x: merkle_root.startswith(x))]

                    if not matching_rows.empty:
                        print(f"Merkle root: {merkle_root}, Nonces encontrados: {len(matching_rows)}")
                        for _, row in matching_rows.iterrows():
                            nonce_init = int(row['nonce'].zfill(8), 16)
                            nonce_end = nonce_init + 65536
                            for nonce in range(nonce_init, nonce_end):
                                nonce_hex = f"{nonce:08x}"
                                block_header = self.stratum_processing.create_block_header_submit(
                                    version, prevhash, job['merkle_branch'], ntime, nbits, coinbase1, coinbase2, nonce_hex,
                                    "00000000"
                                )
                                # print(f"Nonce encontrado en el archivo: {nonce_hex}")
                                # print(f"Block header: {block_header}")

                                # Procesar el submit
                                self.stratum_processing.process_submit(block_header, nbits)  # Aquí se puede pasar un socket real si es necesario
                    # else:
                    #     pass
                        # print(f"No se encontró nonce para merkle root: {merkle_root}")
            # except Exception as e:
            #         print(f"Error en cpu_miner: {e}")
            #     time.sleep(10)  # Esperar un tiempo antes de intentar minar nuevamente


class StratumProcessing:
    def __init__(self, config):
        self.config = config
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
        self.suggested_difficulty = config.get('DIFFICULTY', 'initial')  # Inicializar la dificultad sugerida
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
        self.block_template = self.block_template_fetcher.fetch_template()
        if self.block_template:
            self.block_template['height'] += 1  # Incrementar el height
            self.nbits = self.block_template['bits']
            self.height = self.block_template['height']
            self.version = self.block_template['version']
            self.prevhash = self.block_template['previousblockhash']
            self.fee = self.calculate_coinbase_value()
            self.transactions_raw = self.block_template['transactions']

    def create_job_from_blocktemplate(self):
        try:
            version = self.version_to_hex(self.block_template['version'])
            prev_block = self.block_template['previousblockhash']
            extranonce_placeholder = "00000000"
            miner_message = self.miner_message()
            coinbase1, coinbase2 = self.tx_make_coinbase_stratum(miner_message, extranonce_placeholder)
            coinbase_tx = coinbase1 + coinbase2
            coinbase_tx_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(coinbase_tx)).digest()).digest().hex()
            self.transactions = self.select_random_transactions()
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
            # job_id = random.randint(0, 0xFFFFFFFF)
            # clean_jobs = self.first_job or (self.height != self.block_template[
            #     'height'])  # Establecer clean_jobs como True la primera vez o si el height es diferente

            job = {
                'version': version,
                'prevhash': prev_block,
                'coinbase1': coinbase1,
                'coinbase2': coinbase2,
                'merkle_branch': merkle_branch,
                'nbits': nbits,
                'ntime': timestamp,
            }

            self.first_job = False  # Cambiar a False después del primer trabajo

            # self.jobs[job_id] = job  # Almacenar el trabajo en el diccionario

            return job
        except Exception as e:
            print(f"Error creating job from blocktemplate: {e}")
            return None

    def select_random_transactions(self):
        """Selecciona transacciones hasta que el tamaño total esté por debajo del límite máximo."""

        # Límite de tamaño en bytes
        max_size_limit = random.randint(200, 1024) * 1024  # 280 KB

        transactions = self.block_template['transactions'].copy()
        random.shuffle(transactions)

        # Transacciones seleccionadas
        selected_transactions = []
        # Tamaño total de las transacciones seleccionadas
        total_size = 0

        for transaction in transactions:
            transaction_size = 0
            if transaction:

                if transaction['weight']:
                    transaction_size = transaction['weight']

                    projected_size = total_size + transaction_size
                    # Agrega la transacción si el tamaño proyectado está dentro del límite
                    if projected_size <= max_size_limit:
                        selected_transactions.append(transaction)
                        total_size += transaction_size
                    # Si excede el tamaño máximo, dejamos de agregar transacciones
                    else:
                        break

        return selected_transactions

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

    def tx_make_coinbase_stratum(self, miner_message, extranonce_placeholder="00000000", op_return_data=""):
        pubkey_script = "76a914" + self.bitcoinaddress2hash160(self.address) + "88ac"
        block_height = self.height
        coinbase_script = self.create_coinbase_script(block_height, miner_message, extranonce_placeholder)

        # Ajustar el coinbase_script para que siempre sea correcto
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

        coinbase2 = (
                "ffffffff"  # Secuencia (4 bytes de f's)
                + "01"  # Número de salidas
                + self.int2lehex(self.block_template['coinbasevalue'], 8)  # Recompensa en little-endian (8 bytes)
                + self.int2varinthex(len(pubkey_script) // 2)  # Longitud del pubkey_script
                + pubkey_script  # Script público
                + "00000000"  # Bloque de tiempo de lock (4 bytes de ceros)
        )

        return coinbase1, coinbase2

    def calculate_coinbase_value(self):
        base_reward = self.block_template['coinbasevalue']
        total_fees = sum(tx['fee'] for tx in self.block_template['transactions'])
        coinbase_value = base_reward + total_fees
        self.fee = coinbase_value
        return self.fee

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
        longitud_frase = 100  # random.randint(40, 200)
        caracteres = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890!@#$%^&*()_+-=[]{}|;:,.<>?`~"
        frase = ''.join(random.choice(caracteres) for _ in range(longitud_frase))
        return frase

    def process_submit(self, block_header, nbits):

        # Validar el bloque
        block_hash = hashlib.sha256(hashlib.sha256(bytes.fromhex(block_header)).digest()).digest().hex()
        target = int(self.bits_to_target(nbits), 16)


        if int(block_hash, 16) < target:
            print("block hash: {}", block_hash)
            # Conectar al servidor de Bitcoin Core por RPC y enviar el bloque
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
            print(f'Respuesta enviada (RPC): {json.dumps(response, indent=4)}')


def main():
    config = configparser.ConfigParser()
    config.read('config.ini')

    proxy = StratumProxy(config)
    proxy.start()


if __name__ == '__main__':
    main()
