import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Priority sources
SOURCES = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name):
    """Filters out database IDs and technical IUPAC names."""
    if not name: return True
    name_u = name.upper()
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-"]
    if any(name_u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]+$', name): return True
    # If name is very long and has many numbers/dashes, it's IUPAC
    if len(re.findall(r'[0-9\-]', name)) > 6 and len(name) > 20: return True
    return False

def get_used_cids():
    """Reads the history file from the repo."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f)
    return set()

def save_cid(cid):
    """Appends the used CID to the history file."""
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{cid}\n")

def get_description(cid):
    """Finds description by priority sources."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        data = requests.get(url, timeout=10).json()
        found = {}

        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        text = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            for s in SOURCES:
                                if s.lower() in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 200: # 200 word limit
                                        if s not in found: found[s] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(data)
        for s in SOURCES:
            if s in found: return found[s], s
    except: pass
    return None, None

def get_molecule_data():
    used_cids = get_used_cids()
    print("Fetching CID pool from DrugBank...")
    
    # Get all CIDs from DrugBank (High probability of quality descriptions)
    try:
        res = requests.get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/DrugBank/cids/JSON", timeout=15)
        pool = res.json().get('IdentifierList', {}).get('CID', [])
    except:
        pool = list(range(1, 100000)) # Fallback
    
    random.shuffle(pool)
    
    count = 0
    for cid in pool:
        cid_str = str(cid)
        if cid_str in used_cids: continue
        
        count += 1
        if count > 100: break # Safety break

        try:
            # 1. Quick check for names and IUPAC
            s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
            p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
            
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            
            if not syns or not iupac: continue

            # Filter clean names
            clean_names = [s for s in syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            if not clean_names: continue

            # 2. Check for Description (The slow part, only do if names are good)
            desc, source = get_description(cid)
            if not desc: continue

            return {
                "name": clean_names[0],
                "aka": clean_names[1:4],
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except: continue
    
    print("No molecule found in this batch.")
    sys.exit(1)

def post_to_telegram(data):
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    def clean(t): return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[")

    caption = (
        f"*Name:* {clean(data['name'])}\n"
        f"*A.K.A.:* {clean(aka_text)}\n"
        f"*IUPAC Name:* {clean(data['iupac'])}\n\n"
        f"*Description:* {clean(data['description'])}\n\n"
        f"*PubChem Link:* https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}"
    )

    payload = {"chat_id": CHANNEL_ID, "photo": data['image'], "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    if r.status_code == 200:
        save_cid(data['cid'])
        print(f"Posted CID {data['cid']}")
    else:
        print(f"Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
