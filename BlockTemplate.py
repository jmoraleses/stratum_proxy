import time
import requests
import json

class BlockTemplate:
    def __init__(self, config):
        self.api_url = config.get('API', 'url')
        self.template = None
        self.submission_response = None

        print(f"Initialized BlockTemplate with API URL: {self.api_url}")

    def fetch_template(self, retries=5, backoff_factor=0.5):
        for attempt in range(retries):
            try:
                response = requests.get(f"{self.api_url}/getblocktemplate")
                response.raise_for_status()  # Raise HTTPError for bad responses
                self.template = response.json()
                return self.template
            except requests.exceptions.RequestException as e:
                print(f"Error fetching block template (attempt {attempt + 1}/{retries}): {e}")
                time.sleep(backoff_factor * (2 ** attempt))  # Exponential backoff

        self.template = None
        return None

    def get_template(self):
        return self.template

    def submit_block(self, block):
        payload = {'block': block}
        try:
            response = requests.post(f"{self.api_url}/submitblock", json=payload)
            response.raise_for_status()  # Raise HTTPError for bad responses
            self.submission_response = response.json()
            return self.submission_response
        except requests.exceptions.RequestException as e:
            print(f"Error submitting block: {e}")
            self.submission_response = None
            return None
