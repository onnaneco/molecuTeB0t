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

# Description priority list
TARGET_SOURCES = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name, iupac):
    """Filters out database IDs, technical codes, and IUPAC names."""
    if not name or len(name) < 3: return True
    if iupac and name.lower() == iupac.lower(): return True
    
    u = name.upper()
    # Database ID prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # Heuristic: If it has too many numbers/brackets/dashes, it's an IUPAC variant
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 8: return True
    
    # If it's a long string of ONLY capital letters and numbers, it's a DB ID
    if u == name and len(name) > 10 and any(c.isdigit() for c in name): return True

    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_description_and_source(cid):
    """Uses the faster PUG REST description endpoint."""
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/description/JSON"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200: return None, None
        
        info_list = r.json().get("InformationList", {}).get("Information", [])
        
        # We store found descriptions to pick by priority later
        found_descriptions = {}

        for info in info_list:
            text = info.get("Description")
            source = info.get("DescriptionSourceName", "")
            if not text or not source: continue
            
            # Check if this source matches our targets
            for target in TARGET_SOURCES:
                # Flexible matching (e.g. "DrugBank" in "DrugBank 6.0")
                if target.split('(')[0].strip().lower() in source.lower():
                    word_count = len(text.split())
                    if 10 < word_count <= 150:
                        if target not in found_descriptions:
                            found_descriptions[target] = text

        # Return based on strict priority
        for target in TARGET_SOURCES:
            if target in found_descriptions:
                return found_descriptions[target], target
                
    except Exception: pass
    return None, None

def get_molecule_data():
    used = get_used_cids()
    
    # To be 100% reliable, we fetch the first 10k CIDs that are in DrugBank.
    # This is a very stable endpoint.
    print("Fetching high-quality molecule pool...")
    try:
        pool_r = requests.get("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/sourceall/DrugBank/cids/JSON", timeout=15)
        pool = pool_r.json().get('IdentifierList', {}).get('CID', [])
    except:
        # Emergency fallback if API fails: Use common CIDs (Aspirin to common drugs)
        pool = list(range(1, 10000))

    random.shuffle(pool)
    
    print(f"Searching through {len(pool)} candidates...")
    
    checked = 0
    for cid in pool:
        if str(cid) in used: continue
        checked += 1
        
        # Stop after 200 checks to prevent workflow timeout, though it usually finds one in 5-10 tries
        if checked > 200: break 
        
        try:
            # 1. Fetch IUPAC and Synonyms in one go
            prop_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            syn_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_data = requests.get(prop_url, timeout=5).json()
            s_data = requests.get(syn_url, timeout=5).json()
            
            iupac = p_data.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName')
            all_syns = s_data.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])
            
            if not iupac or not all_syns: continue

            # 2. Find clean names (Primary and AKA)
            clean_names = [s for s in all_syns if not is_bad_name(s, iupac)]
            if not clean_names: continue
            
            # 3. Get Description (Fast check)
            desc, source_name = get_description_and_source(cid)
            if not desc: continue

            # If we found it, return the package
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

    print("Error: Could not find any valid molecule in this run.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    # Format A.K.A.
    aka_text = ", ".join(data['aka']) if data['aka'] else "N/A"
    
    # Telegram Markdown escaping
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
        print(f"Successfully posted: {data['name']}")
    else:
        print(f"Telegram Error: {r.text}")

if __name__ == "__main__":
    mol_data = get_molecule_data()
    post_to_telegram(mol_data)
