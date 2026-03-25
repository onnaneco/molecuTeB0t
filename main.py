import requests
import random
import sys
import os
import re
import time

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Priority sources for descriptions
SOURCES = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name):
    """Filters out database IDs and purely numeric codes."""
    if not name: return True
    name_u = name.upper()
    
    # Block specific database prefixes
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-"]
    if any(name_u.startswith(p) for p in bad_prefixes): return True
    
    # Block if the name is just numbers and dashes (e.g., 50-00-0)
    if re.match(r'^[0-9\-]+$', name): return True

    # Block if name looks like a long technical IUPAC string (heuristic)
    if len(re.findall(r'[0-9\-]{2,}', name)) > 4 and len(name) > 30: return True
    
    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f)
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{cid}\n")

def get_description(cid):
    """Deep-crawls the PUG VIEW JSON for priority descriptions."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        res = requests.get(url, timeout=12)
        if res.status_code != 200: return None, None
        data = res.json()
        found = {}

        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                # Look for Information blocks
                if 'Information' in node:
                    for info in node['Information']:
                        val = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if val and ref:
                            for s in SOURCES:
                                if s.lower() in ref.lower():
                                    words = val.split()
                                    if 10 < len(words) <= 200:
                                        if s not in found: found[s] = val
                # Recurse into sub-sections
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(data)
        for s in SOURCES:
            if s in found: return found[s], s
    except Exception: pass
    return None, None

def get_molecule_data():
    used_cids = get_used_cids()
    print("Fetching CID pool from DrugBank...")
    
    try:
        # Get all CIDs that have DrugBank information
        res = requests.get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/DrugBank/cids/JSON", timeout=15)
        pool = res.json().get('IdentifierList', {}).get('CID', [])
    except:
        print("Could not fetch pool, using fallback range...")
        pool = list(range(1, 50000))
    
    random.shuffle(pool)
    print(f"Pool size: {len(pool)}. Searching for valid candidate...")

    # Check up to 1000 molecules to find a winner
    for i in range(min(1000, len(pool))):
        cid = pool[i]
        cid_str = str(cid)
        
        if cid_str in used_cids: continue

        try:
            # 1. Fetch Synonyms and IUPAC
            s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
            p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
            
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            
            if not syns or not iupac: continue

            # Filter for common names
            clean_names = [s for s in syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            if not clean_names: continue

            # 2. Check for Description (High-quality sources only)
            desc, source = get_description(cid)
            if not desc: 
                continue

            # If we reach here, we found a winner
            return {
                "name": clean_names[0],
                "aka": clean_names[1:4],
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except:
            continue
            
    print("Checked 1000 molecules and found no match. Try running again.")
    sys.exit(1)

def post_to_telegram(data):
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Helper to stop Markdown from breaking on chemical names
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
    if r.status_code == 200:
        save_cid(data['cid'])
        print(f"Successfully posted {data['name']} (CID: {data['cid']})")
    else:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
