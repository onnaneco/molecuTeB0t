import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Description Priority Sources
PRIORITY_SOURCES = [
    {"name": "DrugBank", "label": "DrugBank"},
    {"name": "NCI Thesaurus", "label": "NCI Thesaurus (NCIt)"},
    {"name": "Medical Subject Headings", "label": "Medical Subject Headings (MeSH)"},
    {"name": "CAMEO Chemicals", "label": "CAMEO Chemicals"}
]

def is_bad_name(name, iupac=""):
    """Returns True if the name is a database ID or technical code."""
    if not name or len(name) < 3: return True
    # If the name is exactly the IUPAC name, we don't want it in 'Name' or 'AKA'
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # CAS or numeric codes
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True # Too many technical chars
    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_molecule_pool():
    """Fetches a large pool of CIDs from high-quality sources."""
    pool = []
    print("Building high-quality molecule pool...")
    # We fetch from DrugBank and NCI Thesaurus first as they have the best descriptions
    for src in ["DrugBank", "NCI Thesaurus", "Medical Subject Headings (MeSH)"]:
        try:
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/{src}/cids/JSON"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                cids = r.json().get('IdentifierList', {}).get('CID', [])
                pool.extend(cids)
        except: continue
    return list(set(pool))

def get_description_logic(cid):
    """Deep-crawls the PUG VIEW for the specific prioritized descriptions."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200: return None
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
                            for src in PRIORITY_SOURCES:
                                # Match source name in reference string
                                if src['name'].lower() in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 150: # User limit: 150 words
                                        if src['label'] not in found: found[src['label']] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(data)
        # Apply strict priority
        for src in PRIORITY_SOURCES:
            if src['label'] in found: return found[src['label']]
    except: pass
    return None

def get_molecule_data():
    used = get_used_cids()
    pool = get_molecule_pool()
    
    if not pool:
        print("Fallback: Using random search in low-CID range...")
        pool = list(range(1, 1000000))

    random.shuffle(pool)
    
    for cid in pool:
        if str(cid) in used: continue
        
        try:
            # 1. Get IUPAC and Synonyms
            p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
            s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
            
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not syns: continue

            # 2. Filter for Names (Must not be technical/bad)
            clean_names = [s for s in syns if not is_bad_name(s, iupac)]
            
            # If no common name exists, skip. This prevents SCHEMBL-only molecules.
            if not clean_names: continue

            # 3. Get Description based on priority
            desc = get_description_logic(cid)
            if not desc: continue

            return {
                "name": clean_names[0],
                "aka": clean_names[1:4], # Up to 3
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except: continue

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape characters that break Markdown
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`").replace("(", "\\(").replace(")", "\\)")

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
        print(f"Successfully posted: {data['name']}")
    else:
        # Fallback if markdown still fails
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    mol = get_molecule_data()
    if mol:
        post_to_telegram(mol)
