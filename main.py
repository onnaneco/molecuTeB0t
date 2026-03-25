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

# Priority for Description sources
SOURCES_PRIORITY = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name):
    """Filters out database IDs, IUPAC-heavy strings, and numeric codes."""
    if not name or len(name) < 3: return True
    u = name.upper()
    
    # 1. Database Prefix Blacklist
    bad_prefixes = ["SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID ", "PCID"]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # 2. Check if it's just a numeric/code (e.g., 100-22-1)
    if re.match(r'^[0-9\-]{5,}$', name): return True

    # 3. IUPAC Heuristic (too many numbers/brackets/dashes)
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True

    # 4. Filter out names that are ONLY uppercase and numbers (Likely IDs)
    # Real names like "Aspirin" or "Caffeine" have lowercase letters.
    if u == name and any(c.isalpha() for c in name):
        # Allow short common acronyms (e.g. EDTA, ATP), but block long IDs
        if len(name) > 6: return True

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
    """Fetches CIDs from DrugBank source. This is more stable than Annotations."""
    print("Fetching CID pool from DrugBank source...")
    try:
        # DrugBank is the best source for molecules with descriptions
        url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/DrugBank/cids/JSON"
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.json().get('IdentifierList', {}).get('CID', [])
    except:
        pass
    
    print("API Failed. Using emergency fallback list of common molecules...")
    # Emergency fallback: CIDs for very common drugs/chemicals (Aspirin, Caffeine, Ethanol, etc.)
    return [2244, 2519, 702, 176, 5793, 3121, 5816, 135398634, 6047, 5288826, 3345, 1102, 271, 243, 631, 222]

def get_description(cid):
    """Crawl PUG VIEW for description text by priority."""
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
                            for s in SOURCES_PRIORITY:
                                # Look for the source name in the reference
                                if s.split('(')[0].strip().lower() in ref.lower():
                                    words = text.split()
                                    if 10 < len(words) <= 150: # Priority length
                                        if s not in found: found[s] = text
                                    elif 10 < len(words) <= 220: # Secondary length
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
    
    print(f"Pool size: {len(pool)}. Searching for a valid candidate...")
    
    for cid in pool:
        if str(cid) in used: continue
        
        try:
            # 1. Properties
            s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            
            s_data = requests.get(s_url, timeout=5).json()
            p_data = requests.get(p_url, timeout=5).json()
            
            syns = s_data.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            iupac = p_data.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            
            if not iupac or not syns: continue

            # 2. Extract Names (Exclude IUPAC from names)
            clean_names = [s for s in syns if not is_bad_name(s) and s.lower() != iupac.lower()]
            if not clean_names: continue

            # 3. Extract Description
            desc, source = get_description(cid)
            if not desc: continue
            
            # Formatting AKA: up to 3 synonyms
            primary_name = clean_names[0]
            aka_list = clean_names[1:4]

            return {
                "name": primary_name,
                "aka": aka_list,
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "source": source,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except:
            continue
            
    print("Error: No valid molecule found.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    aka_text = ", ".join(data['aka']) if data['aka'] else "N/A"
    
    # Markdown escape
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
        print(f"Posted: {data['name']}")
    else:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
