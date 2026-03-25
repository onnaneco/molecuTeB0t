import requests
import random
import sys
import os
import time
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
MAX_CID = 1000000 # Higher density of described molecules in the first 1M

def is_bad_name(name):
    """Filters out database IDs, CAS numbers, and long IUPAC names."""
    if not name: return True
    name_u = name.upper()
    
    # 1. Database ID Prefixes
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC"]
    if any(name_u.startswith(p) for p in bad_prefixes):
        return True
    
    # 2. CAS numbers or codes (e.g. 123-45-6)
    if re.match(r'^[0-9\-]{5,}$', name):
        return True

    # 3. IUPAC Heuristic: If it has too many numbers/brackets/dashes, it's not a 'common' name
    # e.g. 1-[(2R,3S,4R,5R)-3,4-dihydroxy-5-(hydroxymethyl)oxolan-2-yl]...
    if len(re.findall(r'[\[\]\(\)\-\,\d]', name)) > 10:
        return True

    return False

def get_description_and_source(cid):
    """
    Deep-crawls the PUG VIEW JSON for specific sources.
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200: return None, None
        data = response.json()
        
        # Priority order
        target_sources = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]
        found_map = {}

        # Recursive function to find all 'Information' blocks regardless of section name
        def crawl(node):
            if isinstance(node, list):
                for item in node: crawl(item)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        # Check if this info block has a string value
                        val_list = info.get('Value', {}).get('StringWithMarkup', [])
                        if val_list:
                            text = val_list[0].get('String')
                            # Look for the source in the Reference field
                            ref = info.get('Reference', '')
                            # Sometimes source is in the 'Name' of the reference
                            for source in target_sources:
                                if source.lower() in ref.lower():
                                    # Check word count
                                    words = text.split()
                                    if 5 < len(words) <= 150:
                                        if source not in found_map:
                                            found_map[source] = text
                
                # Continue crawling sub-sections
                for key in ['Section', 'Record', 'Information']:
                    if key in node: crawl(node[key])

        crawl(data)

        # Return the best match based on your priority list
        for s in target_sources:
            if s in found_map:
                return found_map[s], s
                
        return None, None
    except Exception as e:
        print(f"      Log: Error parsing PUG VIEW: {e}")
        return None, None

def get_molecule_data():
    print("Starting search...")
    attempts = 0
    while True:
        attempts += 1
        cid = random.randint(1, MAX_CID)
        
        if attempts % 10 == 0:
            print(f"  Attempt {attempts}...")

        try:
            # 1. Get Synonyms and IUPAC
            syn_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
            prop_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
            
            iupac = prop_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            all_syns = syn_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])

            if not iupac or not all_syns: continue

            # 2. Filter Names
            # Get only "good" names that aren't IUPAC
            valid_names = [s for s in all_syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            
            if not valid_names: continue

            primary_name = valid_names[0]
            aka = valid_names[1:4] # Up to 3

            # 3. Get Description (The hard part)
            desc, source = get_description_and_source(cid)
            
            if not desc:
                continue

            print(f"  Success on CID {cid} (Source: {source})")
            return {
                "name": primary_name,
                "aka": aka,
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }

        except:
            continue

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape Markdown V1
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

    caption = (
        f"*Name:* {clean(data['name'])}\n"
        f"*A.K.A.:* {clean(aka_text)}\n"
        f"*IUPAC Name:* {clean(data['iupac'])}\n\n"
        f"*Description:* {clean(data['description'])}\n\n"
        f"*PubChem Link:* https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}"
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "photo": data['image'],
        "caption": caption,
        "parse_mode": "Markdown"
    }
    
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code != 200:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
