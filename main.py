import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Description headings used in PubChem's Annotation service
# We search these specific categories to find "original" curated content
SOURCE_HEADINGS = [
    ("DrugBank Description", "DrugBank"),
    ("NCI Thesaurus Description", "NCI Thesaurus (NCIt)"),
    ("MeSH Description", "Medical Subject Headings (MeSH)"),
    ("CAMEO Chemicals Description", "CAMEO Chemicals")
]

def is_bad_name(name, iupac=""):
    """Strictly filters out DB IDs, technical codes, and technical IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # 1. Database ID Prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # 2. Filter CAS numbers or numeric codes (e.g. 100-22-1)
    if re.match(r'^[0-9\-]{5,}$', name): return True

    # 3. Technical IUPAC heuristic (many dashes/brackets/numbers)
    # Real common names (Caffeine, Aspirin) rarely have more than 1 dash
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 6: return True

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
    """Fetches lists of CIDs from the targeted annotation categories."""
    print("Building molecule pool from curated annotations...")
    pool = []
    # We try each heading to gather a large variety of molecules
    for heading, _ in SOURCE_HEADINGS:
        try:
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/{heading.replace(' ', '%20')}/JSON"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                anns = r.json().get('Annotations', {}).get('Annotation', [])
                for ann in anns:
                    cids = ann.get('LinkedRecords', {}).get('CID', [])
                    pool.extend(cids)
        except: continue
    return list(set(pool))

def get_molecule_details(cid):
    """Deep-crawls the PUG VIEW for the exact text and filtered names."""
    try:
        # 1. Fetch IUPAC and Synonyms (PUG REST)
        p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
        s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
        
        iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
        syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
        
        # Filter for clean names
        clean_names = [s for s in syns if not is_bad_name(s, iupac)]
        if not clean_names: return None

        # 2. Fetch Description (PUG VIEW)
        v_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        v_res = requests.get(v_url, timeout=10).json()
        
        found_descriptions = {}
        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        text = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            for _, source_label in SOURCE_HEADINGS:
                                # Match internal reference string to our priority sources
                                if source_label.split('(')[0].strip().lower() in ref.lower():
                                    if len(text.split()) <= 150:
                                        if source_label not in found_descriptions:
                                            found_descriptions[source_label] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(v_res)
        
        # Select description based on priority
        final_desc = None
        for _, source_label in SOURCE_HEADINGS:
            if source_label in found_descriptions:
                final_desc = found_descriptions[source_label]
                break
        
        if not final_desc: return None

        return {
            "name": clean_names[0],
            "aka": clean_names[1:4], # First up to 3 clean synonyms
            "iupac": iupac,
            "description": final_desc,
            "cid": cid
        }
    except: return None

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape Markdown characters
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
        save_cid(data['cid'])
        print(f"Successfully posted: {data['name']}")
    else:
        # Fallback for complex names that break Markdown
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    used = get_used_cids()
    pool = get_molecule_pool()
    random.shuffle(pool)
    
    print(f"Pool size: {len(pool)}. Searching for valid candidate...")
    for cid in pool:
        if str(cid) in used: continue
        data = get_molecule_details(cid)
        if data:
            post_to_telegram(data)
            sys.exit(0)
    
    print("No molecule found.")
    sys.exit(1)
