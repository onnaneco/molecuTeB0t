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

# Your prioritized sources
SOURCES = [
    {"name": "DrugBank", "label": "DrugBank"},
    {"name": "NCI Thesaurus", "label": "NCI Thesaurus (NCIt)"},
    {"name": "Medical Subject Headings (MeSH)", "label": "Medical Subject Headings (MeSH)"},
    {"name": "CAMEO Chemicals", "label": "CAMEO Chemicals"}
]

def is_bad_name(name, iupac):
    """Filters out database IDs, technical codes, and IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # Database ID prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # Filter CAS numbers or numeric codes (e.g. 100-22-1)
    if re.match(r'^[0-9\-]{5,}$', name): return True

    # Filter technical IUPAC names (heuristic: too many numbers/brackets/dashes)
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
    """Fetches CIDs directly from the Source index (More stable than Annotations)."""
    pool = []
    print("Building molecule pool from stable sources...")
    for source in SOURCES:
        try:
            # This API lists every CID provided by the source
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/{source['name'].replace(' ', '%20')}/cids/JSON"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                cids = r.json().get('IdentifierList', {}).get('CID', [])
                print(f"  - Found {len(cids)} molecules from {source['name']}")
                pool.extend(cids)
        except:
            continue
    return list(set(pool))

def get_molecule_details(cid):
    """Deep-crawls the PUG VIEW for the exact text and filtered names."""
    try:
        # 1. Fetch IUPAC and Synonyms
        p_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON", timeout=5).json()
        s_res = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON", timeout=5).json()
        
        iupac = p_res.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
        syns = s_res.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
        
        # Determine Names
        clean_names = [s for s in syns if not is_bad_name(s, iupac)]
        if not clean_names: return None

        # 2. Fetch Descriptions (PUG VIEW)
        v_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        v_res = requests.get(v_url, timeout=10).json()
        
        found_descriptions = {}
        def crawl(node):
            if isinstance(node, list):
                for i in node: crawl(i)
            elif isinstance(node, dict):
                if 'Information' in node:
                    for info in node['Information']:
                        val_list = info.get('Value', {}).get('StringWithMarkup', [])
                        if not val_list: continue
                        text = val_list[0].get('String')
                        ref = info.get('Reference', '')
                        if text and ref:
                            for source in SOURCES:
                                # Look for the source name in the reference string
                                if source['name'].lower() in ref.lower() or "ncit" in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 150:
                                        if source['label'] not in found_descriptions:
                                            found_descriptions[source['label']] = text
                for key in ['Section', 'Record']:
                    if key in node: crawl(node[key])
        
        crawl(v_res)
        
        # Select description based on priority order
        final_desc = None
        for source in SOURCES:
            if source['label'] in found_descriptions:
                final_desc = found_descriptions[source['label']]
                break
        
        if not final_desc: return None

        return {
            "name": clean_names[0],
            "aka": clean_names[1:4],
            "iupac": iupac,
            "description": final_desc,
            "cid": cid
        }
    except: return None

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
        save_cid(data['cid'])
        print(f"Successfully posted: {data['name']}")
    else:
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    used = get_used_cids()
    pool = get_cid_pool()
    
    if not pool:
        print("Error: Could not build molecule pool. API might be down.")
        sys.exit(1)
        
    random.shuffle(pool)
    print(f"Checking {len(pool)} candidates...")
    
    for cid in pool:
        if str(cid) in used: continue
        data = get_molecule_details(cid)
        if data:
            post_to_telegram(data)
            sys.exit(0)
            
    print("No suitable molecule found in the pool.")
    sys.exit(1)
