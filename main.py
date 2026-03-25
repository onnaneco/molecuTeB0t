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

# Priority of headings to search for in PubChem's Annotation service
PRIORITY_HEADINGS = [
    "DrugBank Description",
    "NCI Thesaurus Description",
    "MeSH Description",
    "CAMEO Chemicals Description"
]

def is_bad_name(name):
    """Filters out database IDs and technical codes."""
    if not name or len(name) < 3: return True
    name_u = name.upper()
    
    # Extensive list of DB prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "ST0", "AA-", "BRN ", "CHEBI:"
    ]
    if any(name_u.startswith(p) for p in bad_prefixes): return True
    
    # Filter numeric codes/CAS-like numbers
    if re.match(r'^[0-9\-]{5,}$', name): return True
    
    # Filter long technical IUPAC names (heuristic: many dashes/numbers/brackets)
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 7: return True
    
    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f)
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def fetch_cids_by_annotation(heading):
    """Gets a list of all CIDs that have a specific annotation heading."""
    print(f"Fetching CIDs for: {heading}...")
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/annotations/heading/{heading}/JSON"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            annotations = data.get('Annotations', {}).get('Annotation', [])
            cids = []
            for ann in annotations:
                cid_list = ann.get('LinkedRecords', {}).get('CID', [])
                cids.extend(cid_list)
            return list(set(cids))
    except:
        pass
    return []

def get_detailed_description(cid):
    """Extracts description text and source from PUG VIEW."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200: return None, None
        data = r.json()
        
        found = {} # Map source name to text
        
        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        text = info.get('Value', {}).get('StringWithMarkup', [{}])[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            # Map the internal reference to our target labels
                            sources = {
                                "DrugBank": "DrugBank",
                                "NCI Thesaurus": "NCI Thesaurus (NCIt)",
                                "Medical Subject Headings": "Medical Subject Headings (MeSH)",
                                "CAMEO Chemicals": "CAMEO Chemicals"
                            }
                            for key, label in sources.items():
                                if key.lower() in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 200:
                                        if label not in found: found[label] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(data)
        # Return based on strict priority
        for label in ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]:
            if label in found: return found[label], label
    except: pass
    return None, None

def get_molecule_data():
    used_cids = get_used_cids()
    
    # Try each heading in order of your priority
    for heading in PRIORITY_HEADINGS:
        all_cids = fetch_cids_by_annotation(heading)
        random.shuffle(all_cids)
        
        # Check first 50 unused ones from this heading
        checked = 0
        for cid in all_cids:
            if str(cid) in used_cids: continue
            checked += 1
            if checked > 50: break 
            
            try:
                # 1. Names and IUPAC
                s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
                p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
                
                syns = requests.get(s_url, timeout=5).json().get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
                iupac = requests.get(p_url, timeout=5).json().get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
                
                if not syns or not iupac: continue
                
                clean_names = [s for s in syns if not is_bad_name(s) and s.lower() != iupac.lower()]
                if not clean_names: continue
                
                # 2. Description
                desc, source = get_detailed_description(cid)
                if not desc: continue
                
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
                
    print("CRITICAL: No molecules found after searching all headings.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    def clean(t): return str(t).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

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
        print(f"Successfully posted CID {data['cid']}")
    else:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
