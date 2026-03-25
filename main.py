import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Priority Sources (Order matters for selection)
SOURCES = [
    {"name": "DrugBank", "label": "DrugBank"},
    {"name": "NCI Thesaurus", "label": "NCI Thesaurus (NCIt)"},
    {"name": "Medical Subject Headings", "label": "Medical Subject Headings (MeSH)"},
    {"name": "CAMEO Chemicals", "label": "CAMEO Chemicals"}
]

def is_bad_name(name, iupac=""):
    """Filters out database IDs, technical codes, and IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # Comprehensive DB ID prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    if re.match(r'^[0-9\-]{5,}$', name): return True # CAS check
    
    # Heuristic: IDs often have numbers mixed with 5+ uppercase letters
    if u == name and any(c.isdigit() for c in name) and len(name) > 6: return True
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True # Too technical
    
    return False

def get_posted_cids():
    if not os.path.exists(HISTORY_FILE): return set()
    with open(HISTORY_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_posted_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{cid}\n")

def get_description(cid):
    """Crawl PUG VIEW for the original description text by source priority."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=10)
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
                            for s in SOURCES:
                                if s['name'].lower() in ref.lower():
                                    if len(text.split()) <= 150:
                                        if s['label'] not in found: found[s['label']] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(data)
        for s in SOURCES:
            if s['label'] in found: return found[s['label']], s['label']
    except: pass
    return None, None

def get_molecule_data():
    posted = get_posted_cids()
    
    # Step 1: Build a pool of CIDs from our high-quality sources
    print("Building molecule pool from sources...")
    pool = []
    for s in SOURCES:
        try:
            # Fetch all CIDs for a specific source (Fast PUG REST)
            r = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/{s['name'].replace(' ', '%20')}/cids/JSON", timeout=10).json()
            ids = r.get('IdentifierList', {}).get('CID', [])
            pool.extend(ids)
        except: continue
    
    if not pool:
        # Fallback if source API is down: check low-range CIDs (1-10,000)
        pool = list(range(1, 10000))

    random.shuffle(pool)
    print(f"Pool size: {len(pool)}. Searching for candidate...")

    # Step 2: Linear search through the pre-qualified pool
    for cid in pool:
        if str(cid) in posted: continue
        
        try:
            # Get IUPAC and Synonyms
            p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
            s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
            
            iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
            syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not syns: continue
            
            # Filter clean names
            clean_names = [n for n in syns if not is_bad_name(n, iupac)]
            if not clean_names: continue

            # Get Description
            desc, source_label = get_description(cid)
            if not desc: continue

            return {
                "name": clean_names[0],
                "aka": clean_names[1:4],
                "iupac": iupac,
                "description": desc,
                "cid": cid
            }
        except: continue

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    def clean(t):
        return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`").replace("(", "\\(").replace(")", "\\)")

    caption = (
        f"*Name:* {clean(data['name'])}\n"
        f"*A.K.A.:* {clean(aka_text)}\n"
        f"*IUPAC Name:* {clean(data['iupac'])}\n\n"
        f"*Description:* {clean(data['description'])}\n\n"
        f"*PubChem Link:* https://pubchem.ncbi.nlm.nih.gov/compound/{data['cid']}"
    )

    image_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{data['cid']}/PNG"
    payload = {"chat_id": CHANNEL_ID, "photo": image_url, "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
    
    if r.status_code == 200:
        save_posted_cid(data['cid'])
        print(f"Posted: {data['name']}")
    else:
        # Fallback for complex chemical notation breaking Markdown
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_posted_cid(data['cid'])

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
