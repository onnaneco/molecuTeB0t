import requests
import random
import sys
import os
import re

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = "@moleculesdaily"
HISTORY_FILE = "posted_cids.txt"

# Priority for Description sources
SOURCES_PRIORITY = ["DrugBank", "NCI Thesaurus (NCIt)", "Medical Subject Headings (MeSH)", "CAMEO Chemicals"]

def is_bad_name(name, iupac_name=""):
    """Strictly filters out DB IDs, CAS numbers, and IUPAC-like strings."""
    if not name or len(name) < 3: return True
    if iupac_name and name.lower() == iupac_name.lower(): return True
    
    u = name.upper()
    # Comprehensive blacklist of database prefixes
    bad_prefixes = [
        "SCHEMBL", "ZINC", "AKOS", "NSC", "PUBCHEM", "CSL", "BCP", "STR", "AMBIT", 
        "MCULE", "CHEMBL", "HY-", "ALB-", "SBB-", "BDBM", "GTPL", "STK", "YIL", 
        "KSC", "CAS-", "MFCD", "HMS", "CHEBI", "CID", "PCID", "SMR", "US1", "MLS"
    ]
    if any(u.startswith(p) for p in bad_prefixes): return True
    
    # Filter CAS numbers or numeric codes (e.g. 100-22-1)
    if re.match(r'^[0-9\-]{5,}$', name): return True

    # Filter technical IUPAC names (heuristic: too many numbers/brackets)
    if len(re.findall(r'[0-9\[\(\-,]', name)) > 7: return True
    
    # Filter pure hex/code names (e.g. 'A123B456')
    if len(name) > 8 and not any(v in name.lower() for v in 'aeiouy'): return True

    return False

def get_used_cids():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_cid(cid):
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{str(cid)}\n")

def get_description_fast(cid):
    """
    Uses the fast PUG REST description endpoint to check sources.
    Returns (text, source_label)
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/description/JSON"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200: return None, None
        
        infos = r.json().get("InformationList", {}).get("Information", [])
        found_map = {}
        
        for info in infos:
            text = info.get("Description")
            source = info.get("DescriptionSourceName", "")
            if text and source:
                for target in SOURCES_PRIORITY:
                    # Match "DrugBank" in "DrugBank 5.1.8"
                    target_clean = target.split('(')[0].strip().lower()
                    if target_clean in source.lower():
                        words = text.split()
                        if 10 < len(words) <= 150: # Word limit
                            if target not in found_map:
                                found_map[target] = text
        
        # Return based on strict priority
        for target in SOURCES_PRIORITY:
            if target in found_map:
                return found_map[target], target
    except: pass
    return None, None

def get_molecule_data():
    used = get_used_cids()
    print("Searching for a diverse molecule with a quality description...")
    
    # We search the first 1,000,000 CIDs. This is the "General Knowledge" range.
    # It contains drugs, industrial chemicals, natural products, etc.
    pool = list(range(1, 1000000))
    random.shuffle(pool)
    
    attempts = 0
    # Check up to 500 molecules. With the fast description check, this takes ~2 mins.
    for cid in pool:
        if str(cid) in used: continue
        attempts += 1
        if attempts > 500: break

        # STEP 1: Fast Check for Description Source and Length
        # This is the most restrictive criteria, so we check it first.
        desc, source_label = get_description_fast(cid)
        if not desc: continue

        try:
            # STEP 2: Fetch IUPAC and Synonyms
            p_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON"
            s_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/JSON"
            
            p_data = requests.get(p_url, timeout=5).json()
            s_data = requests.get(s_url, timeout=5).json()
            
            iupac = p_data.get('PropertyTable', {}).get('Properties', [{}])[0].get('IUPACName', "N/A")
            syns = s_data.get('InformationList', {}).get('Information', [{}])[0].get('Synonym', [])

            # STEP 3: Filter Names
            # We must find at least one "Common Name" that isn't a DB ID or IUPAC
            clean_names = [s for s in syns if not is_bad_name(s, iupac)]
            if not clean_names: continue

            return {
                "name": clean_names[0],
                "aka": clean_names[1:4],
                "iupac": iupac,
                "description": desc,
                "cid": cid,
                "image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG"
            }
        except: continue

    print("No molecule found after 500 attempts.")
    sys.exit(1)

def post_to_telegram(data):
    if not TELEGRAM_TOKEN: return
    
    aka_text = ", ".join(data['aka']) if data['aka'] else "None"
    
    # Escape Markdown characters
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
        # Fallback for complex chemical names that break Markdown
        payload["parse_mode"] = ""
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=payload)
        save_cid(data['cid'])

if __name__ == "__main__":
    mol = get_molecule_data()
    post_to_telegram(mol)
