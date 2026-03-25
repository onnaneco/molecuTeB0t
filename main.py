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

# Description Source Priority
SOURCES_PRIORITY = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name):
    """Detects database IDs, technical codes, or long IUPAC strings."""
    if not name or len(name) < 3: return True
    u = name.upper()
    # Expanded DB prefixes
    bad = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-", "MFCD", "HMS", "ST0", "AA-", "BRN ", "CHEBI:", "CID"]
    if any(u.startswith(p) for p in bad): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True
    # Technical IUPAC heuristic: many numbers/dashes/brackets
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True
    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_cid_pool():
    """Fetches a list of CIDs that likely have descriptions."""
    # We use the 'Record Description' annotation which is the most reliable way to find described molecules
    url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/Record%20Description/JSON"
    try:
        print("Accessing PubChem Record Description index...")
        r = requests.get(url, timeout=25)
        if r.status_code == 200:
            data = r.json()
            annotations = data.get('Annotations', {}).get('Annotation', [])
            cids = []
            for ann in annotations:
                cid_list = ann.get('LinkedRecords', {}).get('CID', [])
                cids.extend(cid_list)
            return list(set(cids))
    except Exception as e:
        print(f"Index fetch failed: {e}")
    
    # Fallback to general high-density range if index is down
    return list(range(1, 100000))

def extract_description(cid):
    """Crawl PUG VIEW for priority-based descriptions."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200: return None, None
        data = r.json()
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
                            # Map Reference text to our priority sources
                            for s in SOURCES_PRIORITY:
                                # Clean search for source name in reference
                                if s.split('(')[0].strip().lower() in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 200:
                                        if s not in found: found[s] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])

        crawl(data)
        for s in SOURCES_PRIORITY:
            if s in found: return found[s], s
    except: pass
    return None, None

def get_molecule_data():
    used = get_used_cids()
    pool = get_cid_pool()
    random.shuffle(pool)
    
    print(f"Pool size: {len(pool)}. Searching for suitable molecule...")
    
    checked = 0
    # Increase check limit to ensure we find one
    for cid in pool:
        if str(cid) in used: continue
        checked += 1
        if checked > 500: break # Check up to 500 candidates
        
        try:
            # 1. Get properties
            p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_data = requests.get(p_url, timeout=5).json()
            s_data = requests.get(s_url, timeout=5).json()
            
            iupac = p_data.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            syns = s_data.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not iupac or not syns: continue
            
            # Filter names
            clean_names = [s for s in syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            if not clean_names: continue
            
            # 2. Extract description (Following source priority)
            desc, source_name = extract_description(cid)
            if not desc: continue
            
            return {
                "name": clean_names[0],
                "aka": clean_names[1:4],
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "source": source_name,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except: continue
        
    print("Failure: Could not find a valid molecule in this batch.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    def clean(t): 
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

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
        print(f"Successfully posted {data['name']} (CID: {data['cid']}) from {data['source']}")
    else:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
