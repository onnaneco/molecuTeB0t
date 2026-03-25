import requests
import random
import sys
import os

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"  # Include the '@'
MAX_CID = 150000000  # PubChem has ~119 million compounds as of 2025

def is_bad_name(name):
    """Checks if a name is likely a database ID rather than a chemical name."""
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "CAS-", "MFCD", 
        "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE"
    ]
    name_upper = name.upper()
    return any(name_upper.startswith(p) for p in bad_prefixes)

def get_random_molecule():
    print("Searching PubChem for a molecule with a good name...")
    for _ in range(20): 
        cid = random.randint(1, MAX_CID)
        name_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
        image_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
        
        try:
            response = requests.get(name_url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                all_syns = data['InformationList']['Information'][0].get('Synonym', [])
                
                if not all_syns:
                    continue

                # 1. Try to find the first "good" name for the primary slot
                primary_name = None
                for s in all_syns:
                    if not is_bad_name(s):
                        primary_name = s
                        break
                
                # If everything looks like a database ID, just use the first one
                if not primary_name:
                    primary_name = all_syns[0]

                # 2. Get up to 3 different synonyms for the "a.k.a." section
                other_syns = [s for s in all_syns if s != primary_name]
                aka_list = other_syns[:3]
                
                return primary_name, aka_list, image_url, cid
                
        except Exception as e:
            print(f"Error: {e}")
    
    print("Failed to find a suitable molecule.")
    sys.exit(1)

def post_to_telegram(name, aka_list, image_url, cid):
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN not found!")
        sys.exit(1)

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    
    # Building the caption
    caption = f"*Name:* {name}\n"
    
    if aka_list:
        # Join synonyms with a newline or comma
        aka_text = ", ".join(aka_list)
        caption += f"*a.k.a:* {aka_text}\n"
    
    caption += f"\n*PubChem CID:* [{cid}] (https://pubchem.ncbi.nlm.nih.gov/compound/{cid})"
    
    payload = {
        "chat_id": CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "Markdown"
    }
    
    response = requests.post(api_url, data=payload)
    if response.status_code == 200:
        print(f"Posted: {name}")
    else:
        print(f"Failed: {response.text}")
        sys.exit(1)

if __name__ == "__main__":
    print("Finding a random molecule...")
    name, aka, img, cid = get_random_molecule()
    print(f"Found: {name} (CID: {cid})")
    post_to_telegram(name, aka, img, cid)
