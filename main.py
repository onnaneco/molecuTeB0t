import requests
import random
import sys
import os

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"  # Include the '@'
MAX_CID = 150000000  # PubChem has ~119 million compounds as of 2025

def get_random_molecule():
    """Fetches a random valid molecule name and image URL from PubChem."""
    while True:
        cid = random.randint(1, MAX_CID)
        
        # 1. Try to get synonyms (to find a name)
        name_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
        # 2. Structure Image URL
        image_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
        
        try:
            response = requests.get(name_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # Get the first synonym (usually the most common name)
                name = data['InformationList']['Information'][0]['Synonym'][0]
                return name, image_url, cid
            else:
                print(f"CID {cid} doesn't exist, try another one")
                continue
        except Exception as e:
            print(f"Error fetching CID {cid}: {e}")
            sys.exit(1)

def post_to_telegram(name, image_url, cid):
    # Safety check:
    if not TELEGRAM_TOKEN:
        raise ValueError("No TELEGRAM_TOKEN found! Did you set it in GitHub Secrets?")
        sys.exit(1)

    
    """Posts the molecule to the Telegram channel."""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    
    caption = (
        f"Name: {name}\n"
        f"PubChem CID: [{cid}]"
    )
    
    payload = {
        "chat_id": CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "Markdown"
    }
    
    response = requests.post(api_url, data=payload)
    if response.status_code == 200:
        print(f"Successfully posted: {name}")
    else:
        print(f"Failed to post: {response.status_code} - {response.text}")
        sys.exit(1)

if __name__ == "__main__":
    print("Finding a random molecule...")
    name, img, cid = get_random_molecule()
    print(f"Found: {name} (CID: {cid})")
    post_to_telegram(name, img, cid)
