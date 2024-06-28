import time
import threading
import requests
import json
from requests.auth import HTTPBasicAuth
from flask import Flask, jsonify, request

class BlockTemplate:
    def __init__(self, config):
        rpc_host = config['RPC']['host']
        rpc_port = config['RPC']['port']
        self.rpc_url = f"http://{rpc_host}:{rpc_port}"
        self.rpc_user = config['RPC']['user']
        self.rpc_password = config['RPC']['password']
        self.template = None
        self.submission_response = None

        print(f"Initialized BlockTemplate with URL: {self.rpc_url}")

    def fetch_template(self, retries=5, backoff_factor=0.5):
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "curltest",
            "method": "getblocktemplate",
            "params": [{"rules": ["segwit"]}]
        })
        headers = {
            'content-type': "application/json",
            'cache-control': "no-cache",
        }

        for attempt in range(retries):
            try:
                response = requests.post(self.rpc_url, data=payload, headers=headers,
                                         auth=HTTPBasicAuth(self.rpc_user, self.rpc_password))
                response.raise_for_status()  # Raise HTTPError for bad responses
                self.template = response.json().get('result')
                return self.template
            except requests.exceptions.RequestException as e:
                print(f"Error fetching block template (attempt {attempt + 1}/{retries}): {e}")
                time.sleep(backoff_factor * (2 ** attempt))  # Exponential backoff

        self.template = None
        return None

    def get_template(self):
        return self.template

    def submit_block(self, block):
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "curltest",
            "method": "submitblock",
            "params": [block]
        })
        headers = {
            'content-type': "application/json",
            'cache-control': "no-cache",
        }

        try:
            response = requests.post(self.rpc_url, data=payload, headers=headers,
                                     auth=HTTPBasicAuth(self.rpc_user, self.rpc_password))
            response.raise_for_status()  # Raise HTTPError for bad responses
            self.submission_response = response.json()
            return self.submission_response
        except requests.exceptions.RequestException as e:
            print(f"Error submitting block: {e}")
            self.submission_response = None
            return None

# Configuración de Flask
app = Flask(__name__)

# Configuración de RPC para Bitcoin Core
config = {
    'RPC': {
        'host': '127.0.0.1',
        'port': '18332',
        'user': 'userbit',
        'password': 'passbit'
    }
}

block_template = BlockTemplate(config)

# Función para actualizar el block template cada 10 segundos
def update_template_periodically():
    while True:
        block_template.fetch_template()
        time.sleep(10)

# Iniciar el thread para actualizar el block template
threading.Thread(target=update_template_periodically, daemon=True).start()

@app.route('/getblocktemplate', methods=['GET'])
def get_block_template():
    template = block_template.get_template()
    if template:
        return jsonify(template)
    else:
        return jsonify({'error': 'No block template available'}), 500

@app.route('/submitblock', methods=['POST'])
def submit_block():
    block_data = request.json.get('block')
    if not block_data:
        return jsonify({'error': 'No block data provided'}), 400
    response = block_template.submit_block(block_data)
    if response:
        return jsonify(response)
    else:
        return jsonify({'error': 'Unable to submit block'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005)
