import requests
import json


class BlockTemplate:
    def __init__(self, config):
        rpc_host = config.get('RPC', 'host')
        rpc_port = config.get('RPC', 'port')
        self.rpc_url = f"http://{rpc_host}:{rpc_port}"
        self.rpc_user = config.get('RPC', 'user')
        self.rpc_password = config.get('RPC', 'password')
        self.template = None
        self.submission_response = None

        print(f"Initialized BlockTemplateFetcher with URL: {self.rpc_url}")

    def fetch_template(self):
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

        try:
            response = requests.post(self.rpc_url, data=payload, headers=headers,
                                     auth=(self.rpc_user, self.rpc_password))
            response.raise_for_status()  # Raise HTTPError for bad responses
            self.template = response.json().get('result')
            return self.template
        except requests.exceptions.RequestException as e:
            print(f"Error fetching block template: {e}")
            self.template = None
            return None

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
                                     auth=(self.rpc_user, self.rpc_password))
            response.raise_for_status()  # Raise HTTPError for bad responses
            self.submission_response = response.json()
            return self.submission_response
        except requests.exceptions.RequestException as e:
            print(f"Error submitting block: {e}")
            self.submission_response = None
            return None
